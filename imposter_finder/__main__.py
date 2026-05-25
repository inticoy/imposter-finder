from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from imposter_finder.analysis.pubg import analyze_pubg_match, pubg_match_label
from imposter_finder.config import load_settings
from imposter_finder.discord import DiscordClient
from imposter_finder.games.pubg import PubgClient
from imposter_finder.registry import PubgPlayer, load_pubg_players
from imposter_finder.state import JsonState


def main() -> int:
    parser = argparse.ArgumentParser(description="Imposter Finder MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pubg = subparsers.add_parser("pubg", help="Poll and analyze recent PUBG matches")
    pubg.add_argument("--include-seen", action="store_true", help="Analyze matches even if they already exist in state")
    pubg.add_argument("--max-matches", type=int, default=1, help="Maximum new matches to analyze")
    pubg.add_argument("--send-discord", action="store_true", help="Send generated reports to the configured Discord channel")
    pubg.add_argument("--no-save-state", action="store_true", help="Do not write data/seen_matches.json")
    pubg.add_argument("--list-matches", action="store_true", help="List candidate matches and exit")
    pubg.add_argument("--match-index", type=int, help="Analyze one candidate by zero-based index after sorting")
    pubg.add_argument("--match-id", help="Analyze a specific PUBG match id")
    pubg.add_argument("--ignore-age-limit", action="store_true", help="Include matches older than PUBG_MAX_MATCH_AGE_HOURS")

    args = parser.parse_args()
    if args.command == "pubg":
        return run_pubg(
            include_seen=args.include_seen,
            max_matches=args.max_matches,
            send_discord=args.send_discord,
            save_state=not args.no_save_state,
            list_matches=args.list_matches,
            match_index=args.match_index,
            match_id=args.match_id,
            ignore_age_limit=args.ignore_age_limit,
        )
    return 1


def run_pubg(
    include_seen: bool,
    max_matches: int,
    send_discord: bool,
    save_state: bool,
    list_matches: bool,
    match_index: int | None,
    match_id: str | None,
    ignore_age_limit: bool,
) -> int:
    settings = load_settings()
    players = load_pubg_players(settings.players_path)
    state = JsonState(settings.seen_matches_path)

    client = PubgClient(settings.pubg_api_key)
    discord = None
    if send_discord:
        if not settings.discord_bot_token or not (settings.discord_thread_id or settings.discord_channel_id):
            raise RuntimeError(
                "DISCORD_BOT_TOKEN and the selected DISCORD_THREAD_* or DISCORD_CHANNEL_* are required with --send-discord"
            )
        discord = DiscordClient(settings.discord_bot_token)

    matches = discover_pubg_matches(
        client,
        state,
        players,
        include_seen=include_seen,
        max_match_age_hours=None if ignore_age_limit else settings.pubg_max_match_age_hours,
    )
    candidates = [m for m in matches if include_seen or not state.seen_match("pubg", m["platform"], m["match_id"])]
    candidates = sorted(candidates, key=lambda item: item.get("created_at") or "", reverse=True)

    if list_matches:
        print_candidate_matches(candidates)
        return 0

    if match_id:
        candidates = [item for item in candidates if item["match_id"] == match_id]
    elif match_index is not None:
        if match_index < 0 or match_index >= len(candidates):
            print_candidate_matches(candidates)
            raise RuntimeError(f"--match-index {match_index} is out of range")
        candidates = [candidates[match_index]]

    if not candidates:
        print("새 PUBG 매치가 없습니다. --include-seen을 붙이면 최근 매치를 다시 분석할 수 있습니다.")
        return 0

    analyzed = 0
    for candidate in candidates[:max_matches]:
        platform = candidate["platform"]
        match_id = candidate["match_id"]
        match = candidate["match"]
        telemetry_url = find_telemetry_url(match)
        telemetry = client.get_telemetry(telemetry_url) if telemetry_url else []
        report = analyze_pubg_match(platform, match, telemetry, players)
        if save_state:
            state.mark_match_seen("pubg", platform, match_id, candidate.get("created_at"), report)
        print(report["message"])
        if discord:
            if settings.discord_thread_id:
                discord.send_message(settings.discord_thread_id, report)
            else:
                discord.send_report(
                    settings.discord_channel_id,
                    report,
                    report.get("thread_name") or f"🕵🏻‍♂️ PUBG 범인찾기 🔎 #{match_id[:8]}",
                )
        analyzed += 1

    if save_state:
        state.save()
    print(f"\n분석 완료: {analyzed}개")
    return 0


def print_candidate_matches(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        print("분석 가능한 PUBG 매치 후보가 없습니다.")
        return
    for index, candidate in enumerate(candidates):
        match = candidate["match"]
        attrs = match.get("data", {}).get("attributes", {})
        registered_count = candidate.get("registered_count")
        print(
            f"[{index}] #{candidate['match_id'][:8]} | "
            f"{pubg_match_label(match)} | "
            f"registered={registered_count}"
        )


def discover_pubg_matches(
    client: PubgClient,
    state: JsonState,
    players: list[PubgPlayer],
    include_seen: bool,
    max_match_age_hours: float | None,
) -> list[dict[str, Any]]:
    players_by_platform: dict[str, list[PubgPlayer]] = defaultdict(list)
    for player in players:
        players_by_platform[player.platform].append(player)

    discovered: dict[tuple[str, str], dict[str, Any]] = {}
    for platform, platform_players in players_by_platform.items():
        names = [player.nickname for player in platform_players]
        found = client.lookup_players(platform, names)
        for player in platform_players:
            payload = found.get(player.nickname.lower())
            if not payload:
                print(f"WARN: PUBG player not found: {player.nickname} ({player.name}, {platform})")
                continue

            for match_ref in payload.get("relationships", {}).get("matches", {}).get("data", [])[:5]:
                match_id = match_ref["id"]
                key = (platform, match_id)
                if key in discovered:
                    continue
                discovered[key] = None
                if state.seen_match("pubg", platform, match_id):
                    if include_seen:
                        match = client.get_match(platform, match_id)
                        if is_match_too_old(match, max_match_age_hours):
                            print(f"SKIP: match older than {max_match_age_hours:g}h for match {match_id}")
                            continue
                        registered_count = count_registered_pubg_players(match, platform_players)
                        if registered_count < 2:
                            print(f"SKIP: registered PUBG players < 2 for match {match_id} ({registered_count})")
                            continue
                        discovered[key] = {
                            "platform": platform,
                            "match_id": match_id,
                            "match": match,
                            "created_at": match.get("data", {}).get("attributes", {}).get("createdAt"),
                            "registered_count": registered_count,
                            "seen": True,
                        }
                    continue
                match = client.get_match(platform, match_id)
                if is_match_too_old(match, max_match_age_hours):
                    print(f"SKIP: match older than {max_match_age_hours:g}h for match {match_id}")
                    continue
                registered_count = count_registered_pubg_players(match, platform_players)
                if registered_count < 2:
                    print(f"SKIP: registered PUBG players < 2 for match {match_id} ({registered_count})")
                    continue
                discovered[key] = {
                    "platform": platform,
                    "match_id": match_id,
                    "match": match,
                    "created_at": match.get("data", {}).get("attributes", {}).get("createdAt"),
                    "registered_count": registered_count,
                    "seen": False,
                }

    return [item for item in discovered.values() if item is not None]


def is_match_too_old(match: dict[str, Any], max_match_age_hours: float | None) -> bool:
    if max_match_age_hours is None:
        return False
    created_at = match.get("data", {}).get("attributes", {}).get("createdAt")
    if not created_at:
        return False
    try:
        started = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - started > timedelta(hours=max_match_age_hours)


def count_registered_pubg_players(match: dict[str, Any], players: list[PubgPlayer]) -> int:
    registered_names = {player.nickname.lower() for player in players}
    participant_names = set()
    for item in match.get("included", []):
        if item.get("type") != "participant":
            continue
        name = item.get("attributes", {}).get("stats", {}).get("name")
        if name:
            participant_names.add(name.lower())
    return len(registered_names & participant_names)


def find_telemetry_url(match: dict[str, Any]) -> str | None:
    for item in match.get("included", []):
        if item.get("type") == "asset":
            url = item.get("attributes", {}).get("URL")
            if url:
                return url
    return None


if __name__ == "__main__":
    raise SystemExit(main())
