#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_json_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def request_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body[:500]
        return e.code, parsed


def print_result(name, status, details):
    print(f"\n[{status}] {name}")
    if isinstance(details, str):
        print(details)
    else:
        print(json.dumps(details, ensure_ascii=False, indent=2))


def load_pubg_players_from_registry(path="players.json"):
    registry = load_json_file(path)
    if not registry:
        return []

    players = []
    for player in registry.get("players", []):
        account = player.get("accounts", {}).get("pubg")
        if not account or not account.get("nickname"):
            continue
        players.append(
            {
                "name": player.get("name"),
                "discord_user_id": player.get("discord_user_id"),
                "platform": account.get("platform") or env("PUBG_PLATFORM", "steam"),
                "nickname": account["nickname"],
            }
        )
    return players


def riot_headers(api_key):
    return {"X-Riot-Token": api_key}


def verify_lol():
    api_key = env("RIOT_API_KEY")
    riot_id = env("LOL_RIOT_ID")
    queue = env("LOL_QUEUE_ID", "2400")

    if not api_key or not riot_id or "#" not in riot_id:
        print_result(
            "LoL KR ARAM Mayhem",
            "SKIP",
            "Need RIOT_API_KEY and LOL_RIOT_ID='gameName#tagLine'. Optional LOL_QUEUE_ID defaults to 2400.",
        )
        return

    game_name, tag_line = riot_id.rsplit("#", 1)
    account_url = (
        "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
        f"{urllib.parse.quote(game_name, safe='')}/{urllib.parse.quote(tag_line, safe='')}"
    )
    status, account = request_json(account_url, riot_headers(api_key))
    if status != 200:
        print_result("LoL account lookup", "FAIL", {"status": status, "body": account})
        return

    puuid = account["puuid"]
    ids_url = (
        "https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/"
        f"{urllib.parse.quote(puuid, safe='')}/ids?start=0&count=5&queue={urllib.parse.quote(queue)}"
    )
    status, match_ids = request_json(ids_url, riot_headers(api_key))
    if status != 200:
        print_result("LoL queue matchlist", "FAIL", {"status": status, "body": match_ids})
        return

    if not match_ids:
        print_result(
            "LoL KR ARAM Mayhem",
            "NO_MATCH",
            {"riot_id": riot_id, "queue": queue, "message": "No recent match ids returned for this queue."},
        )
        return

    match_url = f"https://asia.api.riotgames.com/lol/match/v5/matches/{match_ids[0]}"
    status, match = request_json(match_url, riot_headers(api_key))
    if status != 200:
        print_result("LoL match detail", "FAIL", {"status": status, "match_id": match_ids[0], "body": match})
        return

    info = match.get("info", {})
    participants = info.get("participants", [])
    mine = next((p for p in participants if p.get("puuid") == puuid), {})
    print_result(
        "LoL KR ARAM Mayhem",
        "OK",
        {
            "riot_id": riot_id,
            "queue_requested": int(queue),
            "match_id": match_ids[0],
            "queue_returned": info.get("queueId"),
            "game_mode": info.get("gameMode"),
            "game_type": info.get("gameType"),
            "participant_count": len(participants),
            "sample_player_fields": {
                "championName": mine.get("championName"),
                "win": mine.get("win"),
                "kills": mine.get("kills"),
                "deaths": mine.get("deaths"),
                "assists": mine.get("assists"),
                "totalDamageDealtToChampions": mine.get("totalDamageDealtToChampions"),
                "totalDamageTaken": mine.get("totalDamageTaken"),
                "goldEarned": mine.get("goldEarned"),
                "item0": mine.get("item0"),
            },
        },
    )


def pubg_headers(api_key=None):
    headers = {"Accept": "application/vnd.api+json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def verify_pubg():
    api_key = env("PUBG_API_KEY")
    default_platform = env("PUBG_PLATFORM", "steam")
    names = env("PUBG_PLAYER_NAMES")

    if not api_key:
        print_result(
            "PUBG",
            "SKIP",
            "Need PUBG_API_KEY. Optional PUBG_PLATFORM defaults to steam; use kakao for Kakao PUBG.",
        )
        return

    if names:
        pubg_players = [
            {"name": None, "discord_user_id": None, "platform": default_platform, "nickname": n.strip()}
            for n in names.split(",")
            if n.strip()
        ]
    else:
        pubg_players = load_pubg_players_from_registry()

    if not pubg_players:
        print_result(
            "PUBG",
            "SKIP",
            "Need PUBG_PLAYER_NAMES='name1,name2' or players.json with accounts.pubg.nickname.",
        )
        return

    players_by_platform = {}
    for player in pubg_players:
        players_by_platform.setdefault(player["platform"], []).append(player)

    for platform, platform_players in players_by_platform.items():
        verify_pubg_platform(api_key, platform, platform_players)


def verify_pubg_platform(api_key, platform, pubg_players):
    names = [p["nickname"] for p in pubg_players]
    encoded_names = urllib.parse.quote(",".join(names), safe=",")
    players_url = f"https://api.pubg.com/shards/{platform}/players?filter[playerNames]={encoded_names}"
    status, players = request_json(players_url, pubg_headers(api_key))
    if status != 200:
        print_result("PUBG player lookup", "FAIL", {"status": status, "body": players})
        return

    data = players.get("data", [])
    if not data:
        print_result("PUBG", "NO_PLAYER", {"platform": platform, "names": names})
        return

    player = data[0]
    matches = player.get("relationships", {}).get("matches", {}).get("data", [])
    if not matches:
        print_result("PUBG", "NO_MATCH", {"player": player.get("attributes", {}).get("name")})
        return

    match_id = matches[0]["id"]
    match_url = f"https://api.pubg.com/shards/{platform}/matches/{match_id}"
    status, match = request_json(match_url, pubg_headers())
    if status != 200:
        print_result("PUBG match detail", "FAIL", {"status": status, "match_id": match_id, "body": match})
        return

    included = match.get("included", [])
    participants = [x for x in included if x.get("type") == "participant"]
    assets = [x for x in included if x.get("type") == "asset"]
    sample = participants[0].get("attributes", {}).get("stats", {}) if participants else {}
    print_result(
        "PUBG",
        "OK",
        {
            "platform": platform,
            "first_player": player.get("attributes", {}).get("name"),
            "registered_players": [
                {"name": p["name"], "nickname": p["nickname"], "discord_user_id": p["discord_user_id"]}
                for p in pubg_players
            ],
            "match_id": match_id,
            "participant_count": len(participants),
            "has_telemetry_url": bool(assets and assets[0].get("attributes", {}).get("URL")),
            "sample_stats_fields": {
                "name": sample.get("name"),
                "kills": sample.get("kills"),
                "damageDealt": sample.get("damageDealt"),
                "DBNOs": sample.get("DBNOs"),
                "timeSurvived": sample.get("timeSurvived"),
                "winPlace": sample.get("winPlace"),
            },
        },
    )


def nexon_headers(api_key):
    return {"x-nxopen-api-key": api_key}


def verify_fconline():
    api_key = env("NEXON_API_KEY") or env("NXOPEN_API_KEY")
    nickname = env("FC_NICKNAME")
    matchtype = env("FC_MATCHTYPE", "234")

    if not api_key or not nickname:
        print_result(
            "FC Online",
            "SKIP",
            "Need NEXON_API_KEY or NXOPEN_API_KEY, and FC_NICKNAME. Optional FC_MATCHTYPE defaults to 234 (볼타 커스텀).",
        )
        return

    id_url = "https://open.api.nexon.com/fconline/v1/id?nickname=" + urllib.parse.quote(nickname)
    status, account = request_json(id_url, nexon_headers(api_key))
    if status != 200:
        print_result("FC Online account lookup", "FAIL", {"status": status, "body": account})
        return

    ouid = account.get("ouid")
    if not ouid:
        print_result("FC Online account lookup", "FAIL", {"status": status, "body": account})
        return

    match_url = (
        "https://open.api.nexon.com/fconline/v1/user/match?"
        f"ouid={urllib.parse.quote(ouid, safe='')}&matchtype={urllib.parse.quote(matchtype)}&offset=0&limit=5"
    )
    status, match_ids = request_json(match_url, nexon_headers(api_key))
    if status != 200:
        print_result("FC Online matchlist", "FAIL", {"status": status, "body": match_ids})
        return

    if not match_ids:
        print_result(
            "FC Online",
            "NO_MATCH",
            {"nickname": nickname, "matchtype": int(matchtype), "message": "No recent match ids for this matchtype."},
        )
        return

    detail_url = "https://open.api.nexon.com/fconline/v1/match-detail?matchid=" + urllib.parse.quote(match_ids[0])
    status, detail = request_json(detail_url, nexon_headers(api_key))
    if status != 200:
        print_result("FC Online match detail", "FAIL", {"status": status, "match_id": match_ids[0], "body": detail})
        return

    print_result(
        "FC Online",
        "OK",
        {
            "nickname": nickname,
            "matchtype_requested": int(matchtype),
            "match_id": match_ids[0],
            "matchtype_returned": detail.get("matchType"),
            "match_date": detail.get("matchDate"),
            "players": [
                {
                    "nickname": p.get("nickname"),
                    "matchResult": p.get("matchDetail", {}).get("matchResult"),
                    "shoot": p.get("shoot", {}).get("shootTotal"),
                    "effectiveShoot": p.get("shoot", {}).get("effectiveShootTotal"),
                    "possession": p.get("matchDetail", {}).get("possession"),
                    "averageRating": p.get("matchDetail", {}).get("averageRating"),
                }
                for p in detail.get("matchInfo", [])[:4]
            ],
        },
    )


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Verify whether target game APIs expose recent matches.")
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=("all", "lol", "pubg", "fc"),
        help="API to verify. Defaults to all.",
    )
    args = parser.parse_args()

    targets = {
        "lol": verify_lol,
        "pubg": verify_pubg,
        "fc": verify_fconline,
    }
    selected = targets.keys() if args.target == "all" else [args.target]
    for target in selected:
        try:
            targets[target]()
        except Exception as exc:
            print_result(target, "ERROR", f"{type(exc).__name__}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
