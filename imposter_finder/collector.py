from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from imposter_finder.analysis.pubg_report import build_highlight, build_report
from imposter_finder.config import Settings
from imposter_finder.discord import DiscordClient, with_highlight
from imposter_finder.games.pubg import PubgClient
from imposter_finder.registry import PubgPlayer, load_pubg_players
from imposter_finder.storage import LocalStore
from imposter_finder import monitoring


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
    if not players:
        monitoring.observe("pubg.accounts", "degraded", "no-accounts", "배그 등록 계정 없음", "PLAYERS_JSON 확인")
    client = PubgClient(settings.pubg_api_key)
    fetched: dict[tuple[str, str], dict[str, Any]] = {}
    for player in players:
        match_ids = client.get_recent_match_ids(player.platform, player.nickname, 30)
        monitoring.observe("pubg.accounts")
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
        _send_image_report(settings, platform, match, telemetry, registered)
        store.mark_notification_done(platform, match_id, "sent")
        published += 1
    if not notify:
        store.mark_bootstrap_complete("pubg")
    return {"matches": len(fetched), "published": published}


def _send_image_report(settings: Settings, platform: str, match: dict[str, Any], telemetry: list[dict[str, Any]],
                       registered: list[PubgPlayer]) -> None:
    """경기 분석 이미지(결과·스쿼드·경로·교전 흐름·무기) + 총평/평가."""
    payload, files = build_report(platform, match, telemetry, registered, settings.gemini_api_key, settings.gemini_model)
    payload, files = with_highlight(payload, files, _highlight(platform, match, telemetry, registered))
    discord = DiscordClient(settings.discord_bot_token or "")
    for thread_id in settings.discord_pubg_thread_ids:
        discord.send_with_files(thread_id, payload, files, report_id=f"pubg:{platform}:{match['data']['id']}")


def _highlight(platform: str, match: dict[str, Any], telemetry: list[dict[str, Any]],
               registered: list[PubgPlayer]) -> tuple[dict, list[tuple]] | None:
    """베스트 킬 3D GIF (리포트의 마지막 이미지). 실패하면 빼고 리포트만 보낸다."""
    try:
        return build_highlight(platform, match, telemetry, registered)
    except Exception as exc:
        monitoring.error("pubg.highlight", exc, "베스트 킬 하이라이트 실패 — 리포트는 하이라이트 없이 전송", "numpy 설치·지도 원본 다운로드 확인")
        print(f"[pubg] highlight failed: {exc}")
        return None


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
