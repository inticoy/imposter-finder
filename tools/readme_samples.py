"""README 예시 이미지: 실제 경기로 리포트를 만들되 이름·닉네임은 가명으로.

렌더링 전에 경기 데이터(매치·텔레메트리·타임라인·맞대결) 안의 닉네임과 players.json의 이름을
가명으로 바꾸므로, 이미지 글자와 Gemini 판독 문장 모두 가명으로 나온다.

    PYTHONPATH=. .venv/bin/python tools/readme_samples.py --pubg <match id> --lol <match id> --fc <match id>

결과: docs/images/{pubg,lol,fc}.(gif|png) — 디스코드에 올라온 모습 그대로 한 장 (tools/discord_mock.py).
리포트 원본은 data/samples/에 두고, --compose-only면 API 호출 없이 그 원본으로 다시 합성만 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import sqlite3
from pathlib import Path
from typing import Any

from imposter_finder.analysis import fc_report, lol_report, pubg_report
from imposter_finder.config import ROOT_DIR, load_settings
from imposter_finder.games.fconline import FcMeta, FcPrices, FcTeamColors
from imposter_finder.games.lol import RiotClient
from imposter_finder.games.pubg import PubgClient
from imposter_finder.lol_collector import _ddragon
from imposter_finder.registry import PubgPlayer
from imposter_finder.storage import LocalStore

NAMES = ["철수", "영희", "길동", "미애", "민수", "지영", "동현", "수진", "현우", "서연", "준호", "하늘"]
ROMAN = ["Cheolsu", "Younghee", "Gildong", "Miae", "Minsu", "Jiyoung", "Donghyun", "Sujin", "Hyunwoo", "Seoyeon",
         "Junho", "Haneul"]
OUT = ROOT_DIR / "docs" / "images"
RAW = ROOT_DIR / "data" / "samples"  # 리포트 원본 (커밋 안 함)
CHANNELS = {"pubg": "배틀그라운드", "lol": "리그 오브 레전드", "fc": "FC 온라인"}
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
    """세 게임을 같은 높이로 맞춰 README에 나란히."""
    frames = {}
    for game in CHANNELS:
        folder = RAW / game
        message = json.loads((folder / "message.json").read_text())
        files = [(name, (folder / name).read_bytes()) for name in message["files"]]
        frames[game] = render(files, message["payload"], message["mentions"], CHANNELS[game])
    height = max(f[0].height for f in frames.values())
    OUT.mkdir(parents=True, exist_ok=True)
    for game, game_frames in frames.items():
        data, ext = encode(game_frames, height)
        for old in OUT.glob(f"{game}.*"):
            old.unlink()
        (OUT / f"{game}.{ext}").write_bytes(data)
        print(f"{game}.{ext} {len(data) / 2**20:.2f}MB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pubg")
    parser.add_argument("--lol")
    parser.add_argument("--fc")
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

    # 배그
    match = load("matches", args.pubg)
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

    # 롤
    match = load("lol_matches", args.lol)
    timeline = RiotClient(settings.riot_api_key or "").timeline(args.lol)
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

    # FC
    match = load("fc_matches", args.fc)
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
    compose_all()


if __name__ == "__main__":
    main()
