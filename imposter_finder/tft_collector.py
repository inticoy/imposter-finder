"""TFT 친구 로비 수집과 리포트.

롤 계정(players.json의 riot_ids)으로 TFT 경기 ID를 받아, 두 사람 이상의 목록에 같은 경기가 있으면 친구 로비로 본다.
Riot 키는 게임마다 따로라 puuid도 롤과 따로 저장한다. 랭크 판은 지난 수집 때 저장한 LP와 비교해 변동을 보여 준다.
"""
from __future__ import annotations

import time

from imposter_finder.analysis.tft_report import RANKED_QUEUES, Friend, build_report
from imposter_finder.config import Settings
from imposter_finder.discord import DiscordClient
from imposter_finder.games.lol import RiotApiError, RiotClient
from imposter_finder.registry import load_lol_accounts
from imposter_finder.storage import LocalStore

PLATFORM = "tft"  # match_notifications 구분자
RECENT_COUNT = 10
BACKFILL_COUNT = 20
MAX_AGE_S = 24 * 3600
RANK_FRESH_S = 3600  # 지난 랭크 값이 이보다 오래되면 여러 판이 섞이니 LP 변동을 보여 주지 않는다


def run_tft_cycle(settings: Settings, store: LocalStore, notify: bool) -> dict[str, int]:
    accounts = load_lol_accounts(settings.players_path)
    client = RiotClient(settings.riot_tft_api_key or "")
    friends: dict[str, Friend] = {}
    seen_by: dict[str, set[str]] = {}
    for account in accounts:
        try:
            puuid = store.tft_puuid(account.riot_id)
            if not puuid:
                puuid = client.puuid(account.riot_id)
                store.set_tft_puuid(account.riot_id, puuid)
            friends[puuid] = Friend(account.name, account.discord_user_id)
            for match_id in client.tft_match_ids(puuid, RECENT_COUNT if notify else BACKFILL_COUNT):
                seen_by.setdefault(match_id, set()).add(account.name)
        except RiotApiError as exc:
            print(f"[tft] skip {account.riot_id}: {exc}")  # 닉네임 변경 등은 그 계정만 건너뛴다

    # 같은 사람의 부계정끼리 겹친 경기는 친구 로비가 아니라 사람 기준 두 명 이상만
    group_ids = [match_id for match_id, names in seen_by.items() if len(names) >= 2]
    matches = []
    for match_id in group_ids:
        match = store.tft_match(match_id)
        if match is None:
            match = client.tft_match(match_id)
            store.upsert_tft_match(match)
        matches.append(match)

    # 랭크: 지금 값을 받아, 지난 수집 때 값과 비교한 LP 변동을 붙인다. 수집이 끝나면 지금 값을 저장
    ranks: dict[str, dict] = {}
    for puuid in friends:
        try:
            if rank := client.tft_rank(puuid):
                ranks[puuid] = rank | {"fetched_at": time.time()}
        except RiotApiError as exc:
            print(f"[tft] rank failed: {exc}")
    tiers = {}
    for puuid, rank in ranks.items():
        before = store.tft_rank(puuid)
        same = (before and time.time() - before.get("fetched_at", 0) < RANK_FRESH_S
                and (before["tier"], before["rank"]) == (rank["tier"], rank["rank"]))
        tiers[puuid] = rank | {"delta": rank["leaguePoints"] - before["leaguePoints"] if same else 0}

    published = 0
    discord = DiscordClient(settings.discord_bot_token or "")
    for match in sorted(matches, key=lambda m: m["info"].get("game_datetime", 0)):
        match_id = match["metadata"]["match_id"]
        if store.notification_done(PLATFORM, match_id):
            continue
        if not notify:
            store.mark_notification_done(PLATFORM, match_id, "bootstrap")
            continue
        if time.time() - match["info"].get("game_datetime", 0) / 1000 > MAX_AGE_S:
            store.mark_notification_done(PLATFORM, match_id, "too_old")
            continue
        in_match = {p["puuid"]: friends[p["puuid"]] for p in match["info"]["participants"] if p["puuid"] in friends}
        ranked = match["info"].get("queue_id") in RANKED_QUEUES
        payload, files = build_report(match, in_match, tiers if ranked else {}, settings.gemini_api_key, settings.gemini_model)
        for thread_id in settings.discord_tft_thread_ids:
            discord.send_with_files(thread_id, payload, files)
        store.mark_notification_done(PLATFORM, match_id, "sent")
        published += 1
    for puuid, rank in ranks.items():
        store.set_tft_rank(puuid, rank)
    return {"group_matches": len(group_ids), "published": published}
