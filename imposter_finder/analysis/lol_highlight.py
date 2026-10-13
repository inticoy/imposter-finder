"""자동 롤 리포트의 하이라이트 GIF: 친구의 최고 교전 한 장면을 기울인 협곡 위에 다시 그린다 (2.5D).

지형은 게임 화면을 위에서 찍은 협곡 전체 그림(롤 위키, 12032px)을 월드 좌표에 맞춰 원근으로 눕힌다.
위치는 타임라인 1분 스냅샷 + 킬·오브젝트·건물 좌표뿐이라 실제 이동이 아니라 추정 경로다.
- 경로점 사이는 미니맵의 벽(투명)을 피해 A*로 걷고, 벽 안에 찍힌 점은 가장 가까운 길로 옮긴다.
- 킬러·어시스트는 킬 자리 한가운데가 아니라 사거리만큼 떨어진 곳(오던 쪽)에 둔다.
- 죽은 챔피언은 부활할 때까지 지도에서 빠진다 (부활 시각은 lol_flow와 같은 계산).
스킬 위치는 API에 없어서, 킬 이벤트의 victimDamageReceived(스킬별 실제 피해)를 아래 판에 보여 준다.
"""
from __future__ import annotations

import heapq
import json
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from imposter_finder.analysis.cards import (ALLY, BG, ENEMY, GIF_MAX_BYTES, GOLD, HIGHLIGHT, LABEL, PAD, SS, TRACK,
                                            WHITE, WIDTH, _download, ambient_bg, bar, font, glass, header, icon, shade)
from imposter_finder.analysis.lol_flow import (DRAGON_NAMES, FOUNTAIN, MONSTERS, MULTI, RIFT, TURRETS, WALK, clock,
                                               death_seconds, disc, ring_sprite, shared_palette, tinted)
from imposter_finder.analysis.lol_report import AMBIENT_DIM, DRAGON_ICONS, MINIMAP_ICONS, nickname
from imposter_finder.games.lol import DDRAGON, DDragon

# 게임 화면으로 찍은 협곡 전체 (위에서 본 그림). 지형·수풀·강·건물이 그대로 들어 있다.
HD_MAP = "https://wiki.leagueoflegends.com/en-us/images/Summoner%27s_Rift_Map_HD.jpg"
HD_SCALE = 1000  # 아래 행렬은 그림 폭을 1000으로 줄인 좌표 기준
# 월드 (x, y) → 그림 좌표 (폭 1000 기준). 미니맵 벽과 그림의 숲을 맞춰 구한 사영 변환.
WORLD_TO_HD = ((0.0600237633, 0.0003891748, 101.909531251),
               (0.0003426837, -0.0491884847, 811.285892843),
               (1.1476e-06, -2.922e-07, 1.0))
CDRAGON = "https://raw.communitydragon.org/latest"
STAT_ICONS = {"평타": f"{CDRAGON}/game/assets/perks/statmods/statmodsattackdamageicon.png",
              "기타": f"{CDRAGON}/game/assets/perks/statmods/statmodsadaptiveforceicon.png"}

# ── 배치 ────────────────────────────────────────────────
HEIGHT = 680
VIEW = (PAD, 76, WIDTH - PAD, 496)  # 협곡 화면
VW, VH = VIEW[2] - VIEW[0], VIEW[3] - VIEW[1]
PANEL_Y, PANEL_B = 512, HEIGHT - 16
RSS = 2  # 지형은 두 배로 그려 줄인다

# ── 장면 ────────────────────────────────────────────────
FRAME_MS = 100
FIRST_MS, LAST_MS = 900, 4000
INTRO = 14  # 전체 지도 → 교전으로 다가가는 장면 수
PRE_MS, POST_MS = 9000, 3500  # 첫 킬 전 · 마지막 킬 뒤
STEP_CALM, STEP_FIGHT, STEP_SLOW = 220, 130, 65  # 장면 하나가 보여 주는 경기 시간 (ms)
SLOW_BEFORE, SLOW_AFTER = 900, 1100  # 킬 앞뒤 느린 화면
DEATH_MS = 1400  # 쓰러지는 모션을 보여 주는 시간
ATTACK_MS = 800  # 평타 한 번
CLUSTER_MS = 12_000  # 이 안에 이어진 킬은 같은 교전
HFOV = math.radians(46)
PITCH = 52  # 카메라 기울기 (지면에서 위로)

# ── 사람 크기 (월드 단위) ────────────────────────────────
CHAMP_D = 44  # 화면에서 초상 지름 (교전 카메라 기준 px)
LIFT = 330  # 초상이 서 있는 높이
RUN = 0.38  # 걷는 속도 (월드 단위/ms ≈ 이동 속도 380)
VMAX = 0.9  # 이보다 빨라야 닿는 추정 경로점은 버린다
NEAR = 3500  # 교전 중심에서 이 안의 챔피언만 경로를 다듬는다
GRID = 256  # 길 찾기 칸 수 (한 칸 ≈ 58 월드 단위)
WORLD_MIN, WORLD_SPAN = -120, 14990  # 미니맵이 덮는 월드 범위 (lol_flow와 같다)

KEYS = "QWER"
SUMMONER = {"summonerdot": ("SummonerDot", "점화"), "smite": ("SummonerSmite", "강타"),
            "summonerexhaust": ("SummonerExhaust", "탈진")}


# ── 행렬 (3×3) ──────────────────────────────────────────
def _mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _inv(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    co = ((e * i - f * h, c * h - b * i, b * f - c * e),
          (f * g - d * i, a * i - c * g, c * d - a * f),
          (d * h - e * g, b * g - a * h, a * e - b * d))
    det = a * co[0][0] + b * co[1][0] + c * co[2][0]
    return tuple(tuple(v / det for v in row) for row in co)


def _apply(m, x, y):
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    return (m[0][0] * x + m[0][1] * y + m[0][2]) / w, (m[1][0] * x + m[1][1] * y + m[1][2]) / w


# ── 카메라 ──────────────────────────────────────────────
class Camera:
    """지면(z=0) 위를 남쪽에서 비스듬히 내려다보는 원근 카메라. 월드 x 동쪽, y 북쪽, z 위."""

    def __init__(self, target: tuple[float, float], dist: float, pitch: float = PITCH, yaw: float = 0.0,
                 w: int = VW, h: int = VH) -> None:
        p, y = math.radians(pitch), math.radians(yaw)
        back = (-math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p))  # 목표 → 카메라
        self.c = (target[0] + dist * back[0], target[1] + dist * back[1], dist * back[2])
        self.f = tuple(-v for v in back)
        self.r = (math.cos(y), -math.sin(y), 0.0)  # 오른쪽 (yaw 0이면 동쪽)
        f, r = self.f, self.r
        self.u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
        self.w, self.h = w, h
        self.F = (w / 2) / math.tan(HFOV / 2)
        self.target, self.dist, self.pitch, self.yaw = target, dist, pitch, yaw

    def project(self, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
        d = (x - self.c[0], y - self.c[1], z - self.c[2])
        X = sum(a * b for a, b in zip(d, self.r))
        Y = sum(a * b for a, b in zip(d, self.u))
        Z = sum(a * b for a, b in zip(d, self.f))
        Z = max(Z, 1e-6)
        return self.w / 2 + self.F * X / Z, self.h / 2 - self.F * Y / Z, Z

    def ground(self) -> tuple:
        """지면 월드 (x, y, 1) → 화면 (sx, sy, 1) 사영 변환."""
        rows = []
        for axis in (self.r, self.u, self.f):
            rows.append((axis[0], axis[1], -sum(a * c for a, c in zip(axis, self.c))))
        k = ((self.F, 0, self.w / 2), (0, -self.F, self.h / 2), (0, 0, 1))
        return _mul(k, tuple(rows))

    def scale(self, x: float, y: float, z: float = 0.0) -> float:
        """이 자리에서 월드 1단위가 화면 몇 px인가."""
        return self.F / self.project(x, y, z)[2]


def blend_camera(a: Camera, b: Camera, k: float) -> Camera:
    e = k * k * (3 - 2 * k)
    tgt = (a.target[0] + (b.target[0] - a.target[0]) * e, a.target[1] + (b.target[1] - a.target[1]) * e)
    dist = math.exp(math.log(a.dist) + (math.log(b.dist) - math.log(a.dist)) * e)
    return Camera(tgt, dist, a.pitch + (b.pitch - a.pitch) * e, a.yaw + (b.yaw - a.yaw) * e)


# ── 지형 ────────────────────────────────────────────────
class Terrain:
    """협곡 그림을 카메라에 맞춰 눕힌다. 멀리 볼 땐 작은 그림에서 뽑아 깜빡임이 없다."""

    def __init__(self) -> None:
        Image.MAX_IMAGE_PIXELS = None  # 위키 원본이 1억 화소가 넘는다
        src = Image.open(BytesIO(_download(HD_MAP)))
        src.draft("RGB", (src.width // 2, src.height // 2))  # JPEG를 반 크기로 바로 풀면 빠르고 가볍다
        src = src.convert("RGB")
        src = ImageEnhance.Contrast(ImageEnhance.Color(src).enhance(1.08)).enhance(1.06)
        self.levels = [src, src.reduce(2), src.reduce(4)]

    def view(self, cam: Camera) -> Image.Image:
        size = (cam.w * RSS, cam.h * RSS)
        world_of_screen = _inv(cam.ground())
        # 화면 1px에 그림 몇 px가 들어가는지 보고 해상도를 고른다
        cx, cy = cam.w / 2, cam.h * 0.4
        a = _apply(_mul(WORLD_TO_HD, world_of_screen), cx, cy)
        b = _apply(_mul(WORLD_TO_HD, world_of_screen), cx + 1, cy)
        texel = math.dist(a, b) * self.levels[0].width / HD_SCALE / RSS
        level = 0 if texel < 1.6 else 1 if texel < 3.2 else 2
        src = self.levels[level]
        k = src.width / HD_SCALE
        # 출력 픽셀(RSS배) → 화면 → 월드 → 그림(폭 1000) → 이 해상도의 그림
        to_src = _mul(((k, 0, 0), (0, k, 0), (0, 0, 1)),
                      _mul(WORLD_TO_HD, _mul(world_of_screen, ((1 / RSS, 0, 0), (0, 1 / RSS, 0), (0, 0, 1)))))
        coeffs = [v / to_src[2][2] for row in to_src for v in row][:8]
        out = src.transform(size, Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=BG)
        out = out.resize((cam.w, cam.h), Image.LANCZOS)
        # 먼 곳은 어둡게 (안개), 가장자리는 살짝 그늘
        fog = Image.linear_gradient("L").resize((cam.w, cam.h)).point(lambda v: int(max(0, 170 - v * 1.15)))
        out.paste(BG, (0, 0), fog)
        return out


# ── 길 찾기 ─────────────────────────────────────────────
@lru_cache(maxsize=1)
def walk_grid() -> tuple[bytes, int]:
    """미니맵에서 투명(벽·정글 숲)이 아닌 곳 = 걸을 수 있는 칸."""
    rift = Image.open(BytesIO(_download(RIFT))).convert("RGB")
    # 미니맵의 벽은 (1,1,1) 근처의 아주 어두운 색. 가장 밝은 채널로 판단한다
    r, g, b = rift.split()
    bright = ImageChops.lighter(ImageChops.lighter(r, g), b)
    walk = bright.point(lambda v: 255 if v > 70 else 0).resize((GRID, GRID), Image.BOX)
    walk = walk.point(lambda v: 255 if v >= 128 else 0)
    return walk.tobytes(), GRID


def _cell(x: float, y: float) -> tuple[int, int]:
    return (min(GRID - 1, max(0, int((x - WORLD_MIN) / WORLD_SPAN * GRID))),
            min(GRID - 1, max(0, int((1 - (y - WORLD_MIN) / WORLD_SPAN) * GRID))))


def _center(c: tuple[int, int]) -> tuple[float, float]:
    return (WORLD_MIN + (c[0] + 0.5) / GRID * WORLD_SPAN, WORLD_MIN + (1 - (c[1] + 0.5) / GRID) * WORLD_SPAN)


def _walkable(c: tuple[int, int]) -> bool:
    grid, n = walk_grid()
    return 0 <= c[0] < n and 0 <= c[1] < n and grid[c[1] * n + c[0]] > 0


def snap(p: tuple[float, float]) -> tuple[float, float]:
    """벽 안의 점은 가장 가까운 걸을 수 있는 칸으로."""
    c = _cell(*p)
    if _walkable(c):
        return p
    for r in range(1, 12):
        best = None
        for dx in range(-r, r + 1):
            for dy in (-r, r) if abs(dx) != r else range(-r, r + 1):
                q = (c[0] + dx, c[1] + dy)
                if _walkable(q):
                    d = math.dist(_center(q), p)
                    if best is None or d < best[0]:
                        best = (d, q)
        if best:
            return _center(best[1])
    return p


def _clear(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """두 칸을 잇는 직선이 벽을 지나지 않는가."""
    n = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
    for k in range(1, n):
        if not _walkable((round(a[0] + (b[0] - a[0]) * k / n), round(a[1] + (b[1] - a[1]) * k / n))):
            return False
    return True


@lru_cache(maxsize=4096)
def _route_cells(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    if a == b or _clear(a, b):
        return (a, b)
    open_ = [(0.0, 0.0, a)]
    came: dict = {a: None}
    cost = {a: 0.0}
    h = lambda c: math.dist(c, b)
    found = False
    while open_:
        _, g, c = heapq.heappop(open_)
        if c == b:
            found = True
            break
        if g > cost.get(c, 1e18) or g > 600:
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not dx and not dy:
                    continue
                q = (c[0] + dx, c[1] + dy)
                if not _walkable(q) or (dx and dy and not (_walkable((c[0] + dx, c[1])) and _walkable((c[0], c[1] + dy)))):
                    continue
                ng = g + (1.4142 if dx and dy else 1.0)
                if ng < cost.get(q, 1e18):
                    cost[q] = ng
                    came[q] = c
                    heapq.heappush(open_, (ng + h(q), ng, q))
    if not found:
        return (a, b)
    cells = [b]
    while came[cells[-1]] is not None:
        cells.append(came[cells[-1]])
    cells.reverse()
    out = [cells[0]]  # 줄 당기기: 보이는 가장 먼 칸으로 건너뛴다
    k = 0
    while k < len(cells) - 1:
        j = len(cells) - 1
        while j > k + 1 and not _clear(cells[k], cells[j]):
            j -= 1
        out.append(cells[j])
        k = j
    return tuple(out)


def route(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    ca, cb = _cell(*a), _cell(*b)
    if not (_walkable(ca) and _walkable(cb)):
        return [a, b]
    cells = _route_cells(ca, cb)
    return [a] + [_center(c) for c in cells[1:-1]] + [b]


@dataclass
class Track:
    """한 챔피언의 장면 속 경로: [(시작 ms, 끝 ms, 꺾은선)] — 구간마다 걷고 남은 시간은 서 있는다."""
    legs: list[tuple[float, float, list[tuple[float, float]], float]] = field(default_factory=list)
    speed: float = RUN  # 챔피언 이동 속도 (월드 단위/ms)

    def add(self, t0: float, p0, t1: float, p1) -> None:
        line = route(p0, p1)
        length = sum(math.dist(u, v) for u, v in zip(line, line[1:]))
        speed = max(self.speed, length / max(t1 - t0, 1))
        self.legs.append((t0, t1, line, length / speed if length else 0))

    def at(self, t: float) -> tuple[float, float] | None:
        if not self.legs or t < self.legs[0][0]:
            return None
        for t0, t1, line, travel in self.legs:
            if t0 <= t <= t1:
                if travel <= 0:
                    return line[-1]
                u = min(1.0, (t - t0) / travel)  # 롤은 가속이 없다: 같은 속도로 출발하고 바로 선다
                length = sum(math.dist(a, b) for a, b in zip(line, line[1:]))
                left = u * length
                for a, b in zip(line, line[1:]):
                    seg = math.dist(a, b)
                    if left <= seg and seg:
                        k = left / seg
                        return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
                    left -= seg
                return line[-1]
        last = self.legs[-1]
        return last[2][-1] if t > last[1] else None


# ── 장면 고르기 ──────────────────────────────────────────
@dataclass
class Highlight:
    star: int  # 주인공 participantId
    kills: list[dict]  # 주인공이 이 교전에서 낸 킬 (시간 순)
    scene: list[dict]  # 교전 안의 모든 킬
    multi: int
    ace: bool
    score: float


def _events(timeline: dict) -> list[dict]:
    return sorted((e for f in timeline["info"]["frames"] for e in f["events"]), key=lambda e: e["timestamp"])


def pick(match: dict, timeline: dict, friend_puuids: set[str]) -> Highlight | None:
    """친구가 킬을 낸 교전 중 가장 멋진 것: 멀티킬 · 셧다운 · 솔로킬 · 수적 열세에서 이긴 싸움."""
    info = match["info"]
    ps = {p["participantId"]: p for p in info["participants"]}
    events = _events(timeline)
    kills = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("position")]
    specials = [e for e in events if e["type"] == "CHAMPION_SPECIAL_KILL"]
    best = None
    for star in (pid for pid, p in ps.items() if p["puuid"] in friend_puuids):
        mine = [k for k in kills if k["killerId"] == star]
        chains: list[list[dict]] = []
        for k in mine:  # 앞 킬과 12초 안이면 같은 교전
            if chains and k["timestamp"] - chains[-1][-1]["timestamp"] <= CLUSTER_MS:
                chains[-1].append(k)
            else:
                chains.append([k])
        for chain in chains:
            t0, t1 = chain[0]["timestamp"], chain[-1]["timestamp"]
            near = [s for s in specials if s["killerId"] == star and t0 - 1500 <= s["timestamp"] <= t1 + 1500]
            multi = max([s.get("multiKillLength", 1) for s in near if s["killType"] == "KILL_MULTI"] + [1])
            ace = any(s["killType"] == "KILL_ACE" for s in near)
            team = ps[star]["teamId"]
            scene = [k for k in kills if t0 - CLUSTER_MS <= k["timestamp"] <= t1 + 4000]
            allies = {star} | {a for k in chain for a in k.get("assistingParticipantIds", [])}
            enemies = {k["victimId"] for k in chain}
            for k in chain:  # 피해자에게 피해를 준·받은 적도 교전 상대
                enemies |= {d["participantId"] for d in k.get("victimDamageDealt", [])
                            if d.get("type") == "OTHER" and ps.get(d["participantId"], {}).get("teamId") not in (team, None)}
            star_died = any(k["victimId"] == star for k in scene if t0 - 3000 <= k["timestamp"] <= t1 + 3000)
            score = len(chain) * 10 + {1: 0, 2: 25, 3: 60, 4: 110, 5: 180}.get(multi, 180)
            score += sum(k.get("shutdownBounty", 0) for k in chain) / 15
            score += sum(20 for k in chain if not k.get("assistingParticipantIds"))  # 솔로킬
            score += 12 * max(0, len(enemies) - len(allies))  # 수적 열세
            score += 15 if ace else 0
            score -= 15 if star_died else 0
            cand = Highlight(star, chain, scene, multi, ace, score)
            if best is None or score > best.score:
                best = cand
    return best


def headline(hl: Highlight) -> str:
    """제목에 쓸 한 마디: 펜타 킬 · 에이스 / 연속 3킬 / 솔로 킬."""
    solo = all(not k.get("assistingParticipantIds") for k in hl.kills)
    n = len(hl.kills)
    text = MULTI.get(hl.multi) if hl.multi > 1 else (f"솔로 킬 {n}번" if solo else f"연속 {n}킬") if n > 1 else \
        "솔로 킬" if solo else "처치"
    return text + (" · 에이스" if hl.ace else "")


# ── 피해 분석 ────────────────────────────────────────────
@lru_cache(maxsize=64)
def _champion_data(version: str, champion: str) -> dict:
    data = json.loads(_download(f"{DDRAGON}/cdn/{version}/data/ko_KR/champion/{champion}.json"))
    return data["data"][champion]


def damage_rows(star: dict, chain: list[dict], version: str) -> tuple[list[tuple[str, str, str | None]], list[dict]]:
    """주인공이 킬마다 준 피해를 스킬별로: ([(키, 이름, 아이콘)], [{키: 피해}] 킬 순서)."""
    try:
        champ = _champion_data(version, star["championName"])
        spells = {KEYS[n]: (s["name"], f"{DDRAGON}/cdn/{version}/img/spell/{s['image']['full']}")
                  for n, s in enumerate(champ["spells"][:4])}
        passive = (champ["passive"]["name"], f"{DDRAGON}/cdn/{version}/img/passive/{champ['passive']['image']['full']}")
    except Exception:
        spells, passive = {}, ("패시브", None)
    meta = {k: spells.get(k, (k, None)) for k in KEYS} | {"P": passive, "평타": ("기본 공격", STAT_ICONS["평타"]),
                                                          "기타": ("아이템 · 룬", STAT_ICONS["기타"])}

    def key_of(d: dict) -> str:
        slot, name = d.get("spellSlot", -1), (d.get("spellName") or "").lower()
        if 0 <= slot <= 3:
            return KEYS[slot]
        if slot in (4, 5):
            for part, (img, label) in SUMMONER.items():
                if part in name:
                    meta.setdefault(f"S:{img}", (label, f"{DDRAGON}/cdn/{version}/img/spell/{img}.png"))
                    return f"S:{img}"
            return "기타"
        if slot == 63 and not d.get("basic"):
            return "P"
        if slot >= 44 or d.get("basic"):
            return "평타"
        return "기타"

    per_kill = []
    for k in chain:
        acc: dict[str, int] = {}
        for d in k.get("victimDamageReceived", []):
            if d.get("type") != "OTHER" or d.get("participantId") != star["participantId"]:
                continue
            dmg = d.get("physicalDamage", 0) + d.get("magicDamage", 0) + d.get("trueDamage", 0)
            key = key_of(d)
            acc[key] = acc.get(key, 0) + dmg
        per_kill.append(acc)
    total: dict[str, int] = {}
    for acc in per_kill:
        for key, v in acc.items():
            total[key] = total.get(key, 0) + v
    order = [key for key in sorted(total, key=lambda key: -total[key]) if total[key] > 0][:5]
    return [(key, *meta[key]) for key in order], per_kill


# ── 장면 경로 ────────────────────────────────────────────
@dataclass
class Deaths:
    """죽은 시각 · 부활 시각 · 자리. 부활은 레벨·경기 시간으로 계산한 대기 시간의 3/4보다 이르지 않다
    (스냅샷의 '살아 있음'만 믿으면 몇 초 만에 되살아나는 일이 생긴다)."""
    spans: list[tuple[float, float, tuple[float, float]]] = field(default_factory=list)

    def dead_at(self, t: float):
        for d0, d1, pos in self.spans:
            if d0 <= t < d1:
                return d0, d1, pos
        return None


def deaths(info: dict, timeline: dict) -> dict[int, Deaths]:
    ps = {p["participantId"]: p for p in info["participants"]}
    events = _events(timeline)
    levels: dict[int, list[float]] = {pid: [] for pid in ps}
    for e in events:
        if e["type"] == "LEVEL_UP" and e.get("participantId") in ps:
            levels[e["participantId"]].append(e["timestamp"])
    alive: dict[int, list] = {pid: [] for pid in ps}  # 살아 있던 증거: 스냅샷 · 킬 관여
    for f in timeline["info"]["frames"]:
        for pid, pf in f["participantFrames"].items():
            if pf.get("championStats", {}).get("health", 1) > 0 and int(pid) in ps:
                alive[int(pid)].append((f["timestamp"], (pf["position"]["x"], pf["position"]["y"])))
    for e in events:
        if e["type"] in {"CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL"} and e.get("position"):
            for pid in [e.get("killerId"), *e.get("assistingParticipantIds", [])]:
                if pid in ps:
                    alive[pid].append((e["timestamp"], (e["position"]["x"], e["position"]["y"])))
    out = {pid: Deaths() for pid in ps}
    for e in events:
        if e["type"] != "CHAMPION_KILL" or e.get("victimId") not in ps or not e.get("position"):
            continue
        pid, t = e["victimId"], e["timestamp"]
        timer = death_seconds(1 + bisect_right(levels[pid], t), t) * 1000
        respawn, earliest = t + timer, t + timer * 0.75
        home = FOUNTAIN[ps[pid]["teamId"]]
        for at, where in alive[pid]:
            if earliest < at < respawn:
                respawn = min(respawn, max(earliest, at - math.dist(home, where) / WALK))
        out[pid].spans.append((t, respawn, (e["position"]["x"], e["position"]["y"])))
    for d in out.values():
        d.spans.sort()
        for n in range(len(d.spans) - 1):  # 다음 죽음보다 늦게 부활할 순 없다
            d0, d1, pos = d.spans[n]
            d.spans[n] = (d0, min(d1, d.spans[n + 1][0] - 1000), pos)
    return out


def _attack_range(version: str, champion: str) -> float:
    try:
        return float(_champion_data(version, champion)["stats"]["attackrange"])
    except Exception:
        return 300.0


def _move_speed(version: str, champion: str, t: float) -> float:
    """기본 이동 속도 + 신발(8분 뒤엔 대개 2단계 신발 +45) → 월드 단위/ms."""
    try:
        base = float(_champion_data(version, champion)["stats"]["movespeed"])
    except Exception:
        base = 340.0
    return (base + (45 if t > 8 * 60_000 else 25 if t > 4 * 60_000 else 0)) / 1000


def build_tracks(info: dict, timeline: dict, t0: float, t1: float, center: tuple[float, float],
                 version: str) -> tuple[dict[int, Track], dict[int, Deaths]]:
    """장면 앞뒤 1분 남짓의 증거로 경로를 만든다. 반환: (pid → Track, pid → 죽음·부활)."""
    ps = {p["participantId"]: p for p in info["participants"]}
    paths = deaths(info, timeline)
    events = _events(timeline)
    lo, hi = t0 - 65_000, t1 + 65_000
    hard: dict[int, list] = {pid: [] for pid in ps}  # (t, x, y)
    soft: dict[int, list] = {pid: [] for pid in ps}
    for f in timeline["info"]["frames"]:
        if not lo <= f["timestamp"] <= hi:
            continue
        for pid, pf in f["participantFrames"].items():
            pid = int(pid)
            if pf.get("championStats", {}).get("health", 1) > 0 and not paths[pid].dead_at(f["timestamp"]):
                hard[pid].append((f["timestamp"], pf["position"]["x"], pf["position"]["y"]))
    for pid, path in paths.items():
        for d0, d1, pos in path.spans:
            if lo <= d0 <= hi:
                hard[pid].append((d0, *pos))
            if lo <= d1 <= hi:
                hard[pid].append((d1, *FOUNTAIN[ps[pid]["teamId"]]))

    def last_hard(pid: int, t: float):
        before = [p for p in hard[pid] if p[0] < t]
        return max(before)[1:] if before else FOUNTAIN[ps[pid]["teamId"]]

    for e in events:
        t, kind, pos = e["timestamp"], e["type"], e.get("position")
        if not (lo <= t <= hi and pos):
            continue
        p = (pos["x"], pos["y"])
        if kind == "CHAMPION_KILL":
            for pid in [e.get("killerId"), *e.get("assistingParticipantIds", [])]:
                if pid not in ps or paths[pid].dead_at(t):
                    continue
                # 킬 자리에서 사거리만큼, 오던 쪽으로 물러선 곳
                reach = min(max(_attack_range(version, ps[pid]["championName"]), 150), 600) * 0.85
                src = last_hard(pid, t)
                d = math.dist(src, p)
                if d < 1:
                    src, d = FOUNTAIN[ps[pid]["teamId"]], math.dist(FOUNTAIN[ps[pid]["teamId"]], p)
                k = min(reach, d) / max(d, 1)
                soft[pid].append((t, p[0] + (src[0] - p[0]) * k, p[1] + (src[1] - p[1]) * k))
        elif kind in {"ELITE_MONSTER_KILL", "BUILDING_KILL", "TURRET_PLATE_DESTROYED"}:
            for pid in [e.get("killerId"), *e.get("assistingParticipantIds", [])]:
                if pid in ps and not paths[pid].dead_at(t):
                    src = last_hard(pid, t)
                    d = math.dist(src, p)
                    k = min(350, d) / max(d, 1)
                    soft[pid].append((t, p[0] + (src[0] - p[0]) * k, p[1] + (src[1] - p[1]) * k))
        elif kind in {"ITEM_PURCHASED", "ITEM_SOLD"} and e.get("participantId") in ps:
            pid = e["participantId"]
            if not paths[pid].dead_at(t) and t > 5000 and ps[pid]["championName"] != "Ornn":
                # Ornn forges away from the fountain; purchases are not position observations.
                soft[pid].append((t, *FOUNTAIN[ps[pid]["teamId"]]))

    tracks = {}
    for pid in ps:
        pts = sorted({(t, x, y, True) for t, x, y in hard[pid]} | {(t, x, y, False) for t, x, y in soft[pid]})
        hards = [p for p in pts if p[3]]
        kept: list = []
        for p in pts:
            if kept and p[0] - kept[-1][0] < 300:
                if p[3] and not kept[-1][3]:
                    kept[-1] = p
                continue
            if not p[3]:  # 앞뒤 확실한 점에서 닿을 수 없는 추정점은 버린다
                prev = kept[-1] if kept else None
                nxt = next((h for h in hards if h[0] > p[0]), None)
                if prev and math.dist(prev[1:3], p[1:3]) / (p[0] - prev[0]) > VMAX:
                    continue
                if nxt and math.dist(p[1:3], nxt[1:3]) / max(nxt[0] - p[0], 1) > VMAX:
                    continue
            kept.append(p)
        track = Track(speed=_move_speed(version, ps[pid]["championName"], t0))
        far = all(math.dist(p[1:3], center) > NEAR * 2 for p in kept)
        for a, b in zip(kept, kept[1:]):
            if paths[pid].dead_at((a[0] + b[0]) / 2):
                continue  # 죽어 있는 동안은 지도에 없다 (부활하면 우물에서 다시)
            pa, pb = snap(a[1:3]), snap(b[1:3])
            if far:
                track.legs.append((a[0], b[0], [pa, pb], b[0] - a[0]))
            else:
                track.add(a[0], pa, b[0], pb)
        tracks[pid] = track
    return tracks, paths


# ── 그리기 ──────────────────────────────────────────────
def _rounded_mask(w: int, h: int, radius: int = 14) -> Image.Image:
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=255)
    return mask.resize((w, h), Image.LANCZOS)


def render(match: dict, timeline: dict, friend_puuids: set[str], ddragon: DDragon | None = None,
           highlight: Highlight | None = None, use_models: bool = True) -> bytes | None:
    """친구 최고 교전 GIF. 소환사의 협곡이 아니거나 친구 킬이 없으면 None."""
    info = match["info"]
    if info.get("mapId") != 11:
        return None
    hl = highlight or pick(match, timeline, friend_puuids)
    if hl is None:
        return None
    dd = ddragon or DDragon()
    ps = {p["participantId"]: p for p in info["participants"]}
    star = hl.star
    team_id = ps[star]["teamId"]
    color = lambda team: ALLY if team == team_id else ENEMY
    color_of = lambda pid: color(ps[pid]["teamId"]) if pid in ps else LABEL
    friend = {pid: p["puuid"] in friend_puuids for pid, p in ps.items()}
    events = _events(timeline)
    all_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("position")]
    chain, scene = hl.kills, hl.scene
    t_first, t_last = chain[0]["timestamp"], chain[-1]["timestamp"]
    monsters = [e for e in events if e["type"] == "ELITE_MONSTER_KILL" and e.get("position")
                and t_first - 15000 <= e["timestamp"] <= t_last + 2000]
    T0 = min([t_first - PRE_MS] + [k["timestamp"] - 2500 for k in scene] + [m["timestamp"] - 3500 for m in monsters])
    T0 = max(T0, 0)
    T1 = min(t_last + POST_MS, info["gameDuration"] * 1000)

    champ_name = lambda pid: dd.champion_name(ps[pid]["championName"])
    champ_url = {pid: dd.champion_icon(p["championName"]) for pid, p in ps.items()}
    version = dd.version or "latest"

    # ── 카메라: 교전에서 일어난 킬이 모두 들어오게 ──
    pos = lambda e: (e["position"]["x"], e["position"]["y"])
    mid = (sum(pos(k)[0] for k in chain) / len(chain), sum(pos(k)[1] for k in chain) / len(chain))
    # 주인공 킬 둘레 3000 안의 킬·오브젝트만 화면에 담는다 (멀리 난 킬까지 담으면 너무 작다)
    spots = [pos(k) for k in chain] + [pos(e) for e in scene if math.dist(pos(e), mid) < 3000]
    spots += [pos(m) for m in monsters if math.dist(pos(m), mid) < 1500]
    xs, ys = [p[0] for p in spots], [p[1] for p in spots]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    span = max(max(xs) - min(xs), (max(ys) - min(ys)) * 1.8, 1500)
    width = span * 1.3 + 1400
    fight_cam = Camera((center[0], center[1] + width * 0.04), width / 2 / math.tan(HFOV / 2), PITCH, -8)
    over_cam = Camera((7100, 7400), 19500, 80, 0)

    tracks, paths = build_tracks(info, timeline, T0, T1, center, version)
    terrain = Terrain()
    fight_view = terrain.view(fight_cam)

    # ── 그림 조각 ──
    sprite_cache: dict = {}

    def portrait(pid: int, d: int) -> Image.Image:
        key = (pid, d)
        if key not in sprite_cache:
            ring = HIGHLIGHT if pid == star else color_of(pid)
            sprite_cache[key] = disc(champ_url[pid], d, ring, dot=friend[pid] and pid != star)
        return sprite_cache[key]

    tower_icon = {team: tinted(f"{MINIMAP_ICONS}/tower.png", 18, color(team)) for team in (100, 200)}
    rings = {c: [ring_sprite(10 + 26 * k / 7, c, round(235 * (1 - k / 9))) for k in range(8)] for c in (ALLY, ENEMY, LABEL)}
    feed_size = 24
    feed_icon = {pid: icon(champ_url[pid], feed_size, radius=6) for pid in ps}
    death_mark = {}
    for c in (ALLY, ENEMY):
        im = Image.new("RGBA", (22 * SS, 22 * SS), (0, 0, 0, 0))
        dm = ImageDraw.Draw(im)
        dm.ellipse((0, 0, 22 * SS - 1, 22 * SS - 1), fill=BG + (200,), outline=shade(c, 0.8) + (255,), width=2 * SS)
        r0, r1 = 7 * SS, 15 * SS
        dm.line((r0, r0, r1, r1), fill=WHITE + (230,), width=2 * SS)
        dm.line((r1, r0, r0, r1), fill=WHITE + (230,), width=2 * SS)
        death_mark[c] = im.resize((22, 22), Image.LANCZOS)

    # ── 고정 화면: 원화 바탕 · 머리줄 · 아래 판 ──
    splash = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{ps[star]['championName']}_0.jpg"
    ui = Image.new("RGB", (WIDTH, HEIGHT), BG)
    ambient_bg(ui, splash, dim=AMBIENT_DIM, focus_y=0.4)
    dr = ImageDraw.Draw(ui)
    header(dr, "하이라이트", [(ALLY, "우리 팀"), (ENEMY, "상대 팀")])
    title = headline(hl)
    tx = PAD + dr.textlength("하이라이트", font=font(21, 6)) + 12
    dr.text((tx, 25), f"{nickname(ps[star])} · {title} · {clock(t_first)}", font=font(15), fill=LABEL)

    left = (PAD, PANEL_Y, PAD + 330, PANEL_B)
    right = (PAD + 346, PANEL_Y, WIDTH - PAD, PANEL_B)
    glass(ui, left)
    glass(ui, right)
    sp = icon(champ_url[star], 50, radius=10)
    ui.paste(sp, (left[0] + 16, PANEL_Y + 16), sp)
    dr.rounded_rectangle((left[0] + 14, PANEL_Y + 14, left[0] + 67, PANEL_Y + 67), radius=11, outline=HIGHLIGHT, width=2)
    dr.text((left[0] + 80, PANEL_Y + 20), nickname(ps[star]), font=font(19, 6), fill=WHITE)
    dr.text((left[0] + 80, PANEL_Y + 47), champ_name(star), font=font(15), fill=LABEL)
    slot_d, slot_gap = 40, 12
    slot_y = PANEL_Y + 84
    slots = chain[:6]
    for n in range(len(slots)):
        x = left[0] + 16 + n * (slot_d + slot_gap)
        dr.ellipse((x, slot_y, x + slot_d, slot_y + slot_d), outline=shade(LABEL, 0.5), width=2)

    rows, per_kill = damage_rows(ps[star], chain, version)
    total = {key: sum(acc.get(key, 0) for acc in per_kill) for key, *_ in rows}
    vmax = max(total.values(), default=1) or 1
    rx0 = right[0] + 18
    dr.text((rx0, PANEL_Y + 14), "스킬별 피해", font=font(15), fill=LABEL)
    victims = len({k["victimId"] for k in chain})
    dr.text((right[2] - 18, PANEL_Y + 14), f"처치한 {victims}명에게 준 피해", font=font(14), fill=LABEL, anchor="ra")
    row_y0, row_h = PANEL_Y + 44, 22
    for n, (key, name, url) in enumerate(rows):
        y = row_y0 + n * row_h
        ic = icon(url, 18, radius=4)
        if ic:
            ui.paste(ic, (rx0, y + 1), ic)
        label = "" if key.startswith("S:") or key in ("평타", "기타") else key
        if label:
            dr.text((rx0 + 28, y + 10), label, font=font(14, 6), fill=GOLD, anchor="lm")
        dr.text((rx0 + (50 if label else 28), y + 10), name, font=font(14), fill=WHITE, anchor="lm")
    if not rows:
        dr.text((rx0, row_y0 + 10), "피해 기록 없음", font=font(14), fill=LABEL, anchor="lm")
    bar_x0, bar_x1 = rx0 + 230, right[2] - 84

    view_mask = _rounded_mask(VW, VH)

    # ── 움직이는 것들 ──
    held: dict[int, tuple[int, int]] = {}
    pops: dict = {}

    def pop(name: str, team: int, d: int) -> Image.Image:
        key = (name, team, d)
        if key not in pops:
            pops[key] = disc(f"{MINIMAP_ICONS}/{name}.png", d, color(team), crop=0.8)
        return pops[key]

    def monster_icon(m: dict) -> tuple[str, str]:
        if m["monsterType"] == "DRAGON":
            sub = m.get("monsterSubType", "")
            return DRAGON_ICONS.get(sub, "dragon"), f"{DRAGON_NAMES.get(sub, '')} 드래곤".strip()
        name, ic = MONSTERS.get(m["monsterType"], (m["monsterType"], "dragon"))
        return ic, name

    broken = [b for b in events if b["type"] == "BUILDING_KILL" and b.get("position") and b["timestamp"] <= T1]

    facing: dict[int, float] = {}
    reach = {pid: min(max(_attack_range(version, p["championName"]), 150), 650) + 200 for pid, p in ps.items()}

    def enemy_near(pid: int, at: tuple[float, float], t: float):
        best = None
        for other, track in tracks.items():
            if ps[other]["teamId"] == ps[pid]["teamId"] or paths[other].dead_at(t):
                continue
            q = track.at(t)
            if q and math.dist(q, at) <= reach[pid] and (best is None or math.dist(q, at) < math.dist(best, at)):
                best = q
        return best

    def draw(view: Image.Image, cam: Camera, t: float, intro: bool = False) -> Image.Image:
        im = ui.copy()
        im.paste(view, VIEW[:2], view_mask)
        layer = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)

        # 포탑: 서 있는 것만 팀 색 표시 (그림의 포탑 위에)
        for team, lst in TURRETS.items():
            for x, y in lst:
                gone = any(b["teamId"] == team and b["timestamp"] <= t and math.dist((x, y), (b["position"]["x"], b["position"]["y"])) < 900
                           for b in broken)
                sx, sy, _ = cam.project(x, y, 330)
                if gone or not (-20 < sx < VW + 20 and -20 < sy < VH + 20):
                    continue
                spr = tower_icon[team]
                layer.alpha_composite(spr, (round(sx - spr.width / 2), round(sy - spr.height / 2)))

        for m in monsters:  # 오브젝트: 잡히기 전엔 흐리게, 잡히면 팀 색으로 크게
            age = t - m["timestamp"]
            ic, _ = monster_icon(m)
            taker = m.get("killerTeamId") or ps.get(m.get("killerId"), {}).get("teamId", team_id)
            sx, sy, _ = cam.project(m["position"]["x"], m["position"]["y"], 120)
            if age < 0:
                spr = icon(f"{MINIMAP_ICONS}/{ic}.png", 30, radius=0)
                if spr is None:
                    continue
                spr.putalpha(spr.getchannel("A").point(lambda a: a * 7 // 10))
            elif age < 9000:
                spr = pop(ic, taker, 30 if age < 300 else 36 if age < 600 else 42)
            else:
                continue
            layer.alpha_composite(spr, (round(sx - spr.width / 2), round(sy - spr.height / 2)))

        # 죽은 자리 표시 (몇 초)
        for k in all_kills:
            age = t - k["timestamp"]
            if 600 <= age < 6000 and T0 <= k["timestamp"]:
                sx, sy, _ = cam.project(k["position"]["x"], k["position"]["y"], 0)
                spr = death_mark[color_of(k["victimId"])]
                layer.alpha_composite(spr, (round(sx - 11), round(sy - 11)))

        # 챔피언: 지면 그림자 · 막대 · 초상 (먼 것부터)
        placed = []
        for pid, track in tracks.items():
            dead = paths[pid].dead_at(t)
            if dead and not (use_models and not intro and t - dead[0] < DEATH_MS):
                continue
            p = dead[2] if dead else track.at(t)
            if p is None:
                continue
            gx, gy, _ = cam.project(p[0], p[1], 0)
            if not (-60 < gx < VW + 60 and -60 < gy < VH + 100):
                continue
            s = cam.scale(p[0], p[1]) / fight_cam.scale(*center)
            d = max(18, int(round(CHAMP_D * min(max(s, 0.6), 1.25) / 2) * 2))
            if pid == star:
                d += 6
            px, py, _ = cam.project(p[0], p[1], LIFT)
            placed.append([pid, gx, gy, px, py, d])
        for _ in range(8):  # 초상끼리 겹치면 살짝 민다
            moved = False
            for i in range(len(placed)):
                for j in range(i + 1, len(placed)):
                    a, b = placed[i], placed[j]
                    dx, dy = b[3] - a[3], b[4] - a[4]
                    dist = math.hypot(dx, dy)
                    gap = (a[5] + b[5]) / 2 + 2
                    if dist >= gap:
                        continue
                    if dist < 0.01:
                        ang = (a[0] * 7 + b[0] * 3) % 12 * math.pi / 6
                        dx, dy, dist = math.cos(ang), math.sin(ang), 1.0
                    push = (gap - dist) / 2 * 0.6
                    a[3] -= dx / dist * push
                    a[4] -= dy / dist * push
                    b[3] += dx / dist * push
                    b[4] += dy / dist * push
                    moved = True
            if not moved:
                break
        for item in placed:  # 1px 안쪽 흔들림은 무시: 서 있는 초상은 픽셀이 그대로
            old = held.get(item[0])
            if not intro and old and abs(item[3] - old[0]) < 0.9 and abs(item[4] - old[1]) < 0.9:
                item[3], item[4] = old
            else:
                item[3], item[4] = round(item[3]), round(item[4])
            held[item[0]] = (item[3], item[4])
        placed.sort(key=lambda it: (it[2], it[0] == star))
        for pid, gx, gy, px, py, d in placed:
            model_sprite = None
            if use_models and not intro and ps[pid].get('championId'):
                from imposter_finder.analysis.character_models import sprite
                dead = paths[pid].dead_at(t)
                position = dead[2] if dead else tracks[pid].at(t)
                before = position if dead else tracks[pid].at(max(T0, t - 200)) or position
                dx, dy = position[0]-before[0], position[1]-before[1]
                walking = math.hypot(dx, dy) > 12
                action, phase = None, int(t/100) % 8
                if dead:  # 쓰러지는 모션 (킬 자리에서)
                    action, phase = 'death', min(7, int((t - dead[0]) / DEATH_MS * 8))
                elif walking:
                    facing[pid] = math.degrees(math.atan2(dx, -dy))
                else:  # 멈춰 있으면 가까운 적에게 평타
                    foe = enemy_near(pid, position, t)
                    if foe:
                        facing[pid] = math.degrees(math.atan2(foe[0]-position[0], -(foe[1]-position[1])))
                        action, phase = 'attack', int(((t + pid * 173) % ATTACK_MS) / ATTACK_MS * 8)
                heading = facing.get(pid, 90 if ps[pid]['teamId'] == team_id else -90)
                angle = round((heading - cam.yaw) / 22.5) % 16
                model_sprite = sprite(ps[pid]['championName'], ps[pid]['championId'], angle,
                                      round(cam.pitch/5), phase, walking, action)
            sw, sh = d * 0.7, d * 0.24
            ld.ellipse((gx - sw / 2, gy - sh / 2, gx + sw / 2, gy + sh / 2), fill=(0, 0, 0, 130))
            if model_sprite is not None:
                height = max(32, round(d*1.9))
                model_sprite = model_sprite.resize((height*320//100, height*320//100), Image.Resampling.LANCZOS)
                ratio = height/100
                layer.alpha_composite(model_sprite, (round(gx-160*ratio), round(gy-240*ratio)))
                ld.ellipse((gx-sw/2,gy-sh/2,gx+sw/2,gy+sh/2),outline=color_of(pid)+(230,),width=2)
                # Keep the small portrait for champion recognition above the model.
                spr = portrait(pid, 20 if pid != star else 24)
                layer.alpha_composite(spr,(round(gx-spr.width/2),round(gy-height-spr.height-4)))
            else:
                ld.line((gx, gy, px, py + d / 2 - 1), fill=color_of(pid) + (210,), width=2)
                spr = portrait(pid, d)
                layer.alpha_composite(spr, (round(px - spr.width / 2), round(py - spr.height / 2)))
        pos_of = {it[0]: (it[3], it[4]) for it in placed}
        if star in pos_of:  # 주인공 이름표
            sx, sy = pos_of[star]
            d = next(it[5] for it in placed if it[0] == star)
            name = nickname(ps[star])
            f = font(14, 6)
            w = ld.textlength(name, font=f)
            top = sy - d / 2 - (80 if use_models else 28)
            ld.rounded_rectangle((sx - w / 2 - 9, top, sx + w / 2 + 9, top + 22), radius=11, fill=HIGHLIGHT + (240,))
            ld.text((sx, top + 11), name, font=f, fill=BG, anchor="mm")

        # 킬: 처치자 → 피해자 선 · 땅의 원
        for k in all_kills:
            age = t - k["timestamp"]
            if -450 <= age < 250 and k["killerId"] in pos_of:
                a = pos_of[k["killerId"]]
                b = cam.project(k["position"]["x"], k["position"]["y"], LIFT)[:2]
                ld.line((a[0], a[1], b[0], b[1]), fill=color_of(k["killerId"]) + (235,), width=4)
            if 0 <= age < 2400:
                sx, sy, _ = cam.project(k["position"]["x"], k["position"]["y"], 0)
                c = color_of(k["killerId"]) if k["killerId"] in ps else LABEL
                spr = rings[c][min(7, int(age / 300))]
                layer.alpha_composite(spr, (round(sx - spr.width / 2), round(sy - spr.height / 2)))
        im.paste(layer, VIEW[:2], ImageChops.multiply(layer.getchannel("A"), view_mask))

        d2 = ImageDraw.Draw(im)
        # 화면 위: 시계 · 킬 스코어
        d2.text((VIEW[0] + 18, VIEW[1] + 14), clock(t), font=font(30, 6), fill=WHITE, stroke_width=2, stroke_fill=BG)
        score = {100: 0, 200: 0}
        for k in all_kills:
            if k["timestamp"] > t:
                break
            if k["killerId"] in ps:
                score[ps[k["killerId"]]["teamId"]] += 1
        f30 = font(30, 6)
        x = VIEW[2] - 18
        for text, fill, fnt in ((str(score[300 - team_id]), ENEMY, f30), (":", LABEL, font(24, 4)),
                                (str(score[team_id]), ALLY, f30), ("킬", LABEL, font(15))):
            dy = {":": 2, "킬": 12}.get(text, 0)
            d2.text((x, VIEW[1] + 14 + dy), text, font=fnt, fill=fill, anchor="ra",
                    stroke_width=0 if text in (":", "킬") else 2, stroke_fill=BG)
            x -= d2.textlength(text, font=fnt) + (10 if text != "킬" else 0)

        # 화면 오른쪽 위: 교전 킬 피드 (최근 3개)
        recent = [k for k in all_kills if T0 <= k["timestamp"] <= t][-3:][::-1]
        for n, k in enumerate(recent):
            y = VIEW[1] + 56 + n * 32
            killer, victim = k["killerId"], k["victimId"]
            x1 = VIEW[2] - 14
            x0 = x1 - (feed_size * 2 + 38)
            fresh = t - k["timestamp"] < 2500
            glass(im, (x0 - 54, y - 3, x1 + 4, y + feed_size + 3), radius=8, alpha=34 if fresh else 18)
            d2.text((x0 - 8, y + feed_size / 2), clock(k["timestamp"]), font=font(13), fill=LABEL, anchor="rm")
            if killer in ps:
                im.paste(feed_icon[killer], (x0, y), feed_icon[killer])
                d2.rounded_rectangle((x0 - 1, y - 1, x0 + feed_size, y + feed_size), radius=6, outline=color_of(killer), width=2)
            cx = x0 + feed_size + 19
            d2.line((cx - 7, y + 12, cx + 5, y + 12), fill=LABEL, width=2)
            d2.line((cx + 1, y + 8, cx + 5, y + 12), fill=LABEL, width=2)
            d2.line((cx + 1, y + 16, cx + 5, y + 12), fill=LABEL, width=2)
            vx = x1 - feed_size
            grey = Image.eval(feed_icon[victim].convert("L").convert("RGB"), lambda v: v * 6 // 10)
            im.paste(grey, (vx, y), feed_icon[victim])
            d2.rounded_rectangle((vx - 1, y - 1, vx + feed_size, y + feed_size), radius=6, outline=color_of(victim), width=2)

        # 화면 아래 가운데: 주인공 연속 킬 · 오브젝트
        banner = None
        for n, k in enumerate(chain):
            age = t - k["timestamp"]
            last = n == len(chain) - 1
            if 0 <= age < 1700 or (last and age >= 0):
                solo = not k.get("assistingParticipantIds")
                text = (MULTI[n + 1] if 0 < n < hl.multi else f"{n + 1}번째 킬" if n else "솔로 킬" if solo else "처치")
                if last and hl.ace:
                    text += " · 에이스"
                banner = (text, last and n > 0, ALLY)
        if banner is None:
            for m in monsters:
                if 0 <= t - m["timestamp"] < 2200:
                    taker = m.get("killerTeamId") or ps.get(m.get("killerId"), {}).get("teamId", team_id)
                    banner = (f"{monster_icon(m)[1]} 처치", False, color(taker))
        if banner:
            text, big, c = banner
            size = 44 if big else 32
            f = font(size, 6)
            w = d2.textlength(text, font=f)
            cx, cy = WIDTH / 2, VIEW[3] - 50
            hh = size * 0.78
            d2.rounded_rectangle((cx - w / 2 - 26, cy - hh, cx + w / 2 + 26, cy + hh), radius=int(hh), fill=shade(c, 0.6))
            d2.text((cx, cy), text, font=f, fill=WHITE, anchor="mm")

        # 아래 판: 처치한 챔피언 · 스킬별 피해 (킬마다 늘어난다)
        for n, k in enumerate(slots):
            x = left[0] + 16 + n * (slot_d + slot_gap)
            if t >= k["timestamp"]:
                spr = portrait(k["victimId"], slot_d)
                im.paste(spr, (x, slot_y), spr)
                d2.text((x + slot_d / 2, slot_y + slot_d + 5), clock(k["timestamp"]), font=font(12), fill=LABEL, anchor="ma")
        cur = {key: 0.0 for key, *_ in rows}
        for k, acc in zip(chain, per_kill):
            grow = min(max((t - k["timestamp"]) / 400, 0), 1)
            for key in cur:
                cur[key] += acc.get(key, 0) * grow
        for n, (key, *_) in enumerate(rows):
            y = row_y0 + n * row_h
            bar(im, (bar_x0, y + 7, bar_x1, y + 13), TRACK, dim_side=None)
            if cur[key] > 0:
                bar(im, (bar_x0, y + 7, bar_x0 + (bar_x1 - bar_x0) * cur[key] / vmax, y + 13), GOLD if n == 0 else ALLY)
            d2.text((right[2] - 18, y + 10), f"{round(cur[key]):,}", font=font(15, 6), fill=WHITE if cur[key] else LABEL,
                    anchor="rm")
        return im

    # ── 장면 시각: 전체 지도에서 다가간 뒤, 교전 앞은 빠르게 · 킬 앞뒤는 느리게 ──
    marks = [k["timestamp"] for k in chain]
    times: list[tuple[float, float | None]] = [(T0, n / (INTRO - 1)) for n in range(INTRO)]
    t = T0
    while t < T1:
        times.append((t, None))
        slow = any(-SLOW_BEFORE <= t - m <= SLOW_AFTER for m in marks)
        calm = t < t_first - 2500 and not any(abs(t - k["timestamp"]) < 1500 for k in scene)
        t += STEP_SLOW if slow else STEP_CALM if calm else STEP_FIGHT
    times.append((T1, None))

    frames = []
    for t, k in times:
        if k is None or k >= 1:
            frames.append(draw(fight_view, fight_cam, t))
        else:
            cam = blend_camera(over_cam, fight_cam, k)
            frames.append(draw(terrain.view(cam), cam, t, intro=True))
    return _encode(frames, fight_view, ui)


def _encode(frames: list[Image.Image], view: Image.Image, ui: Image.Image) -> bytes:
    """팔레트 하나 · 바탕은 점 섞기 · 안 바뀐 픽셀은 바탕 그대로. 용량을 넘으면 장면을 고르게 줄인다."""
    base = frames[INTRO]
    sample = Image.new("RGB", (WIDTH, HEIGHT * 3))
    for n, k in enumerate((INTRO + (len(frames) - INTRO) // 3, INTRO + (len(frames) - INTRO) * 2 // 3, len(frames) - 1)):
        sample.paste(frames[k], (0, HEIGHT * n))
    palette = shared_palette([(view, 120), (sample, 72), (frames[INTRO // 2], 40)], [ALLY, ENEMY, WHITE, GOLD, LABEL, HIGHLIGHT])
    base_idx = base.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
    indexed = []
    for n, im in enumerate(frames):
        if n < INTRO:
            indexed.append(im.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG))
            continue
        q = im.quantize(palette=palette, dither=Image.Dither.NONE)
        r, g, b = ImageChops.difference(im, base).split()
        same = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v == 0 else 0)
        q.paste(base_idx, mask=same)
        indexed.append(q)
    keep = list(range(len(indexed)))
    while True:
        chosen = [indexed[n] for n in keep]
        durations = [FRAME_MS] * len(chosen)
        durations[0], durations[-1] = FIRST_MS, LAST_MS
        if len(keep) < len(indexed):  # 빠진 장면만큼 앞 장면을 길게: 재생 속도는 그대로
            durations = [FRAME_MS * (b - a) for a, b in zip(keep, keep[1:])] + [LAST_MS]
            durations[0] = max(durations[0], FIRST_MS)
        out = BytesIO()
        chosen[0].save(out, "GIF", save_all=True, append_images=chosen[1:], duration=durations, loop=0, optimize=True)
        data = out.getvalue()
        if len(data) <= GIF_MAX_BYTES or len(keep) < 40:
            return data
        # 다가가는 장면은 남기고, 나머지에서 고르게 덜어 낸다
        ratio = GIF_MAX_BYTES / len(data) * 0.92
        body = keep[INTRO:-1]
        count = max(10, int(len(body) * ratio))
        body = [body[round(i * (len(body) - 1) / (count - 1))] for i in range(count)]
        keep = keep[:INTRO] + sorted(set(body)) + [keep[-1]]


def build_highlight(match: dict, timeline: dict | None, friends: dict, ddragon: DDragon,
                    accent: int) -> tuple[dict, list[tuple]] | None:
    """리포트 뒤에 따로 보내는 하이라이트 메시지. 그릴 장면이 없으면 None."""
    if not timeline or match["info"].get("mapId") != 11:
        return None
    friend_ids = set(friends)
    hl = pick(match, timeline, friend_ids)
    if hl is None:
        return None
    data = render(match, timeline, friend_ids, ddragon, highlight=hl)
    if not data or len(data) > GIF_MAX_BYTES:
        return None
    star = {p["participantId"]: p for p in match["info"]["participants"]}[hl.star]
    who = friends[star["puuid"]].name if star["puuid"] in friends else nickname(star)
    payload = {"flags": 32768, "allowed_mentions": {"parse": []}, "components": [
        {"type": 17, "accent_color": accent, "components": [
            {"type": 10, "content": f"**하이라이트** · {who} {headline(hl)}"},
            {"type": 12, "items": [{"media": {"url": "attachment://highlight.gif"}}]}]}]}
    return payload, [("highlight.gif", data, "image/gif")]
