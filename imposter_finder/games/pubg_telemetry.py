"""PUBG 텔레메트리에서 리포트용 이야기를 뽑는다: 경로, 낙하, 교전, 자기장, 무기별 피해.

좌표는 cm 단위, 지도 왼쪽 위가 (0, 0)이고 y가 아래로 커진다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image

from imposter_finder.analysis.cards import ASSET_DIR, _download

ASSETS = "https://raw.githubusercontent.com/pubg/api-assets/master"
LFS = "https://media.githubusercontent.com/media/pubg/api-assets/master"  # High_Res 지도는 Git LFS
# 텔레메트리 mapName → (지도 그림 이름, 한 변 길이 cm)
MAPS = {"Erangel_Main": ("Erangel", 816000), "Baltic_Main": ("Erangel", 816000), "Desert_Main": ("Miramar", 816000),
        "Savage_Main": ("Sanhok", 408000), "DihorOtok_Main": ("Vikendi", 816000), "Tiger_Main": ("Taego", 816000),
        "Kiki_Main": ("Deston", 816000), "Neon_Main": ("Rondo", 816000), "Chimera_Main": ("Paramo", 306000),
        "Summerland_Main": ("Karakin", 204000), "Heaven_Main": ("Haven", 102000), "Range_Main": ("Camp_Jackal", 204000)}
MAP_PX = 4096  # 8192px 원본을 줄여 캐시


@dataclass
class Member:
    name: str
    account_id: str
    friend: bool
    path: list[tuple[float, float, float, bool]] = field(default_factory=list)  # (초, x, y, 탈것)
    jump: tuple[float, float, float] | None = None  # 비행기에서 뛰어내린 (초, x, y)
    landing: tuple[float, float, float] | None = None
    knocked: list[tuple[float, float, float]] = field(default_factory=list)  # 내가 기절당한 (초, x, y)
    revived: list[float] = field(default_factory=list)
    death: tuple[float, float, float] | None = None
    knocks: list[dict] = field(default_factory=list)  # 내가 기절시킨 적 {t, x, y, weapon, distance}
    kills: list[dict] = field(default_factory=list)
    weapon_damage: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    weapon_hits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    weapon_fires: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    weapon_knocks: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    weapon_kills: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class Story:
    map_name: str
    map_size: int
    duration: float  # 경기 전체 (초)
    team_end: float  # 우리 팀 마지막 생존자가 끝난 시점
    members: list[Member]
    plane: tuple[tuple[float, float], tuple[float, float]] | None
    zones: list[tuple[float, tuple[float, float], float, tuple[float, float], float]]  # (초, 파란원 중심, 반지름, 흰원 중심, 반지름)
    alive: list[tuple[float, int]]  # (초, 남은 인원)
    phases: list[tuple[float, float]]  # 자기장이 줄어드는 구간 (시작, 끝)


def _time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _xy(char: dict | None) -> tuple[float, float] | None:
    loc = (char or {}).get("location") or {}
    return (loc["x"], loc["y"]) if "x" in loc and loc["x"] else None


def build_story(match: dict[str, Any], telemetry: list[dict[str, Any]], friend_names: set[str]) -> Story | None:
    start = next((e for e in telemetry if e["_T"] == "LogMatchStart"), None)
    if not start:
        return None
    t0 = _time(start["_D"])
    at = lambda e: (_time(e["_D"]) - t0).total_seconds()
    lower = {n.lower() for n in friend_names}
    team_id = next((c["character"]["teamId"] for c in start["characters"] if c["character"]["name"].lower() in lower), None)
    if team_id is None:
        return None
    members = {c["character"]["accountId"]: Member(c["character"]["name"], c["character"]["accountId"],
                                                   c["character"]["name"].lower() in lower)
               for c in start["characters"] if c["character"]["teamId"] == team_id}
    mine = lambda char: (char or {}).get("accountId") in members
    plane_points: list[tuple[float, float, float]] = []
    zones, alive = [], []

    for e in telemetry:
        kind = e["_T"]
        if kind == "LogPlayerPosition":
            vehicle = e.get("vehicle") or {}
            xy = _xy(e["character"])
            if vehicle.get("vehicleType") == "TransportAircraft":
                if xy:
                    plane_points.append((at(e), *xy))
                continue
            if mine(e["character"]) and xy and e.get("elapsedTime", 0) > 0:
                members[e["character"]["accountId"]].path.append((at(e), *xy, bool(e["character"].get("isInVehicle"))))
        elif kind == "LogVehicleLeave" and mine(e.get("character")) \
                and (e.get("vehicle") or {}).get("vehicleType") == "TransportAircraft":
            xy = _xy(e["character"])
            if xy and members[e["character"]["accountId"]].jump is None:
                members[e["character"]["accountId"]].jump = (at(e), *xy)
        elif kind == "LogParachuteLanding" and mine(e.get("character")):
            m = members[e["character"]["accountId"]]
            xy = _xy(e["character"])
            if xy and m.landing is None:
                m.landing = (at(e), *xy)
        elif kind == "LogPlayerMakeGroggy":
            if mine(e.get("victim")) and _xy(e["victim"]):
                members[e["victim"]["accountId"]].knocked.append((at(e), *_xy(e["victim"])))
            attacker = e.get("attacker")
            if mine(attacker) and not mine(e.get("victim")) and _xy(e.get("victim")):
                weapon = e.get("damageCauserName", "")
                members[attacker["accountId"]].knocks.append(
                    {"t": at(e), "xy": _xy(e["victim"]), "weapon": weapon, "distance": e.get("distance", 0) / 100})
                members[attacker["accountId"]].weapon_knocks[weapon] += 1
        elif kind == "LogPlayerRevive" and mine(e.get("victim")):
            members[e["victim"]["accountId"]].revived.append(at(e))
        elif kind == "LogPlayerKillV2":
            victim = e.get("victim")
            if mine(victim):
                xy = _xy(victim) or (members[victim["accountId"]].path[-1][1:3] if members[victim["accountId"]].path else None)
                if xy:
                    members[victim["accountId"]].death = (at(e), *xy)
            killer = e.get("killer") or e.get("finisher")
            if mine(killer) and not mine(victim) and _xy(victim):
                info = e.get("killerDamageInfo") or e.get("finishDamageInfo") or {}
                weapon = info.get("damageCauserName", "")
                members[killer["accountId"]].kills.append(
                    {"t": at(e), "xy": _xy(victim), "weapon": weapon, "distance": info.get("distance", 0) / 100})
                members[killer["accountId"]].weapon_kills[weapon] += 1
        elif kind == "LogPlayerTakeDamage":
            attacker, victim = e.get("attacker"), e.get("victim")
            if mine(attacker) and victim and not mine(victim) and e.get("damage", 0) > 0:
                m = members[attacker["accountId"]]
                m.weapon_damage[e.get("damageCauserName", "")] += e["damage"]
                if e.get("damageTypeCategory") == "Damage_Gun":
                    m.weapon_hits[e.get("damageCauserName", "")] += 1
        elif kind == "LogWeaponFireCount" and mine(e.get("character")):
            fires = members[e["character"]["accountId"]].weapon_fires  # 누적값으로 여러 번 온다
            fires[e["weaponId"]] = max(fires[e["weaponId"]], e.get("fireCount", 0))
        elif kind == "LogGameStatePeriodic":
            g = e["gameState"]
            safe, warn = g["safetyZonePosition"], g["poisonGasWarningPosition"]
            zones.append((at(e), (safe["x"], safe["y"]), g["safetyZoneRadius"],
                          (warn["x"], warn["y"]), g["poisonGasWarningRadius"]))
            alive.append((at(e), g["numAlivePlayers"]))

    end = next((e for e in telemetry if e["_T"] == "LogMatchEnd"), None)
    duration = at(end) if end else (zones[-1][0] if zones else 0)
    team = sorted(members.values(), key=lambda m: (not m.friend, m.name.lower()))
    team_end = max((m.death[0] if m.death else duration) for m in team)

    plane = None
    if len(plane_points) >= 2:
        plane_points.sort()
        plane = (plane_points[0][1:], plane_points[-1][1:])

    # 파란 자기장이 줄어드는 동안 (반지름이 10초 사이에 줄어듦)
    phases, shrinking = [], None
    for (t1, _, r1, *_), (t2, _, r2, *_) in zip(zones, zones[1:]):
        if r2 < r1 - 1 and shrinking is None:
            shrinking = t1
        elif r2 >= r1 - 1 and shrinking is not None:
            phases.append((shrinking, t1))
            shrinking = None
    if shrinking is not None:
        phases.append((shrinking, zones[-1][0]))

    map_name, size = MAPS.get(match["data"]["attributes"]["mapName"], ("Erangel", 816000))
    return Story(map_name, size, duration, team_end, team, plane, zones, alive, phases)


def zone_at(story: Story, t: float):
    """t초 시점의 (파란원 중심, 반지름, 흰원 중심, 반지름)."""
    best = None
    for z in story.zones:
        if z[0] <= t:
            best = z
    return best[1:] if best else None


# ── 그림 자료 ────────────────────────────────────────────
def map_image(map_name: str) -> Image.Image:
    """8192px 원본(70MB 안팎)을 한 번 받아 4096px JPEG로 줄여 data/assets/pubg/maps에 둔다."""
    path = ASSET_DIR / "pubg" / "maps" / f"{map_name}_{MAP_PX}.jpg"
    if path.exists():
        return Image.open(path).convert("RGB")
    Image.MAX_IMAGE_PIXELS = None
    try:
        raw = _download_uncached(f"{LFS}/Assets/Maps/{map_name}_Main_No_Text_High_Res.png")
        im = Image.open(BytesIO(raw)).convert("RGB").resize((MAP_PX, MAP_PX), Image.LANCZOS)
    except Exception:  # 고해상도가 없으면 819px 저해상도
        im = Image.open(BytesIO(_download(f"{ASSETS}/Assets/Maps/{map_name}_Main_No_Text_Low_Res.png"))).convert("RGB")
        im = im.resize((MAP_PX, MAP_PX), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=90)
    return im


def _download_uncached(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "imposter-finder/0.1"})
    return urllib.request.urlopen(req, timeout=120).read()


def _asset_index() -> set[str]:
    """api-assets 저장소의 파일 목록 (무기 아이콘이 어느 폴더에 있는지 찾는 데 씀). 한 번 받아 둔다."""
    path = ASSET_DIR / "pubg" / "tree.json"
    if not path.exists():
        raw = _download_uncached("https://api.github.com/repos/pubg/api-assets/git/trees/master?recursive=1")
        files = [x["path"] for x in json.loads(raw)["tree"] if x["type"] == "blob"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(files))
    return set(json.loads(path.read_text()))


_INDEX: set[str] | None = None
_NAMES: dict[str, str] | None = None


def weapon_name(causer: str) -> str:
    """WeapHK416_C → M416. 사전에 없으면 앞뒤를 떼어 보여준다."""
    global _NAMES
    if _NAMES is None:
        try:
            _NAMES = json.loads(_download(f"{ASSETS}/dictionaries/telemetry/damageCauserName.json"))
        except Exception:
            _NAMES = {}
    causer = causer.removesuffix("_0")
    return _NAMES.get(causer) or causer.removeprefix("Weap").removeprefix("Proj").removesuffix("_C")


def weapon_icon_url(causer: str) -> str | None:
    """흰 실루엣 아이콘 (게임 HUD와 같은 모양). WeapHK416_C → Item_Weapon_HK416_C_w.png"""
    global _INDEX
    try:
        _INDEX = _INDEX or _asset_index()
    except Exception:
        return None
    core = causer.removesuffix("_0").removeprefix("Weap").removeprefix("Proj").removesuffix("_C")
    by_lower = {p.lower(): p for p in _INDEX}
    for folder in ("Weapon/Main", "Weapon/Handgun", "Weapon/Melee", "Equipment/Throwable"):
        for suffix in ("_w.png", ".png"):
            hit = by_lower.get(f"assets/item/{folder}/item_weapon_{core}_c{suffix}".lower())
            if hit:
                return f"{ASSETS}/{hit}"
    return None


def fire_key(causer: str) -> str:
    """피해 이름(WeapHK416_C)과 발사 기록 이름(Item_Weapon_HK416_C)을 맞춘다."""
    return "Item_Weapon_" + causer.removesuffix("_0").removeprefix("Weap")
