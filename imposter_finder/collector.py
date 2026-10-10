from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from imposter_finder.analysis.pubg import analyze_pubg_match
from imposter_finder.analysis.pubg_report import build_report
from imposter_finder.config import Settings
from imposter_finder.discord import DiscordClient
from imposter_finder.games.pubg import PubgClient
from imposter_finder.registry import PubgPlayer, load_pubg_players
from imposter_finder.storage import LocalStore


def refresh_player_history(client: PubgClient, store: LocalStore, player: PubgPlayer, limit: int = 30) -> int:
    """Fetch the currently exposed history for one registered player into SQLite."""
    match_ids = client.get_recent_match_ids(player.platform, player.nickname, limit)
    for match_id in match_ids:
        match = client.get_match(player.platform, match_id)
        store.upsert_match(player.platform, match)
        store.link_player_match(player.platform, player.nickname, match_id)
    return len(match_ids)


def run_collection_cycle(settings: Settings, store: LocalStore, notify: bool) -> dict[str, int]:
    """Collect all registered players, then optionally publish unprocessed team matches."""
    players = load_pubg_players(settings.players_path)
    client = PubgClient(settings.pubg_api_key)
    fetched: dict[tuple[str, str], dict[str, Any]] = {}
    for player in players:
        match_ids = client.get_recent_match_ids(player.platform, player.nickname, 30)
        for match_id in match_ids:
            key = (player.platform, match_id)
            match = fetched.get(key)
            if match is None:
                match = client.get_match(player.platform, match_id)
                fetched[key] = match
                store.upsert_match(player.platform, match)
            store.link_player_match(player.platform, player.nickname, match_id)

    published = 0
    for (platform, match_id), match in fetched.items():
        if store.notification_done(platform, match_id):
            continue
        if _match_too_old(match, settings.pubg_max_match_age_hours):
            store.mark_notification_done(platform, match_id, "too_old")
            continue
        registered = _registered_in_match(platform, match, players)
        if len(registered) < 2:
            store.mark_notification_done(platform, match_id, "not_a_group_match")
            continue
        if not notify:
            store.mark_notification_done(platform, match_id, "bootstrap")
            continue

        telemetry_url = _telemetry_url(match)
        telemetry = client.get_telemetry(telemetry_url) if telemetry_url else []
        if settings.discord_pubg_thread_ids:
            _send_image_report(settings, platform, match, telemetry, registered)
        else:
            _send_report(settings, analyze_pubg_match(platform, match, telemetry, registered), match_id)
        store.mark_notification_done(platform, match_id, "sent")
        published += 1
    return {"matches": len(fetched), "published": published}


def _send_report(settings: Settings, report: dict[str, Any], match_id: str) -> None:
    if not settings.discord_bot_token or not (settings.discord_thread_id or settings.discord_channel_id):
        raise RuntimeError("Discord bot token and target channel/thread are required for local collection")
    discord = DiscordClient(settings.discord_bot_token)
    if settings.discord_thread_id:
        discord.send_message(settings.discord_thread_id, report)
    else:
        discord.send_report(settings.discord_channel_id or "", report, report.get("thread_name") or f"🕵🏻‍♂️ PUBG 범인찾기 #{match_id[:8]}")


def _send_image_report(settings: Settings, platform: str, match: dict[str, Any], telemetry: list[dict[str, Any]],
                       registered: list[PubgPlayer]) -> None:
    """경기 분석 이미지(결과·스쿼드·경로·교전 흐름·무기) + 총평/평가."""
    payload, files = build_report(platform, match, telemetry, registered, settings.gemini_api_key, settings.gemini_model)
    discord = DiscordClient(settings.discord_bot_token or "")
    for thread_id in settings.discord_pubg_thread_ids:
        discord.send_with_files(thread_id, payload, files)


def _registered_in_match(platform: str, match: dict[str, Any], players: list[PubgPlayer]) -> list[PubgPlayer]:
    names = {
        str(item.get("attributes", {}).get("stats", {}).get("name") or "").casefold()
        for item in match.get("included", [])
        if item.get("type") == "participant"
    }
    return [player for player in players if player.platform == platform and player.nickname.casefold() in names]


def _telemetry_url(match: dict[str, Any]) -> str | None:
    for item in match.get("included", []):
        if item.get("type") == "asset":
            url = item.get("attributes", {}).get("URL")
            if url:
                return str(url)
    return None


def _match_too_old(match: dict[str, Any], hours: float) -> bool:
    created_at = match.get("data", {}).get("attributes", {}).get("createdAt")
    if not created_at:
        return False
    try:
        started = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - started > timedelta(hours=hours)
