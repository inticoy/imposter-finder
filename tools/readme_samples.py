"""README 예시 이미지: 실제 경기로 리포트를 만들되 이름·닉네임은 가명으로.

렌더링 전에 경기 데이터(매치·텔레메트리·타임라인·맞대결) 안의 닉네임과 players.json의 이름을
가명으로 바꾸므로, 이미지 글자와 Gemini 판독 문장 모두 가명으로 나온다.

    PYTHONPATH=. .venv/bin/python tools/readme_samples.py --pubg <id> --lol <id> --tft <id> --fc <id>   (일부만 줘도 됨)

결과: docs/images/report-{pubg,lol,fc}.(gif|png) — 디스코드에 올라온 모습 그대로 한 장 (tools/discord_mock.py).
리포트 원본은 data/samples/에 두고, --compose-only면 API 호출 없이 그 원본으로 다시 합성만 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import sqlite3
from pathlib import Path
from typing import Any

from imposter_finder.analysis import fc_report, lol_report, pubg_report, tft_report
from imposter_finder.config import ROOT_DIR, load_settings
from imposter_finder.games.fconline import FcMeta, FcPrices, FcTeamColors
from imposter_finder.games.lol import RiotApiError, RiotClient
from imposter_finder.games.pubg import PubgClient
from imposter_finder.lol_collector import _ddragon
from imposter_finder.registry import PubgPlayer
from imposter_finder.storage import LocalStore

NAMES = ["철수", "영희", "길동", "미애", "민수", "지영", "동현", "수진", "현우", "서연", "준호", "하늘"]
ROMAN = ["Cheolsu", "Younghee", "Gildong", "Miae", "Minsu", "Jiyoung", "Donghyun", "Sujin", "Hyunwoo", "Seoyeon",
         "Junho", "Haneul"]
OUT = ROOT_DIR / "docs" / "images"
RAW = ROOT_DIR / "data" / "samples"  # 리포트 원본 (커밋 안 함)
CHANNELS = {"pubg": "배틀그라운드", "lol": "리그 오브 레전드", "tft": "전략적 팀 전투", "fc": "FC 온라인"}
ROWS = [("pubg", "lol"), ("tft", "fc")]  # README 2×2: 줄마다 높이를 맞춘다
sys.path.insert(0, str(Path(__file__).resolve().parent))
from discord_mock import encode, render  # noqa: E402


class Pseudonyms:
    def __init__(self, people: list[dict]) -> None:
        self.people = people
        self.map: dict[str, str] = {}  # casefold 실제 → 가명
        self.real: set[str] = set()
        for i, person in enumerate(people):
            accounts = person.get("accounts", {})
            self._add(person["name"], NAMES[i])
            if accounts.get("pubg"):
                self._add(accounts["pubg"]["nickname"], ROMAN[i])
            if accounts.get("fconline"):
                self._add(accounts["fconline"]["nickname"], f"{NAMES[i]}FC")
            for riot_id in accounts.get("lol", {}).get("riot_ids", []):
                self._add(riot_id.split("#")[0], NAMES[i])
            if person.get("discord_user_id"):
                self.real.add(person["discord_user_id"])
        assert not {n.casefold() for n in NAMES + ROMAN} & {r.casefold() for r in self.real}, "가명이 실제 이름과 겹침"

    def _add(self, real: str, fake: str) -> None:
        self.map[real.casefold()] = fake
        self.real.add(real)

    def strangers(self, names: list[str], prefix: str) -> None:
        """친구가 아닌 참가자 이름은 Player1, Player2…"""
        for name in names:
            if name and name.casefold() not in self.map:
                self._add(name, f"{prefix}{sum(v.startswith(prefix) for v in self.map.values()) + 1}")

    def apply(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self.apply(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.apply(v) for v in value]
        if isinstance(value, str):
            return self.map.get(value.casefold(), value)
        return value

    def leaks(self, *values: Any) -> int:
        text = json.dumps(values, ensure_ascii=False).casefold()
        return sum(w.casefold() in text for w in self.real if len(w) >= 2)


def save(game: str, files: list[tuple], payload: dict, mentions: list[str]) -> None:
    folder = RAW / game
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        old.unlink()
    for name, data, *_ in files:
        (folder / name).write_bytes(data)
    (folder / "message.json").write_text(json.dumps({"files": [f[0] for f in files], "payload": payload,
                                                    "mentions": mentions}, ensure_ascii=False, indent=1))


def compose_all() -> None:
    """게임마다 디스코드 모양 한 장. README 2×2에서 같은 줄끼리 높이를 맞춘다."""
    frames = {}
    for game in CHANNELS:
        folder = RAW / game
        if not (folder / "message.json").exists():
            continue
        message = json.loads((folder / "message.json").read_text())
        files = [(name, (folder / name).read_bytes()) for name in message["files"]]
        frames[game] = render(files, message["payload"], message["mentions"], CHANNELS[game])
    OUT.mkdir(parents=True, exist_ok=True)
    for game, game_frames in frames.items():
        row = next(r for r in ROWS if game in r)
        height = max(frames[g][0].height for g in row if g in frames)
        data, ext = encode(game_frames, height)
        for old in OUT.glob(f"report-{game}.*"):
            old.unlink()
        (OUT / f"report-{game}.{ext}").write_bytes(data)
        print(f"report-{game}.{ext} {len(data) / 2**20:.2f}MB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pubg")
    parser.add_argument("--lol")
    parser.add_argument("--fc")
    parser.add_argument("--tft")
    parser.add_argument("--compose-only", action="store_true", help="data/samples의 원본으로 합성만")
    args = parser.parse_args()
    if args.compose_only:
        compose_all()
        return
    settings = load_settings()
    people = json.loads(settings.players_path.read_text(encoding="utf-8"))["players"]
    fake = Pseudonyms(people)
    db = sqlite3.connect(settings.local_database_path)
    store = LocalStore(settings.local_database_path)
    load = lambda table, mid: json.loads(db.execute(f"select payload_json from {table} where match_id = ?", (mid,)).fetchone()[0])
    key, model = settings.gemini_api_key, settings.gemini_model

    if args.pubg:
        pubg(args.pubg, fake, people, settings, load, key, model)
    if args.lol:
        lol(args.lol, fake, people, settings, store, load, key, model)
    if args.tft:
        tft(args.tft, fake, people, settings, key, model)
    if args.fc:
        fc(args.fc, fake, people, store, load, key, model)
    compose_all()  # 지정하지 않은 게임은 data/samples의 원본을 그대로


def pubg(match_id, fake, people, settings, load, key, model) -> None:
    match = load("matches", match_id)
    url = next(x["attributes"]["URL"] for x in match["included"] if x["type"] == "asset")
    telemetry = PubgClient(settings.pubg_api_key).get_telemetry(url)
    fake.strangers([x["attributes"]["stats"]["name"] for x in match["included"] if x["type"] == "participant"], "Player")
    in_match = {x["attributes"]["stats"]["name"].casefold() for x in match["included"] if x["type"] == "participant"}
    registered = [PubgPlayer(NAMES[i], None, p["accounts"]["pubg"].get("platform", "steam"), ROMAN[i])
                  for i, p in enumerate(people)
                  if p.get("accounts", {}).get("pubg") and p["accounts"]["pubg"]["nickname"].casefold() in in_match]
    match, telemetry = fake.apply(match), fake.apply(telemetry)
    payload, files = pubg_report.build_report("steam", match, telemetry, registered, key, model)
    assert not fake.leaks(match, payload), "배그: 실제 이름이 남음"
    save("pubg", files, payload, [p.name for p in registered])


def lol(match_id, fake, people, settings, store, load, key, model) -> None:
    match = load("lol_matches", match_id)
    timeline = RiotClient(settings.riot_api_key or "").timeline(match_id)
    fake.strangers([p.get("riotIdGameName") or p.get("summonerName") for p in match["info"]["participants"]], "Summoner")
    friends = {}
    for i, p in enumerate(people):
        for riot_id in p.get("accounts", {}).get("lol", {}).get("riot_ids", []):
            if puuid := store.lol_puuid(riot_id):
                friends[puuid] = lol_report.Friend(NAMES[i], None)
    match, timeline = fake.apply(match), fake.apply(timeline)
    payload, files = lol_report.build_report(match, timeline, friends, _ddragon, key, model)
    assert not fake.leaks(match, payload), "롤: 실제 이름이 남음"
    save("lol", files, payload, list(dict.fromkeys(friends[p["puuid"]].name for p in match["info"]["participants"]
                                                   if p["puuid"] in friends)))


def tft(match_id, fake, people, settings, key, model) -> None:
    """TFT 키로 경기·친구 puuid·티어를 받는다 (puuid는 키마다 달라 롤 DB 값을 못 쓴다)."""
    client = RiotClient(settings.riot_tft_api_key or "")
    match = client.tft_match(match_id)
    lobby = {p["puuid"] for p in match["info"]["participants"]}
    friends = {}
    for i, p in enumerate(people):
        for riot_id in p.get("accounts", {}).get("lol", {}).get("riot_ids", []):
            try:
                puuid = client.puuid(riot_id)
            except RiotApiError:
                continue
            if puuid in lobby:
                friends[puuid] = tft_report.Friend(NAMES[i], None)
    ranked = match["info"].get("queue_id") in tft_report.RANKED_QUEUES
    tiers = {puuid: rank for puuid in friends if ranked and (rank := client.tft_rank(puuid))}
    fake.strangers([p.get("riotIdGameName") for p in match["info"]["participants"]], "Player")
    match = fake.apply(match)
    payload, files = tft_report.build_report(match, friends, tiers, key, model)
    assert not fake.leaks(match, payload), "TFT: 실제 이름이 남음"
    order = sorted((p for p in match["info"]["participants"] if p["puuid"] in friends), key=lambda p: p["placement"])
    save("tft", files, payload, [friends[p["puuid"]].name for p in order])


def fc(match_id, fake, people, store, load, key, model) -> None:
    match = load("fc_matches", match_id)
    friends = {}
    for i, p in enumerate(people):
        nickname = p.get("accounts", {}).get("fconline", {}).get("nickname")
        if nickname and (ouid := store.fc_ouid(nickname)):
            friends[ouid] = fc_report.Friend(NAMES[i], None)
    sides = [s["ouid"] for s in match["matchInfo"]]
    head_to_head = fake.apply(store.fc_head_to_head(sides[0], sides[1], limit=10))
    match = fake.apply(match)
    meta = FcMeta(ROOT_DIR / "data" / "fc_meta")
    payload, files = fc_report.build_report(match, head_to_head, friends, meta, FcTeamColors(ROOT_DIR / "data" / "fc_meta"),
                                            key, model, FcPrices(ROOT_DIR / "data" / "fc_meta", meta))
    assert not fake.leaks(match, payload), "FC: 실제 이름이 남음"
    save("fc", files, payload, [friends[o].name for o in sides if o in friends])


if __name__ == "__main__":
    main()
