"""FC 온라인 친구전 수집과 리포트.

친구들의 '클래식 1on1' 경기 목록을 받아, 두 명 이상의 목록에 같은 경기 ID가 있으면
친구끼리 한 경기로 본다. 경기 상세를 받아 DB에 쌓고, 새 경기는 FC thread에 리포트한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from imposter_finder.analysis.fc_highlight import build_highlight
from imposter_finder.analysis.fc_league import LEAGUE_EVERY, build_league_report
from imposter_finder.analysis.fc_report import Friend, build_forfeit_report, build_report, is_forfeit
from imposter_finder.config import ROOT_DIR, Settings
from imposter_finder.discord import DiscordClient
from imposter_finder.games.fconline import FcMeta, FcOnlineClient, FcPrices, FcTeamColors, NexonApiError, NexonQuotaExceeded
from imposter_finder.registry import load_fc_players
from imposter_finder.storage import LocalStore
from imposter_finder import monitoring

PLATFORM = "fconline"  # match_notifications 구분자
LEAGUE = "fconline_league"  # 리그 리포트가 어디까지 다뤘나 (match_id = 마지막으로 포함한 경기)
RECENT_LIMIT = 20  # 평소에는 최근 20경기만 본다 (15분 사이에 20경기 넘게 하지 않는다)
BACKFILL_LIMIT = 100  # 첫 실행에는 넥슨이 보여주는 만큼 (약 30일치) 다 받는다
# 넥슨 반영이 2~3시간쯤 늦어서 넉넉히 둔다
MAX_AGE = timedelta(hours=24)

_meta: FcMeta | None = None
_colors: FcTeamColors | None = None
_prices: FcPrices | None = None


def run_fc_cycle(settings: Settings, store: LocalStore, notify: bool) -> dict[str, int]:
    global _meta, _colors, _prices
    _meta = _meta or FcMeta(ROOT_DIR / "data" / "fc_meta")
    _colors = _colors or FcTeamColors(ROOT_DIR / "data" / "fc_meta")
    _prices = _prices or FcPrices(ROOT_DIR / "data" / "fc_meta", _meta)
    players = load_fc_players(settings.players_path)
    if not players:
        monitoring.observe("fc.accounts", "degraded", "no-accounts", "FC 등록 계정 없음", "PLAYERS_JSON 확인")
    client = FcOnlineClient(settings.nexon_api_key or "", lambda day: store.add_api_call("nexon", day),
                            settings.nexon_daily_limit)

    friends: dict[str, Friend] = {}
    seen_by: dict[str, set[str]] = {}
    limit = RECENT_LIMIT if notify else BACKFILL_LIMIT
    for player in players:
        try:
            ouid = store.fc_ouid(player.nickname)
            if not ouid:
                ouid = client.ouid(player.nickname)
                store.set_fc_ouid(player.nickname, ouid)
            friends[ouid] = Friend(player.name, player.discord_user_id)
            for match_id in client.match_ids(ouid, limit=limit):
                seen_by.setdefault(match_id, set()).add(ouid)
            monitoring.observe("fc.accounts")
        except NexonQuotaExceeded:
            raise
        except NexonApiError as exc:
            monitoring.error("fc.accounts", exc, "일부 FC 계정 조회 실패", "Nexon 키 또는 등록 계정 확인")
            # 닉네임 변경 등으로 한 명이 실패해도 나머지는 계속한다
            print(f"[fc] skip {player.name}: {exc}")

    friend_matches = [match_id for match_id, ouids in seen_by.items() if len(ouids) >= 2]
    matches: list[dict[str, Any]] = []
    for match_id in friend_matches:
        match = store.fc_match(match_id)
        if match is None:
            match = client.match_detail(match_id)
            store.upsert_fc_match(match)
        matches.append(match)

    published = 0
    discord = DiscordClient(settings.discord_bot_token or "")
    for match in sorted(matches, key=lambda m: m["matchDate"]):  # 오래된 경기부터 순서대로
        match_id = match["matchId"]
        if store.notification_done(PLATFORM, match_id):
            continue
        if not notify:
            store.mark_notification_done(PLATFORM, match_id, "bootstrap")
            continue
        if _too_old(match):
            store.mark_notification_done(PLATFORM, match_id, "too_old")
            continue
        sides = [side["ouid"] for side in match["matchInfo"]]
        if len(sides) != 2 or any(ouid not in friends for ouid in sides):
            store.mark_notification_done(PLATFORM, match_id, "not_a_friend_match")
            continue

        head_to_head = store.fc_head_to_head(sides[0], sides[1], limit=10)
        if is_forfeit(match):
            payload = build_forfeit_report(match, head_to_head, friends)
            for thread_id in settings.discord_fc_thread_ids:
                discord.send_message(thread_id, payload)
        else:
            payload, files = build_report(match, head_to_head, friends, _meta, _colors, settings.gemini_api_key,
                                          settings.gemini_model, _prices)
            for thread_id in settings.discord_fc_thread_ids:
                discord.send_with_files(thread_id, payload, files)
            _send_highlight(settings, discord, match, friends)
        store.mark_notification_done(PLATFORM, match_id, "sent")
        published += 1
    league = _publish_league(settings, store, discord, friends) if notify else 0
    return {"friend_matches": len(friend_matches), "published": published, "league": league}


def _send_highlight(settings: Settings, discord: DiscordClient, match: dict[str, Any], friends: dict[str, Friend]) -> None:
    """리포트 바로 다음 메시지로 베스트 골 3D 리플레이. 리포트 파일들과 합치면 디스코드 한도(10MiB)를 넘을 수 있어 따로 보낸다.
    실패해도 리포트는 이미 갔으니 기록만 남긴다."""
    highlight = build_highlight(match, friends, _meta, _colors)
    if highlight is None:
        return
    payload, files = highlight
    for thread_id in settings.discord_fc_thread_ids:
        try:
            discord.send_with_files(thread_id, payload, files)
        except Exception as exc:
            monitoring.error("fc.highlight", exc, "FC 베스트 골 전송 실패 — 리포트는 그대로")
            print(f"[fc] highlight send failed: {exc}")


def _publish_league(settings: Settings, store: LocalStore, discord: DiscordClient, friends: dict[str, Friend]) -> int:
    """리포트를 보낸 친구전이 LEAGUE_EVERY개 쌓이면 리그 리포트. 처음엔 지금부터 센다."""
    last = store.last_notification(LEAGUE)
    after = (store.fc_match(last) or {}).get("matchDate") if last else None
    window = store.fc_sent_after(PLATFORM, after)
    if not last:
        if window:
            store.mark_notification_done(LEAGUE, window[-1]["matchId"], "bootstrap")
        return 0
    if len(window) < LEAGUE_EVERY:
        return 0
    try:
        history = [m for m in store.fc_matches() if m["matchDate"] <= window[-1]["matchDate"]]
        payload, files = build_league_report(history, window, friends)
        for thread_id in settings.discord_fc_thread_ids:
            discord.send_with_files(thread_id, payload, files)
        monitoring.observe("fc.league")
    except Exception as exc:
        monitoring.error("fc.league", exc, "FC 리그 리포트 실패 — 다음 실행에서 다시 시도")
        print(f"[fc] league report failed: {exc}")
        return 0
    store.mark_notification_done(LEAGUE, window[-1]["matchId"], "sent")
    return 1


def _too_old(match: dict[str, Any]) -> bool:
    try:
        # matchDate는 UTC다 (랭커들의 최신 경기 시각이 한국 시간보다 9시간 앞서 멈춰 있는 것으로 확인)
        played = datetime.fromisoformat(match["matchDate"]).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    return datetime.now(timezone.utc) - played > MAX_AGE
