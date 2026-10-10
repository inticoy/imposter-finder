"""롤 친구 경기 수집과 리포트.

친구 계정마다 최근 경기 ID를 받아, 두 계정 이상에 같은 경기가 있으면 친구끼리 한 판으로 본다.
모드는 가리지 않는다 (자유랭크·아레나 등, 증강 칼바람은 Riot API에 잡히지 않음).
"""
from __future__ import annotations

import time

from imposter_finder.analysis.lol_report import Friend, build_report
from imposter_finder.config import Settings
from imposter_finder.discord import DiscordClient
from imposter_finder.games.lol import DDragon, RiotApiError, RiotClient
from imposter_finder.registry import load_lol_accounts
from imposter_finder.storage import LocalStore

PLATFORM = "lol"  # match_notifications 구분자
RECENT_COUNT = 10  # 15분 사이에 10판 넘게 하지 않는다
BACKFILL_COUNT = 30
MAX_AGE_S = 24 * 3600

_ddragon = DDragon()


def run_lol_cycle(settings: Settings, store: LocalStore, notify: bool) -> dict[str, int]:
    accounts = load_lol_accounts(settings.players_path)
    client = RiotClient(settings.riot_api_key or "")
    friends: dict[str, Friend] = {}
    seen_by: dict[str, set[str]] = {}
    for account in accounts:
        try:
            puuid = store.lol_puuid(account.riot_id)
            if not puuid:
                puuid = client.puuid(account.riot_id)
                store.set_lol_puuid(account.riot_id, puuid)
            friends[puuid] = Friend(account.name, account.discord_user_id)
            for match_id in client.match_ids(puuid, RECENT_COUNT if notify else BACKFILL_COUNT):
                seen_by.setdefault(match_id, set()).add(account.name)
        except RiotApiError as exc:
            print(f"[lol] skip {account.riot_id}: {exc}")  # 닉네임 변경 등은 그 계정만 건너뛴다

    # 같은 사람의 부계정끼리 겹친 경기는 친구전이 아니라 사람 기준 두 명 이상만 본다
    group_ids = [match_id for match_id, names in seen_by.items() if len(names) >= 2]
    published = 0
    discord = DiscordClient(settings.discord_bot_token or "")
    matches = []
    for match_id in group_ids:
        match = store.lol_match(match_id)
        if match is None:
            match = client.match(match_id)
            store.upsert_lol_match(match)
        matches.append(match)

    for match in sorted(matches, key=lambda m: m["info"]["gameCreation"]):
        match_id = match["metadata"]["matchId"]
        if store.notification_done(PLATFORM, match_id):
            continue
        if not notify:
            store.mark_notification_done(PLATFORM, match_id, "bootstrap")
            continue
        ended = (match["info"].get("gameEndTimestamp") or match["info"]["gameCreation"]) / 1000
        if time.time() - ended > MAX_AGE_S:
            store.mark_notification_done(PLATFORM, match_id, "too_old")
            continue
        timeline = None
        if match["info"]["queueId"] not in (1700, 1750):  # 아레나는 골드 그래프를 쓰지 않는다
            try:
                timeline = client.timeline(match_id)
            except RiotApiError as exc:
                print(f"[lol] timeline failed {match_id}: {exc}")
        payload, files = build_report(match, timeline, friends, _ddragon, settings.gemini_api_key, settings.gemini_model)
        for thread_id in settings.discord_lol_thread_ids:
            discord.send_with_files(thread_id, payload, files)
        store.mark_notification_done(PLATFORM, match_id, "sent")
        published += 1
    return {"group_matches": len(group_ids), "published": published}
