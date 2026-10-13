"""배그 베스트 킬 하이라이트: 친구가 한 가장 멋진 킬 하나를 텔레메트리 x·y·z로 3D 장면으로 다시 그린다.

고르기: 친구가 사람(봇 제외)을 기절시키거나 바로 처치한 순간마다 점수 = 거리 + 헤드샷 + 높이 차,
        같은 사람이 30초 안에 이어서 잡으면 한 장면으로 묶어 더 높게 친다 (10초 안이면 연속 킬).
지형:   공식 지도 그림(색) + 이 경기 모든 사람의 발 높이로 추정한 땅 높이 + 지도 색으로 찾은 지붕.
        공개된 높이 지도가 없어 사람이 다닌 곳 근처만 정확하다.
그리기: 세로선이 기울지 않는 카메라(건축 사진처럼 렌즈만 내림)라 화면 세로줄 하나가 땅 위 한 직선과 같다.
        그래서 줄마다 앞에서 뒤로 높이를 훑어 가려짐을 한 번에 푼다 (옛 복셀 지형 방식, numpy).
        사람·총알·글자는 Pillow. 카메라는 소개 장면에서만 움직이고 교전 동안은 멈춘다 (GIF는 바뀐 픽셀만 저장).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from imposter_finder.analysis.cards import GIF_MAX_BYTES, font
from imposter_finder.analysis.pubg_report import (BG, DIM, KILLFEED, MUTED, RED, TEXT, YELLOW, _clock, _panel, _tint,
                                                  _white, teko)
from imposter_finder.games.pubg_telemetry import MAPS, map_crop_hi, map_image, weapon_icon_url, weapon_name

CHAIN_GAP, MULTI_GAP = 30.0, 10.0  # 같은 사람이 이 시간 안에 또 잡으면 한 장면, 더 짧으면 연속 킬 (초)
LEAD, TAIL, MAX_SPAN = 9.0, 3.5, 48.0  # 첫 킬 전·마지막 킬 뒤로 보여 줄 시간, 장면 최대 길이 (초)
W, H = 1000, 560  # 경로 GIF와 같은 크기
FRAME_MS, LAST_MS = 80, 4500
INTRO = 14  # 소개 장면 (카메라가 움직이는 유일한 구간)
MIN_DIST = 230.0  # 지도 원본이 1m/px라 너무 다가가면 흐려진다 (가운데에서 1m ≈ 5.5px)
EXAG = 1.0  # 실제 높이 비율: 추정 지형의 경사를 과장하지 않는다
HUMAN_Z = 0.9  # 텔레메트리 캐릭터 높이는 발보다 이만큼 위 (m)
TEX = 1024  # 지형 판 텍스처 한 변 (px)
FOG = (28, 30, 34)
ENEMY_RED = (232, 84, 70)
SQUAD_ORANGE = (255, 140, 40)
ROOF_H = 7.0  # m
WHITE_ROOFS = {"Desert_Main", "Tiger_Main", "Kiki_Main", "Summerland_Main"}


# ── 고르기 ───────────────────────────────────────────────
@dataclass
class Play:
    t: float
    shooter: str
    victim: str
    kind: str  # knock | kill
    weapon: str
    distance: float  # m
    headshot: bool
    height: float  # 쏜 사람이 높으면 + (m)
    score: float = 0.0


@dataclass
class Highlight:
    shooter: str
    plays: list[Play]  # 연속 킬 묶음 (시간 순)
    best: Play
    score: float


def _when(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _is_ai(char: dict | None) -> bool:
    char = char or {}
    return str(char.get("accountId", "")).startswith("ai.") or char.get("type") == "ai"


def _z(char: dict | None) -> float:
    return ((char or {}).get("location") or {}).get("z", 0) / 100


def play_score(p: Play) -> float:
    return 1 + min(p.distance, 300) / 60 + (1.5 if p.headshot else 0) + min(max(p.height, 0), 30) / 10 \
        + (0.3 if p.kind == "kill" else 0)


def find_plays(telemetry: list[dict[str, Any]], friend_names: set[str]) -> list[Play]:
    """친구가 사람을 기절시킨 순간 + 기절 없이 바로 처치한 순간 (같은 팀·봇 제외)."""
    start = next((e for e in telemetry if e["_T"] == "LogMatchStart"), None)
    if not start:
        return []
    t0 = _when(start["_D"])
    lower = {n.casefold() for n in friend_names}
    team = {c["character"]["teamId"] for c in start["characters"] if c["character"]["name"].casefold() in lower}
    friend = lambda c: bool(c) and str(c.get("name", "")).casefold() in lower
    enemy = lambda c: bool(c) and c.get("teamId") not in team and not _is_ai(c)
    plays = []
    for e in telemetry:
        kind = e["_T"]
        if kind == "LogPlayerMakeGroggy":
            a, v = e.get("attacker"), e.get("victim")
            if friend(a) and enemy(v):
                plays.append(Play((_when(e["_D"]) - t0).total_seconds(), a["name"], v["name"], "knock",
                                  e.get("damageCauserName", ""), e.get("distance", 0) / 100,
                                  e.get("damageReason") == "HeadShot", _z(a) - _z(v)))
        elif kind == "LogPlayerKillV2":
            killer, v = e.get("killer") or e.get("finisher"), e.get("victim")
            maker = e.get("dBNOMaker") or {}
            if friend(killer) and enemy(v) and maker.get("teamId") not in team:
                info = e.get("killerDamageInfo") or e.get("finishDamageInfo") or {}
                plays.append(Play((_when(e["_D"]) - t0).total_seconds(), killer["name"], v["name"], "kill",
                                  info.get("damageCauserName", ""), info.get("distance", 0) / 100,
                                  info.get("damageReason") == "HeadShot", _z(killer) - _z(v)))
    for p in plays:
        p.score = play_score(p)
    return plays


def pick(telemetry: list[dict[str, Any]], friend_names: set[str]) -> Highlight | None:
    """연속 킬 묶음마다 (가장 멋진 한 방 + 묶음 크기) 점수. 친구가 사람을 못 잡았으면 None."""
    best = None
    plays = find_plays(telemetry, friend_names)
    for shooter in sorted({p.shooter for p in plays}):
        mine = sorted((p for p in plays if p.shooter == shooter), key=lambda p: p.t)
        groups: list[list[Play]] = []
        for p in mine:
            if groups and p.t - groups[-1][-1].t <= CHAIN_GAP and p.t - groups[-1][0].t <= MAX_SPAN - LEAD - TAIL:
                groups[-1].append(p)
            else:
                groups.append([p])
        for g in groups:
            top = max(g, key=lambda p: p.score)
            score = top.score + sum(1.5 if b.t - a.t <= MULTI_GAP else 0.8 for a, b in zip(g, g[1:]))
            if best is None or score > best.score:
                best = Highlight(shooter, g, top, score)
    return best


# ── 장면 자료 ─────────────────────────────────────────────
@dataclass
class Actor:
    name: str
    team: int
    ours: bool
    friend: bool
    track: list[tuple[float, float, float, float]] = field(default_factory=list)  # (초, x, y, z) m
    health: list[tuple[float, float]] = field(default_factory=list)
    knocked: list[float] = field(default_factory=list)
    revived: list[float] = field(default_factory=list)
    died: float | None = None


@dataclass
class Scene:
    start: float
    end: float
    actors: dict[str, Actor]
    shots: list[dict]  # 맞은 총알 {t, a, v, head, dmg}
    feed: list[dict]  # 기절·처치 {t, kind, a, v, weapon, dist, head}
    ground_pts: list[tuple[float, float, float]]  # 땅 높이 추정용 발 위치 (m)


def _scene(telemetry: list[dict[str, Any]], hl: Highlight, friend_names: set[str]) -> Scene:
    start = next(e for e in telemetry if e["_T"] == "LogMatchStart")
    t0 = _when(start["_D"])
    lower = {n.casefold() for n in friend_names}
    team = next(c["character"]["teamId"] for c in start["characters"] if c["character"]["name"].casefold() == hl.shooter.casefold())
    s, e_ = hl.plays[0].t - LEAD, hl.plays[-1].t + TAIL
    s = max(s, e_ - MAX_SPAN)
    timed = []
    for e in telemetry:
        if "_D" in e:
            timed.append(((_when(e["_D"]) - t0).total_seconds(), e))
    # 우리 팀 + 이 시간에 우리 팀과 총을 주고받은 사람만 등장
    names = {c["character"]["name"] for c in start["characters"] if c["character"]["teamId"] == team}
    for t, e in timed:
        if s - 6 <= t <= e_ and e["_T"] in ("LogPlayerTakeDamage", "LogPlayerMakeGroggy", "LogPlayerKillV2"):
            a = e.get("attacker") or e.get("killer") or e.get("finisher") or {}
            v = e.get("victim") or {}
            if a.get("name") in names or v.get("name") in names:
                names |= {n for n in (a.get("name"), v.get("name")) if n}
    names |= {p.victim for p in hl.plays}
    actors: dict[str, Actor] = {}
    landed: dict[str, float] = {}
    ground: list[tuple[float, float, float]] = []
    shots, feed = [], []
    for t, e in timed:
        kind = e["_T"]
        if kind == "LogParachuteLanding":
            c = e.get("character") or {}
            landed.setdefault(c.get("accountId", ""), t)
        for key in ("character", "attacker", "victim", "killer", "finisher", "dBNOMaker", "reviver"):
            c = e.get(key)
            if not isinstance(c, dict) or not (c.get("location") or {}).get("x"):
                continue
            loc = c["location"]
            x, y, z = loc["x"] / 100, loc["y"] / 100, loc["z"] / 100
            acc = c.get("accountId", "")
            if kind == "LogPlayerPosition" and not c.get("isInVehicle") and acc in landed and t > landed[acc] \
                    and e.get("elapsedTime", 1) > 0:
                ground.append((x, y, z - HUMAN_Z))
            name = c.get("name")
            if name in names and s - 25 <= t <= e_ + 12:
                if name not in actors:
                    actors[name] = Actor(name, c.get("teamId", -1), c.get("teamId") == team, name.casefold() in lower)
                actors[name].track.append((t, x, y, z))
                if "health" in c and key not in ("killer", "finisher") and not (kind == "LogPlayerKillV2" and key == "victim"):
                    actors[name].health.append((t, float(c["health"])))
        if not s - 30 <= t <= e_ + 12:
            continue
        a, v = e.get("attacker") or {}, e.get("victim") or {}
        if kind == "LogPlayerTakeDamage" and a.get("name") in names and v.get("name") in names \
                and a["name"] != v["name"] and e.get("damageTypeCategory") == "Damage_Gun" and e.get("damage", 0) > 0:
            shots.append({"t": t, "a": a["name"], "v": v["name"], "head": e.get("damageReason") == "HeadShot",
                          "dmg": e.get("damage", 0)})
        elif kind == "LogPlayerMakeGroggy" and v.get("name") in names:
            feed.append({"t": t, "kind": "knock", "a": a.get("name"), "v": v["name"], "weapon": e.get("damageCauserName", ""),
                         "dist": e.get("distance", 0) / 100, "head": e.get("damageReason") == "HeadShot"})
            if v["name"] in actors:
                actors[v["name"]].knocked.append(t)
        elif kind == "LogPlayerRevive" and v.get("name") in actors:
            actors[v["name"]].revived.append(t)
        elif kind == "LogPlayerKillV2" and v.get("name") in names:
            killer = e.get("finisher") or e.get("killer") or {}
            info = e.get("finishDamageInfo") or e.get("killerDamageInfo") or {}
            feed.append({"t": t, "kind": "kill", "a": killer.get("name"), "v": v["name"],
                         "weapon": info.get("damageCauserName", ""), "dist": info.get("distance", 0) / 100,
                         "head": info.get("damageReason") == "HeadShot"})
            if v["name"] in actors:
                actors[v["name"]].died = t
    for actor in actors.values():
        actor.track.sort()
        actor.health.sort()
    # 같은 순간 산탄 여러 알은 한 발로
    seen, uniq = set(), []
    for shot in shots:
        key = (round(shot["t"], 1), shot["a"], shot["v"])
        if key not in seen:
            seen.add(key)
            uniq.append(shot)
    return Scene(s, e_, actors, uniq, sorted(feed, key=lambda f: f["t"]), ground)


def _pos(actor: Actor, t: float) -> tuple[float, float, float] | None:
    """보간한 위치를 ±0.6초로 평균: 띄엄띄엄한 기록 사이 꺾임을 부드럽게."""
    tr = actor.track
    if not tr:
        return None
    if actor.died is not None:
        t = min(t, actor.died)

    def raw(tt: float):
        if tt <= tr[0][0]:
            return tr[0][1:]
        if tt >= tr[-1][0]:
            return tr[-1][1:]
        lo, hi = 0, len(tr) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if tr[mid][0] <= tt else (lo, mid)
        (ta, *pa), (tb, *pb) = tr[lo], tr[hi]
        f = 0 if tb == ta else (tt - ta) / (tb - ta)
        return tuple(p + (q - p) * f for p, q in zip(pa, pb))

    pts = [raw(t + dt) for dt in (-0.6, -0.3, 0, 0.3, 0.6)]
    return tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))


def _state(actor: Actor, t: float) -> str:
    if actor.died is not None and actor.died <= t:
        return "dead"
    last_knock = max((k for k in actor.knocked if k <= t), default=None)
    if last_knock is not None and not any(last_knock < r <= t for r in actor.revived):
        return "knocked"
    return "alive"


def _health(actor: Actor, t: float) -> float | None:
    hp = None
    for tt, v in actor.health:
        if tt > t:
            break
        hp = v
    if _state(actor, t) == "dead":
        return 0
    return hp


# ── 지형 ────────────────────────────────────────────────
def _blur(a, r: int):
    """상자 흐림 세 번 ≈ 가우스 (numpy, 가장자리는 늘려 채움)."""
    import numpy as np
    if r < 1:
        return a
    out = a.astype(np.float32)
    for _ in range(3):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (r + 1, r)
            c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis, dtype=np.float64)
            hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
            lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
            out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def _pull_push(z, w):
    """빈 칸을 주변 값으로 채운다: 반씩 줄이며 평균을 모았다가(pull) 다시 키우며 빈 곳만 채움(push)."""
    import numpy as np
    n = z.shape[0]
    if n <= 1:
        return np.where(w > 0, z, 0.0)
    zw = (z * w).reshape(n // 2, 2, n // 2, 2).sum(axis=(1, 3))
    w2 = w.reshape(n // 2, 2, n // 2, 2).sum(axis=(1, 3))
    coarse = _pull_push(np.where(w2 > 0, zw / np.maximum(w2, 1e-9), 0.0), np.minimum(w2, 1.0))
    up = np.asarray(Image.fromarray(coarse.astype(np.float32), "F").resize((n, n), Image.BILINEAR))
    a = np.minimum(w, 1.0)
    return z * a + up * (1 - a)


class Terrain:
    """지형 판: 가운데 (cx, cy), 한 변 2·half (m). 높이·색은 TEX×TEX 격자."""

    def __init__(self, map_key: str, cx: float, cy: float, half: float, ground_pts: list[tuple[float, float, float]]):
        import numpy as np
        self.cx, self.cy, self.half = cx, cy, half
        self.step = 2 * half / TEX
        name, size_cm = MAPS.get(map_key, ("Erangel", 816000))
        box_cm = tuple(v * 100 for v in (cx - half, cy - half, cx + half, cy + half))
        try:
            crop = map_crop_hi(name, size_cm, box_cm)
        except Exception as exc:  # 고해상도를 못 받으면 캐시된 4096px
            print(f"[pubg] highlight hi-res map unavailable ({exc}); using 4096px")
            im = map_image(name)
            k = im.width / size_cm
            crop = im.crop(tuple(round(v * k) for v in box_cm))
        tex = crop.resize((TEX, TEX), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 70, 2))
        col = np.asarray(tex, dtype=np.float32)

        # 땅 높이: 발 위치를 4m 칸에 모아 낮은 값(2층·지붕 위 사람 제외) → 빈 칸 채우기 → 부드럽게
        G, pad = 128, 40.0
        span = 2 * (half + pad)
        zgrid, wgrid = np.zeros((G, G), np.float32), np.zeros((G, G), np.float32)
        pts = np.array([p for p in ground_pts if abs(p[0] - cx) < half + pad and abs(p[1] - cy) < half + pad]
                       or [(cx, cy, 0.0)], np.float32)
        lo_z, hi_z = np.percentile(pts[:, 2], 2) - 30, np.percentile(pts[:, 2], 90) + 40  # 낙하산·비행기 높이 제외
        pts = pts[(pts[:, 2] >= lo_z) & (pts[:, 2] <= hi_z)]
        gi = np.clip(((pts[:, 0] - cx + half + pad) / span * G).astype(int), 0, G - 1)
        gj = np.clip(((pts[:, 1] - cy + half + pad) / span * G).astype(int), 0, G - 1)
        cell = gj * G + gi
        order = np.argsort(cell, kind="stable")
        cell, zs = cell[order], pts[order, 2]
        bounds = np.flatnonzero(np.diff(cell)) + 1
        for c, group in zip(cell[np.r_[0, bounds]], np.split(zs, bounds)):
            zgrid.flat[c] = np.percentile(group, 20)
            wgrid.flat[c] = min(1.0, len(group) / 2)
        # 이웃 칸과 너무 다른 칸(비행기·글라이더·높은 건물 위)은 버린다
        near = np.full((25, G, G), np.nan, np.float32)
        masked = np.where(wgrid > 0, zgrid, np.nan)
        for n, (dj, di) in enumerate((dj, di) for dj in range(-2, 3) for di in range(-2, 3)):
            near[n] = np.roll(np.roll(masked, dj, axis=0), di, axis=1)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                local = np.nanmedian(near, axis=0)
        wgrid[(wgrid > 0) & (np.abs(zgrid - local) > 12)] = 0
        kept = zgrid[wgrid > 0]
        base = float(np.median(kept)) if kept.size else float(np.median(pts[:, 2]))
        filled = _pull_push(np.where(wgrid > 0, zgrid, base), wgrid)
        filled = _blur(filled, 2)
        lo, hi = (float(kept.min()) - 3, float(kept.max()) + 3) if kept.size else (base - 3, base + 3)
        filled = np.clip(filled, lo, hi)
        # 판 부분만 TEX로 키운다
        inner = filled.astype(np.float32)
        a = pad / span * G
        ground = Image.fromarray(inner, "F").resize((TEX, TEX), Image.BICUBIC, box=(a, a, G - a, G - a))
        hg = np.asarray(ground, dtype=np.float32)

        # 지붕: 옅은 노랑 (에란겔·사녹·비켄디) 또는 흰 회색 (미라마·태이고·데스턴·카라킨).
        # 흰 길·눈과 헷갈리지 않게 지도마다 한 가지만, 3m보다 가는 것(길 가장자리·짚단)은 지운다
        r, g, b = col[..., 0], col[..., 1], col[..., 2]
        if map_key in WHITE_ROOFS:
            roof = (r > 178) & (g > 178) & (b > 170) & (col.max(axis=2) - col.min(axis=2) < 28)
        else:
            roof = (r > 165) & (g > 145) & (b < 160) & (r - b > 40) & (np.abs(r - g) < 50)
        m = Image.fromarray((roof * 255).astype(np.uint8))
        px_m = 1 / self.step
        size = max(3, int(px_m * 3) | 1)
        m = m.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))  # 잡티 제거
        m = m.filter(ImageFilter.GaussianBlur(px_m * 0.8))  # 가장자리를 매끈하게
        roof = np.asarray(m) > 127
        dense = _blur(roof.astype(np.float32), int(18 * px_m))
        roof &= dense < 0.75  # 넓게 다 밝은 곳(눈밭·모래)은 지붕이 아니다
        flat = _blur(hg, int(10 * px_m))
        height = np.where(roof, flat + ROOF_H, hg)  # 층수는 알 수 없어 지붕 높이는 하나로
        self.roof = roof
        self.h = height.astype(np.float32)  # 지붕 포함 (m, 과장 전)
        self.hg = hg

        # 빛: 지도 그림자처럼 북서쪽 위에서. 기복 음영 + 그림자 + 오목한 곳·건물 밑 어둡게
        zz = self.h * EXAG
        dzdx = np.gradient(zz, self.step, axis=1)
        dzdy = np.gradient(zz, self.step, axis=0)  # 지도 y는 남쪽으로
        L = np.array([-0.55, 0.5, 0.67])
        L /= np.linalg.norm(L)
        nx, ny, nz = -dzdx, dzdy, np.ones_like(zz)
        lam = (nx * L[0] + ny * L[1] + nz * L[2]) / np.sqrt(nx ** 2 + ny ** 2 + 1)
        shadow = self._shadow(zz, L)
        cavity = np.clip((_blur(self.h, int(6 * px_m)) - self.h) * 0.06, 0, 0.25)
        near = _blur(roof.astype(np.float32), int(2.5 * px_m)) * ~roof
        ao = 1 - cavity - 0.45 * near
        light = (0.66 + 0.5 * np.clip(lam, 0, 1) * shadow) * ao
        rgb = col * light[..., None]
        inner = _blur(roof.astype(np.float32), max(1, int(0.8 * px_m)))
        rgb[roof] = rgb[roof] * (0.8 + 0.25 * inner[roof])[:, None] + 10
        self.rgb = np.clip(rgb, 0, 255).astype(np.float32)
        # 벽 색: 지붕 색을 어둡게, 벽이 빛을 보는 정도 (지붕 가장자리 기울기 방향)
        soft = _blur(self.h, max(1, int(1.2 * px_m)))
        gx, gy = np.gradient(soft, axis=1), -np.gradient(soft, axis=0)
        norm = np.sqrt(gx ** 2 + gy ** 2) + 1e-6
        self.wall_lit = np.clip(0.55 + 0.45 * (-(gx * L[0] + gy * L[1]) / norm), 0.35, 1.0).astype(np.float32)
        self.zmin, self.zmax = float(self.h.min()), float(self.h.max())
        self.bottom = self.zmin - 10

    def _shadow(self, zz, L):
        """지형·건물이 드리우는 그림자: 칸마다 빛 쪽으로 걸어가며 더 높은 곳이 있는지 (512 격자)."""
        import numpy as np
        n = 512
        z = np.asarray(Image.fromarray(zz.astype(np.float32), "F").resize((n, n), Image.BILINEAR))
        step = 2 * self.half / n
        lit = np.ones((n, n), np.float32)
        jj, ii = np.mgrid[0:n, 0:n].astype(np.float32)
        horiz = math.hypot(L[0], L[1])
        di, dj = L[0] / horiz, -L[1] / horiz  # 지도 칸 방향 (y는 남쪽)
        rise = L[2] / horiz * step
        for k in range(1, 48):
            si = np.clip((ii + di * k).astype(int), 0, n - 1)
            sj = np.clip((jj + dj * k).astype(int), 0, n - 1)
            lit = np.minimum(lit, np.where(z[sj, si] > z + rise * k + 0.4, 0.0, 1.0))
        lit = _blur(lit, 1)
        return np.asarray(Image.fromarray(lit.astype(np.float32), "F").resize((TEX, TEX), Image.BILINEAR))

    def ground(self, x: float, y: float) -> float:
        i = int(min(max((x - self.cx + self.half) / self.step, 0), TEX - 1))
        j = int(min(max((y - self.cy + self.half) / self.step, 0), TEX - 1))
        return float(self.hg[j, i])

    def inside(self, x: float, y: float, margin: float = 0) -> bool:
        return abs(x - self.cx) <= self.half - margin and abs(y - self.cy) <= self.half - margin


# ── 카메라·그리기 ─────────────────────────────────────────
class Camera:
    """세로선이 서 있는 카메라. 지도 좌표 (x 동쪽, y 남쪽, z 위)를 그대로 쓴다.

    yaw: 바라보는 방향 (지도에서 0 = 북쪽, 시계 방향), pitch: 내려다보는 각, dist: 목표까지 거리.
    """

    def __init__(self, target: tuple[float, float, float], yaw: float, pitch: float, dist: float, fov: float = 42.0,
                 scale: int = 1):
        self.w, self.h = W * scale, H * scale
        yr, pr = math.radians(yaw), math.radians(pitch)
        self.fx, self.fy = math.sin(yr), -math.cos(yr)  # 지도 좌표의 앞 (y가 남쪽이라 북쪽은 -y)
        self.rx, self.ry = -self.fy, self.fx  # 오른쪽 (지도에서 앞을 시계 방향 90도)
        tx, ty, tz = target
        self.cx = tx - self.fx * dist * math.cos(pr)
        self.cy = ty - self.fy * dist * math.cos(pr)
        self.cz = tz * EXAG + dist * math.sin(pr)
        self.focal = (self.w / 2) / math.tan(math.radians(fov) / 2)
        self.v0 = self.h / 2 - self.focal * math.tan(pr)  # 수평선이 있는 화면 줄 (보통 화면 위 바깥)

    def project(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        dx, dy = x - self.cx, y - self.cy
        fwd = dx * self.fx + dy * self.fy
        fwd = max(fwd, 0.1)
        side = dx * self.rx + dy * self.ry
        return self.w / 2 + self.focal * side / fwd, self.v0 - self.focal * (z * EXAG - self.cz) / fwd, fwd


def _background():
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot((xx - W / 2) / W, (yy - H * 0.42) / H)
    g = np.clip(1 - r * 1.25, 0, 1)[..., None]
    top, bot = np.array([38, 42, 48]), np.array(BG)
    return (bot + (top - bot) * g).astype(np.float32)


def render_terrain(terrain: Terrain, cam: Camera):
    """화면 세로줄마다 땅 위 직선을 앞에서 뒤로 훑는다: 높이를 화면 줄로 바꿔 누적 최소값 = 가려짐.

    돌려주는 것: (RGB 그림, 픽셀마다 앞 거리 — 사람·총알 가려짐 판정용)
    """
    import numpy as np
    w, h = cam.w, cam.h
    a = (np.arange(w) + 0.5 - w / 2) / cam.focal  # 줄마다 옆으로 벌어지는 정도
    dx, dy = cam.fx + a * cam.rx, cam.fy + a * cam.ry  # 앞으로 1 갈 때 지도 위 이동
    x0, x1 = terrain.cx - terrain.half, terrain.cx + terrain.half
    y0, y1 = terrain.cy - terrain.half, terrain.cy + terrain.half
    # 판 사각형에 들어오고 나가는 앞 거리 (줄마다)
    with np.errstate(divide="ignore", invalid="ignore"):
        tx0, tx1 = (x0 - cam.cx) / dx, (x1 - cam.cx) / dx
        ty0, ty1 = (y0 - cam.cy) / dy, (y1 - cam.cy) / dy
    t_in = np.maximum(np.minimum(tx0, tx1), np.minimum(ty0, ty1))
    t_out = np.minimum(np.maximum(tx0, tx1), np.maximum(ty0, ty1))
    t_in = np.maximum(t_in, 1.0)
    hit_cols = t_out > t_in
    t_in, t_out = np.where(hit_cols, t_in, 1.0), np.where(hit_cols, t_out, 2.0)
    K = int(np.ceil((t_out - t_in).max() / 0.4)) + 1
    s = np.linspace(0, 1, K, dtype=np.float32)[:, None]
    T = t_in[None, :] + (t_out - t_in)[None, :] * s  # (K, w)
    X, Y = cam.cx + T * dx[None, :], cam.cy + T * dy[None, :]
    step = terrain.step
    I = np.clip(((X - x0) / step).astype(np.int32), 0, TEX - 1)
    J = np.clip(((Y - y0) / step).astype(np.int32), 0, TEX - 1)
    Z = terrain.h[J, I] * EXAG
    V = cam.v0 - cam.focal * (Z - cam.cz) / T  # 그 땅이 보이는 화면 줄
    M = np.minimum.accumulate(V, axis=0)  # 앞에 있는 것보다 더 높이 올라온 곳만 보인다
    rows = np.arange(h, dtype=np.float32) + 0.5
    k = np.empty((h, w), np.int32)
    for c in range(w):  # 줄마다: 화면 줄 → 처음 덮는 칸
        k[:, c] = np.searchsorted(-M[:, c], -rows, side="left")
    valid = (k < K) & hit_cols[None, :]
    k = np.minimum(k, K - 1)
    cols = np.broadcast_to(np.arange(w), (h, w))
    # 판 앞 옆면 아래로 지나가는 줄은 바닥
    v_bottom = cam.v0 - cam.focal * (terrain.bottom * EXAG - cam.cz) / t_in
    valid &= rows[:, None] < v_bottom[None, :]
    t = T[k, cols]
    # 칸 사이를 선형으로: 이 줄이 땅에 닿는 앞 거리 (벽이면 그대로)
    kp = np.maximum(k - 1, 0)
    vp, vk = V[kp, cols], V[k, cols]
    frac = np.clip((vp - rows[:, None]) / np.maximum(vp - vk, 1e-3), 0, 1)
    t = np.where(k > 0, T[kp, cols] + (t - T[kp, cols]) * frac, t)
    px, py = cam.cx + t * dx[None, :], cam.cy + t * dy[None, :]
    u = np.clip((px - x0) / step - 0.5, 0, TEX - 1.001)
    v = np.clip((py - y0) / step - 0.5, 0, TEX - 1.001)
    i0, j0 = u.astype(np.int32), v.astype(np.int32)
    fu, fv = (u - i0)[..., None], (v - j0)[..., None]
    rgb = terrain.rgb
    color = (rgb[j0, i0] * (1 - fu) * (1 - fv) + rgb[j0, i0 + 1] * fu * (1 - fv)
             + rgb[j0 + 1, i0] * (1 - fu) * fv + rgb[j0 + 1, i0 + 1] * fu * fv)
    # 벽: 이 줄의 시선 높이가 그 자리 지붕보다 낮다 = 세로면
    z_ray = (cam.cz - (rows[:, None] - cam.v0) * t / cam.focal) / EXAG
    surf = terrain.h[J[k, cols], I[k, cols]]
    gap = surf - z_ray
    side = valid & (k == 0) & (gap > 0.05)  # 판 앞 옆면
    wall = valid & ~side & (gap > 0.6)
    if wall.any():
        lit = terrain.wall_lit[J[k, cols], I[k, cols]][wall]
        down = np.clip(gap[wall] / 8, 0, 1)
        floor = 1 - 0.12 * ((gap[wall] % 3.0) < 0.35)  # 층마다 가는 줄
        base = np.array([150, 143, 128], np.float32)
        color[wall] = base * (lit * (1 - 0.4 * down) * floor)[:, None]
    if side.any():
        depth = np.clip(gap[side] / max(1.0, terrain.zmax - terrain.bottom), 0, 1)
        stripe = 1 - 0.1 * ((gap[side] // 3) % 2)
        color[side] = np.array([58, 62, 68], np.float32)[None, :] * ((1 - 0.5 * depth) * stripe)[:, None]
        edge = side & (gap < 0.5)
        color[edge] = np.array([150, 150, 145], np.float32)
    # 먼 곳은 안개
    fog = np.clip((t - t.min()) / max(1.0, float(np.percentile(t[valid], 99)) - float(t.min())) if valid.any() else 0, 0, 1)
    fog = (fog ** 1.6 * 0.5)[..., None]
    color = color * (1 - fog) + np.array(FOG, np.float32) * fog
    back = _background() if (w, h) == (W, H) else np.asarray(Image.fromarray(_background().astype(np.uint8)).resize((w, h)),
                                                               np.float32)
    out = np.where(valid[..., None], color, back)
    depth = np.where(valid, t, np.inf).astype(np.float32)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), depth


# ── 장면 그리기 ───────────────────────────────────────────
SS = 2


def _weapon(causer: str) -> str:
    name = weapon_name(causer)
    return "주먹" if name.startswith("Player") else name


def _label(name: str, names: dict[str, str]) -> str:
    return names.get(name.casefold(), name)


class Painter:
    def __init__(self, scene: Scene, hl: Highlight, terrain: Terrain, names: dict[str, str], colors: dict[str, tuple],
                 map_ko: str):
        self.scene, self.hl, self.terrain = scene, hl, terrain
        self.names = {k.casefold(): v for k, v in names.items()}
        # 스쿼드 빨강은 적(빨강)과 헷갈려 주황으로
        self.colors = {k.casefold(): (SQUAD_ORANGE if v == RED else v) for k, v in colors.items()}
        self.map_ko = map_ko
        self.death = _white(f"{KILLFEED}/Death.png", 15)
        self.groggy = _white(f"{KILLFEED}/Groggy.png", 15)
        self.captions = self.banners()

    def color(self, name: str) -> tuple:
        actor = self.scene.actors.get(name)
        if actor and actor.ours:
            return self.colors.get(name.casefold(), TEXT)
        return ENEMY_RED

    def label(self, name: str) -> str:
        return _label(name, self.names)

    def overlay(self, base: Image.Image, depth, cam: Camera, t: float, fx: dict) -> Image.Image:
        img = base.copy()
        layer = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        terrain, scene = self.terrain, self.scene

        def P(x, y, z):
            sx, sy, _ = cam.project(x, y, z)
            return sx * SS, sy * SS

        def seen(x, y, z) -> float:
            sx, sy, fwd = cam.project(x, y, z)
            ix, iy = int(sx), int(sy)
            if not (0 <= ix < W and 0 <= iy < H):
                return 1.0
            return 1.0 if fwd <= depth[iy, ix] + 6 else 0.4

        actors = [a for a in scene.actors.values() if a.track]
        # 지나온 길 (최근 10초, 발 높이)
        for actor in actors:
            if _state(actor, t) == "dead" and not actor.ours:
                continue
            pts = []
            for k in range(26):
                tt = t - 10 + k * 0.4
                if tt < scene.start - 4:
                    continue
                p = _pos(actor, tt)
                if p and terrain.inside(p[0], p[1]):
                    pts.append(P(p[0], p[1], p[2] - HUMAN_Z + 0.15))
            col = self.color(actor.name)
            for i, (p0, p1) in enumerate(zip(pts, pts[1:])):
                al = int(30 + 170 * (i + 1) / len(pts))
                d.line(p0 + p1, fill=col + (al,), width=3 * SS)

        # 총알: 쏜 사람 손 → 맞은 곳, 머리가 날아가고 꼬리가 사라진다
        flight = 0.12
        for shot in scene.shots:
            age = t - shot["t"]
            if not -flight <= age <= 1.5:
                continue
            a_, v_ = scene.actors.get(shot["a"]), scene.actors.get(shot["v"])
            pa, pv = (_pos(a_, shot["t"]) if a_ else None), (_pos(v_, shot["t"]) if v_ else None)
            if not pa or not pv:
                continue
            A = (pa[0], pa[1], pa[2] + 0.5)
            B = (pv[0], pv[1], pv[2] + (0.8 if shot["head"] else 0.3))
            head = 1.0 if age >= 0 else 1 + age / flight
            tail = max(0.0, min(head, (age - 0.2) / 1.1)) if age > 0 else max(0.0, head - 0.5)
            pa_ = tuple(A[i] + (B[i] - A[i]) * tail for i in range(3))
            pb_ = tuple(A[i] + (B[i] - A[i]) * head for i in range(3))
            hero = shot["a"] == self.hl.shooter
            ours = scene.actors[shot["a"]].ours
            glow = (255, 214, 120) if ours else (255, 110, 90)
            fade = 1.0 if age < 0.2 else max(0.0, 1 - (age - 0.2) / 1.3)
            w = (3 if hero else 2) * SS
            d.line(P(*pa_) + P(*pb_), fill=glow + (int(70 * fade),), width=w * 4)
            d.line(P(*pa_) + P(*pb_), fill=glow + (int(200 * fade),), width=w * 2)
            d.line(P(*pa_) + P(*pb_), fill=(255, 255, 255, int(255 * fade)), width=max(SS, w // 2))
            if 0 <= age <= 0.6:  # 맞은 자리
                cx, cy = P(*B)
                r = (8 + 34 * age) * SS * (1.5 if shot["head"] else 1)
                ring = YELLOW if shot["head"] else (255, 70, 50)
                d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ring + (int(255 * (1 - age / 0.6)),), width=3 * SS)

        # 높이 차 (최고의 한 방)
        best = self.hl.best
        if fx.get("height", 0) > 0 and best.height >= 4:
            sa, sv = scene.actors.get(best.shooter), scene.actors.get(best.victim)
            pa, pv = (_pos(sa, best.t) if sa else None), (_pos(sv, best.t) if sv else None)
            if pa and pv:
                al = int(255 * fx["height"])
                top, foot, far = P(*pa), P(pa[0], pa[1], pv[2]), P(*pv)
                for seg_a, seg_b, alpha in ((top, foot, al), (foot, far, al // 2)):
                    for i in range(14):
                        f0, f1 = i / 14, (i + 0.5) / 14
                        d.line((seg_a[0] + (seg_b[0] - seg_a[0]) * f0, seg_a[1] + (seg_b[1] - seg_a[1]) * f0,
                                seg_a[0] + (seg_b[0] - seg_a[0]) * f1, seg_a[1] + (seg_b[1] - seg_a[1]) * f1),
                               fill=(255, 255, 255, alpha), width=2 * SS)

        # 사람: 땅 그림자 + 막대 + 머리
        people = []
        for actor in actors:
            p = _pos(actor, t)
            if not p or not terrain.inside(p[0], p[1], 1):
                continue
            st = _state(actor, t)
            x, y, z = p
            foot_z = z - HUMAN_Z
            stand = 2.4 if st == "alive" else 0.9
            people.append((cam.project(x, y, z)[2], actor, st, (x, y, foot_z), stand, seen(x, y, foot_z + stand)))
        people.sort(key=lambda q: -q[0])
        for _, actor, st, (x, y, fz), stand, vis in people:
            col = self.color(actor.name) if st != "dead" else (120, 120, 120)
            hero = actor.name == self.hl.shooter
            al = int(255 * vis)
            gx, gy = P(x, y, fz)
            d.ellipse((gx - 9 * SS, gy - 3.5 * SS, gx + 9 * SS, gy + 3.5 * SS), fill=(0, 0, 0, int(120 * vis)))
            hx, hy = P(x, y, fz + stand)
            d.line((gx, gy, hx, hy), fill=(0, 0, 0, int(200 * vis)), width=5 * SS)
            d.line((gx, gy, hx, hy), fill=col + (al,), width=3 * SS)
            r = (10 if hero else 7) * SS
            if hero and st == "alive":
                halo = r + 5 * SS
                d.ellipse((hx - halo, hy - halo, hx + halo, hy + halo), fill=col + (int(60 * vis),))
            d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=col + (al,), outline=(255, 255, 255, al), width=2 * SS)
            if st == "knocked":
                d.ellipse((hx - r * 0.45, hy - r * 0.45, hx + r * 0.45, hy + r * 0.45), fill=(20, 20, 20, al))
        small = layer.reduce(SS)
        img.paste(small, (0, 0), small)

        if fx.get("height", 0) > 0.05 and best.height >= 4:
            sa, sv = scene.actors.get(best.shooter), scene.actors.get(best.victim)
            pa, pv = (_pos(sa, best.t) if sa else None), (_pos(sv, best.t) if sv else None)
            if pa and pv:
                mx, my, _ = cam.project(pa[0], pa[1], (pa[2] + pv[2]) / 2)
                k = fx["height"]
                text = f"{best.height:+.0f}m"
                tw = ImageDraw.Draw(img).textlength(text, font=teko(30))
                _panel(img, (mx + 8, my - 16, mx + 28 + tw, my + 16), radius=8, alpha=int(200 * k))
                lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(lay).text((mx + 18 + tw / 2, my + 2), text, font=teko(30),
                                         fill=TEXT + (int(255 * k),), anchor="mm")
                img.paste(lay, (0, 0), lay)

        # 이름표 + 체력
        dr = ImageDraw.Draw(img)
        placed: list[tuple[float, float, float]] = []
        for _, actor, st, (x, y, fz), stand, vis in sorted(people, key=lambda q: (q[1].name != self.hl.shooter, -q[0])):
            if st == "dead" and not actor.ours and t - actor.died > 4:  # 오래전에 죽은 적은 이름표를 치운다
                continue
            hx, hy, _ = cam.project(x, y, fz + stand)
            name = self.label(actor.name)
            f = font(15, 6) if actor.ours else font(13, 4)
            icon = self.death if st == "dead" else self.groggy if st == "knocked" else None
            tw = dr.textlength(name, font=f)
            iw = icon.width + 5 if icon else 0
            bw = tw + 18 + iw
            ty = hy - 18
            for _ in range(8):  # 겹치면 위로
                if not any(abs(hx - px) < (bw + pw) / 2 + 4 and abs(ty - py) < 26 for px, py, pw in placed):
                    break
                ty -= 26
            if ty != hy - 18:
                dr.line((hx, hy - 10, hx, ty + 2), fill=(230, 230, 230), width=1)
            placed.append((hx, ty, bw))
            alpha = 190 if vis > 0.5 else 120
            _panel(img, (hx - bw / 2, ty - 24, hx + bw / 2, ty), radius=7, alpha=alpha)
            colr = self.color(actor.name) if st == "alive" else MUTED
            dr.text((hx - bw / 2 + 9 + iw, ty - 12), name, font=f, fill=colr, anchor="lm")
            if icon:
                ic = _tint(icon, RED) if st == "dead" else icon
                img.paste(ic, (round(hx - bw / 2 + 9), round(ty - 12 - ic.height / 2)), ic)
            hp = _health(actor, t)
            if hp is not None and st != "dead":
                bx0, bx1 = hx - bw / 2 + 6, hx + bw / 2 - 6
                dr.rectangle((bx0, ty - 3, bx1, ty - 2), fill=(60, 60, 60))
                if hp > 0:
                    dr.rectangle((bx0, ty - 3, bx0 + (bx1 - bx0) * min(hp, 100) / 100, ty - 2),
                                 fill=(255, 255, 255) if hp > 25 else RED)
        return img

    # ── HUD ──
    def hud(self, img: Image.Image, t: float, slow: bool) -> None:
        d = ImageDraw.Draw(img)
        who = self.label(self.hl.shooter)
        title_w = 128 + d.textlength(f"{self.map_ko} · {who}", font=font(16, 4)) + 20
        _panel(img, (20, 20, 20 + title_w, 64), radius=12, alpha=170)
        d.text((38, 42), "베스트 킬", font=font(20, 6), fill=TEXT, anchor="lm")
        d.text((138, 42), f"{self.map_ko} · {who}", font=font(16, 4), fill=MUTED, anchor="lm")
        _clock(img, W - 150, t)
        if slow:
            _panel(img, (W - 236, 21, W - 160, 53), radius=10, alpha=200, color=(60, 42, 0))
            d.text((W - 198, 38), "SLOW", font=teko(26), fill=YELLOW, anchor="mm")
        self._feed(img, t)
        self._banner(img, t)

    def _feed(self, img: Image.Image, t: float) -> None:
        d = ImageDraw.Draw(img)
        items = [f for f in self.scene.feed if self.scene.start - 1 <= f["t"] <= t and f["a"]]
        y = H - 20
        for item in reversed(items[-3:]):
            age = t - item["t"]
            icon = self.death if item["kind"] == "kill" else self.groggy
            wico = _white(weapon_icon_url(item["weapon"]), 18) if item["weapon"] else None
            fa, fv, fe = font(15, 6), font(15, 4), font(13, 4)
            a, v = self.label(item["a"]), self.label(item["v"])
            extra = ("헤드샷 · " if item["head"] else "") + f"{item['dist']:.0f}m"
            wtxt = None if wico else (_weapon(item["weapon"]) if item["weapon"] else None)
            ww = wico.width if wico else (d.textlength(wtxt, font=teko(24)) if wtxt else 0)
            wa, wv, we = d.textlength(a, font=fa), d.textlength(v, font=fv), d.textlength(extra, font=fe)
            total = 14 + wa + 10 + ww + 8 + (icon.width if icon else 0) + 8 + wv + 12 + we + 14
            x0 = W - 20 - total
            hero = item["a"] == self.hl.shooter
            _panel(img, (x0, y - 32, W - 20, y), radius=8, alpha=200, color=(72, 52, 0) if hero else (0, 0, 0))
            if age < 0.25:  # 새 줄은 잠깐 밝게
                _panel(img, (x0, y - 32, W - 20, y), radius=8, alpha=int(90 * (1 - age / 0.25)), color=YELLOW)
            cx = x0 + 14
            d.text((cx, y - 16), a, font=fa, fill=self.color(item["a"]), anchor="lm")
            cx += wa + 10
            if wico:
                img.paste(wico, (round(cx), round(y - 16 - wico.height / 2)), wico)
                cx += ww + 8
            elif wtxt:
                d.text((cx, y - 14), wtxt, font=teko(24), fill=TEXT, anchor="lm")
                cx += ww + 8
            if icon:
                ic = _tint(icon, RED if item["kind"] == "kill" else TEXT)
                img.paste(ic, (round(cx), round(y - 16 - ic.height / 2)), ic)
                cx += icon.width + 8
            d.text((cx, y - 16), v, font=fv, fill=TEXT, anchor="lm")
            cx += wv + 12
            d.text((cx, y - 16), extra, font=fe, fill=YELLOW if item["head"] else MUTED, anchor="lm")
            y -= 38

    def banners(self) -> list[dict]:
        """큰 자막: 친구의 연속 킬마다 + 그 사이 우리 팀이 죽은 순간 + 바로 복수."""
        out = []
        deaths = {}
        for f in self.scene.feed:
            victim = self.scene.actors.get(f["v"])
            if f["kind"] == "kill" and victim and victim.ours and self.scene.start <= f["t"] <= self.scene.end:
                out.append({"t": f["t"], "dur": 1.6, "big": f"{self.label(f['v'])} 사망", "color": RED,
                            "sub": f"{_weapon(f['weapon'])} · {f['dist']:.0f}m" if f["weapon"] else ""})
                if f["a"]:
                    deaths[f["a"]] = f["t"]
        streak = 0
        for n, p in enumerate(self.hl.plays, 1):
            prev = self.hl.plays[n - 2] if n >= 2 else None
            streak = streak + 1 if prev and p.t - prev.t <= MULTI_GAP else 1
            sub = [_weapon(p.weapon), f"{p.distance:.0f}m"]
            if p.height >= 4:
                sub.append(f"{p.height:.0f}m 위에서")
            if p.victim in deaths and 0 < p.t - deaths[p.victim] <= 12:
                big, latin = "바로 복수", False
                sub = [_weapon(p.weapon), f"{p.t - deaths[p.victim]:.1f}초 만에"]
            elif streak >= 2:
                big, latin = {2: "더블 킬", 3: "트리플 킬"}.get(streak, f"{streak}연속 킬"), False
            elif p.headshot:
                big, latin = "HEADSHOT", True
            elif p.distance >= 100:
                big, latin = f"{p.distance:.0f}m 저격", False
            else:
                big, latin = ("기절" if p.kind == "knock" else "처치"), False
            if p.headshot and big != "HEADSHOT":
                sub.insert(1, "헤드샷")
            out.append({"t": p.t, "dur": 3.2, "big": big, "latin": latin, "color": YELLOW, "sub": " · ".join(sub)})
        return sorted(out, key=lambda b: b["t"])

    def _banner(self, img: Image.Image, t: float) -> None:
        current = None
        for b in self.captions:
            if b["t"] - 0.05 <= t <= b["t"] + b["dur"]:
                current = b
        if not current:
            return
        al = min(1.0, (t - current["t"] + 0.05) / 0.15, (current["t"] + current["dur"] - t) / 0.4)
        if al <= 0:
            return
        pop = 1 + 0.12 * max(0.0, 1 - (t - current["t"]) / 0.25)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        fb = teko(round(64 * pop)) if current.get("latin") else font(round(40 * pop), 6)
        tw = ld.textlength(current["big"], font=fb)
        sub = current.get("sub", "")
        sw = ld.textlength(sub, font=font(17, 4)) if sub else 0
        bw = max(tw, sw) + 64
        cy = 118
        ld.rounded_rectangle((W / 2 - bw / 2, cy - 38, W / 2 + bw / 2, cy + (46 if sub else 36)), radius=14,
                             fill=(0, 0, 0, int(180 * al)))
        ld.rectangle((W / 2 - bw / 2 + 14, cy - 38, W / 2 + bw / 2 - 14, cy - 35), fill=current["color"] + (int(255 * al),))
        ld.text((W / 2, cy + (3 if current.get("latin") else 0)), current["big"], font=fb,
                fill=current["color"] + (int(255 * al),), anchor="mm")
        if sub:
            ld.text((W / 2, cy + 31), sub, font=font(17, 4), fill=TEXT + (int(255 * al),), anchor="mm")
        img.paste(layer, (0, 0), layer)

    def end_card(self, last: Image.Image) -> Image.Image:
        hl, scene = self.hl, self.scene
        img = Image.blend(last, Image.new("RGB", (W, H), BG), 0.5)
        d = ImageDraw.Draw(img)
        best = hl.best
        stats: list[tuple[str, str, str]] = [("거리", f"{best.distance:.0f}", "m")]
        if len(hl.plays) >= 2:
            stats.append(("연속으로 잡음", str(len(hl.plays)), "명"))
        if best.height >= 3:
            stats.append(("높이 차", f"+{best.height:.0f}", "m"))
        heads = sum(p.headshot for p in hl.plays)
        if heads:
            stats.append(("헤드샷", str(heads), "번"))
        hits = sum(1 for s in scene.shots if s["a"] == hl.shooter and hl.plays[0].t - 8 <= s["t"] <= hl.plays[-1].t)
        if hits:
            stats.append(("맞힌 총알", str(hits), "발"))
        stats = stats[:3]
        bw, bh = 600, 210
        x0, y0 = (W - bw) / 2, (H - bh) / 2 - 6
        _panel(img, (x0, y0, x0 + bw, y0 + bh), radius=16, alpha=220)
        d.text((W / 2, y0 + 40), f"{self.label(hl.shooter)}의 베스트 킬", font=font(26, 6), fill=YELLOW, anchor="mm")
        for i, (lab, val, unit) in enumerate(stats):
            cx = x0 + bw * (2 * i + 1) / (2 * len(stats))
            fu = font(20, 6)
            vw, uw = d.textlength(val, font=teko(56)), d.textlength(unit, font=fu)
            left = cx - (vw + uw + 4) / 2
            d.text((left, y0 + 106), val, font=teko(56), fill=TEXT, anchor="ls")
            d.text((left + vw + 4, y0 + 100), unit, font=fu, fill=MUTED, anchor="ls")
            d.text((cx, y0 + 134), lab, font=font(15, 4), fill=MUTED, anchor="mm")
        verbs = {"knock": "기절", "kill": "처치"}
        line = f"{_weapon(best.weapon)}{'로' if best.weapon else ''} " \
               f"{'헤드샷 ' if best.headshot else ''}{verbs[best.kind]}"
        if len(hl.plays) >= 2:
            line += f" · {hl.plays[-1].t - hl.plays[0].t:.0f}초 동안 {len(hl.plays)}명"
        d.text((W / 2, y0 + 176), line, font=font(15, 4), fill=MUTED, anchor="mm")
        return img


# ── 시간표·카메라 ─────────────────────────────────────────
def _schedule(scene: Scene, hl: Highlight) -> list[tuple[float, bool]]:
    """(경기 시각, 느리게?) 장면 목록: 결정적 순간은 0.25배, 교전 중엔 1배, 사이는 빠르게."""
    key = [p.t for p in hl.plays]
    busy = [f["t"] for f in scene.feed] + [s["t"] for s in scene.shots if s["a"] == hl.shooter or s["v"] == hl.shooter]

    def speed(t: float) -> tuple[float, bool]:
        if any(-0.35 <= t - k <= 0.7 for k in key):
            return 0.25, True
        if any(abs(t - b) <= 1.6 for b in busy):
            return 1.0, False
        return 3.0, False

    out, t = [], scene.start
    while t < scene.end:
        sp, slow = speed(t)
        out.append((t, slow))
        t += sp * FRAME_MS / 1000
    return out


def _framing(scene: Scene, hl: Highlight):
    """지형 판 가운데·크기, 카메라 방향, 화면에 꼭 들어올 지점들. 쏜 사람 → 맞은 사람이 왼쪽 → 오른쪽으로 보이게."""
    key_pts = []
    shooter = scene.actors[hl.shooter]
    for p in hl.plays:
        for name in (p.shooter, p.victim):
            if name in scene.actors and (q := _pos(scene.actors[name], p.t)):
                key_pts.append(q)
    for tt in range(int(scene.start), int(scene.end) + 1, 2):
        if q := _pos(shooter, tt):
            key_pts.append(q)
    here = _pos(shooter, hl.best.t)
    for actor in scene.actors.values():  # 쏜 사람 곁에 있던 팀원
        q = _pos(actor, hl.best.t)
        if actor.ours and q and here and math.hypot(q[0] - here[0], q[1] - here[1]) < 80:
            key_pts.append(q)
    for f in scene.feed:
        if scene.start <= f["t"] <= scene.end:
            for name in (f["a"], f["v"]):
                if name in scene.actors and (q := _pos(scene.actors[name], f["t"])):
                    key_pts.append(q)
    xs, ys = [p[0] for p in key_pts], [p[1] for p in key_pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 30)
    half = min(max(extent * 0.8 + 45, 150), 230)
    best = hl.best
    pa, pv = _pos(shooter, best.t), _pos(scene.actors[best.victim], best.t) if best.victim in scene.actors else None
    if pa and pv and math.hypot(pv[0] - pa[0], pv[1] - pa[1]) > 3:
        ang = math.degrees(math.atan2(pv[0] - pa[0], -(pv[1] - pa[1])))  # 쏜 방향 (북쪽 0, 시계 방향)
        yaw = ang - 90  # 옆에서: 쏜 사람이 왼쪽, 맞은 사람이 오른쪽
    else:
        yaw = 0.0
    return (cx, cy), half, yaw, key_pts


def _fit(target, yaw: float, pitch: float, pts):
    """주요 지점(발·머리)이 제목·킬피드를 피해 화면 안에 다 들어오게: 목표를 옮기고 거리를 맞춘다."""
    box = (110, 200, W - 110, H - 105)  # 들어와야 할 화면 구역 (위는 이름표 자리)
    marks = [(x, y, z - HUMAN_Z) for x, y, z in pts] + [(x, y, z + 1.6) for x, y, z in pts]
    tz = target[2]

    def bounds(tx: float, ty: float, dist: float):
        cam = Camera((tx, ty, tz), yaw, pitch, dist)
        xy = [cam.project(*p)[:2] for p in marks]
        xs, ys = [p[0] for p in xy], [p[1] for p in xy]
        return (min(xs) + max(xs)) / 2 - (box[0] + box[2]) / 2, (min(ys) + max(ys)) / 2 - (box[1] + box[3]) / 2, \
            max(xs) - min(xs) <= box[2] - box[0] and max(ys) - min(ys) <= box[3] - box[1]

    tx, ty = target[:2]
    dist = 60.0
    for _ in range(8):
        while not bounds(tx, ty, dist)[2] and dist < 3000:
            dist *= 1.08
        # 화면 가운데 어긋남을 목표 이동으로 (1m씩 움직여 본 기울기로 2×2 풀기)
        ex, ey, _ = bounds(tx, ty, dist)
        if abs(ex) < 3 and abs(ey) < 3:
            break
        ax, ay, _ = bounds(tx + 1, ty, dist)
        bx, by, _ = bounds(tx, ty + 1, dist)
        j11, j12, j21, j22 = ax - ex, bx - ex, ay - ey, by - ey
        det = j11 * j22 - j12 * j21
        if abs(det) < 1e-9:
            break
        tx -= 0.9 * (j22 * ex - j12 * ey) / det
        ty -= 0.9 * (-j21 * ex + j11 * ey) / det
    return (tx, ty, tz), max(dist, MIN_DIST)


def _best_yaw(terrain: Terrain, target, yaw: float, pitch: float, dist: float, pts) -> float:
    """언덕에 가려지는 주요 지점이 가장 적은 방향 (쏜 방향 옆에서 보는 각도들 중)."""
    def hidden(y: float) -> int:
        cam = Camera(target, y, pitch, dist)
        count = 0
        for x, yy, z in pts:
            for f in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
                px, py = cam.cx + (x - cam.cx) * f, cam.cy + (yy - cam.cy) * f
                if not terrain.inside(px, py):
                    continue
                pz = cam.cz + (z * EXAG - cam.cz) * f
                i = int((px - terrain.cx + terrain.half) / terrain.step)
                j = int((py - terrain.cy + terrain.half) / terrain.step)
                if terrain.h[min(j, TEX - 1), min(i, TEX - 1)] * EXAG > pz:
                    count += 1
                    break
        return count
    options = [yaw + d for d in (0, 15, -15, 30, -30)]
    return min(options, key=lambda y: (hidden(y), options.index(y)))


# ── GIF ─────────────────────────────────────────────────
def _encode(frames: list[Image.Image], durations: list[int], intro: int, accents: list[tuple]) -> bytes:
    """한 팔레트: 지형 색 + 사람·총알·글자 색. 소개 장면은 통째로, 카메라가 멈춘 뒤는 바뀐 픽셀만."""
    sample = Image.new("RGB", (W, H * 3))
    sample.paste(frames[0], (0, 0))
    sample.paste(frames[min(len(frames) - 1, intro + 30)], (0, H))
    sample.paste(frames[-1], (0, 2 * H))
    ui = [YELLOW, ENEMY_RED, RED, TEXT, MUTED, DIM, (255, 214, 120), (255, 110, 90), (255, 70, 50), (72, 52, 0),
          (60, 42, 0), (255, 255, 255), (0, 0, 0), (120, 120, 120), (60, 60, 60), *accents]
    core = list(dict.fromkeys([*accents, ENEMY_RED, RED, YELLOW, TEXT, MUTED, (255, 255, 255), (0, 0, 0), (120, 120, 120)]))
    colors = []
    for c in ui:
        for back in (BG, (90, 100, 110)):
            for k in (0.4, 0.75, 1.0):
                colors.append(tuple(round(b * (1 - k) + v * k) for v, b in zip(c, back)))
    colors = list(dict.fromkeys(colors))[:96]
    terr = sample.quantize(colors=255 - len(colors), method=Image.Quantize.MEDIANCUT)
    tp = terr.getpalette()[:(255 - len(colors)) * 3]
    colors += [tuple(tp[i:i + 3]) for i in range(0, len(tp), 3)]
    pal = Image.new("P", (1, 1))
    pal.putpalette([v for c in colors[:255] for v in c])
    out_frames, prev_rgb, prev_q = [], None, None
    for i, f in enumerate(frames):
        if i < intro:  # 카메라가 움직이는 장면: 장면마다 지형 96색 + 표시 색 (적은 색이 훨씬 작게 저장된다)
            own = f.quantize(colors=96, method=Image.Quantize.MEDIANCUT).getpalette()[:96 * 3]
            local = Image.new("P", (1, 1))
            local.putpalette(own + [v for c in core for v in c])
            fq = f.quantize(palette=local, dither=Image.Dither.NONE)
        elif i == intro or i == len(frames) - 1 or prev_rgb is None:
            fq = f.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
        else:
            r, g, b = ImageChops.difference(f, prev_rgb).split()
            diff = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
            fq = prev_q.copy()
            fq.paste(f.quantize(palette=pal, dither=Image.Dither.NONE), (0, 0), diff)
        out_frames.append(fq)
        prev_rgb, prev_q = f, fq
    buf = BytesIO()
    out_frames[0].save(buf, "GIF", save_all=True, append_images=out_frames[1:], loop=0, optimize=True,
                       duration=durations)
    return buf.getvalue()


def render(match: dict[str, Any], telemetry: list[dict[str, Any]], names: dict[str, str], colors: dict[str, tuple],
           map_ko: str) -> bytes | None:
    """친구의 베스트 킬 GIF. names: 배그 닉네임 → 보여 줄 이름 (친구), colors: 닉네임 → 스쿼드 색.
    친구가 사람을 못 잡았으면 None."""
    friend_names = set(names)
    hl = pick(telemetry, friend_names)
    if hl is None:
        return None
    scene = _scene(telemetry, hl, friend_names)
    if hl.shooter not in scene.actors:
        return None
    (cx, cy), half, yaw, key_pts = _framing(scene, hl)
    pitch = 58.0
    target = (cx, cy, sum(p[2] for p in key_pts) / len(key_pts) - HUMAN_Z)
    (tx, ty, _), first = _fit(target, yaw, pitch, key_pts)
    half = min(max(half, first * 0.85), 330)  # 소개 장면에서도 판 가장자리가 덜 보이게
    terrain = Terrain(match["data"]["attributes"].get("mapName", ""), tx, ty, half, scene.ground_pts)
    yaw = _best_yaw(terrain, (tx, ty, target[2]), yaw, pitch, first, [(x, y, z + 1.0) for x, y, z in key_pts])
    target, dist = _fit(target, yaw, pitch, key_pts)
    painter = Painter(scene, hl, terrain, names, colors, map_ko)
    frames, durations = [], []
    for i in range(INTRO):  # 멀리 높은 곳에서 돌아 들어오며 내려앉는다
        e = i / (INTRO - 1)
        e = e * e * (3 - 2 * e)
        cam = Camera(target, yaw, pitch + 5 * (1 - e), dist * (1 + 0.16 * (1 - e)))
        base, depth = render_terrain(terrain, cam)
        img = painter.overlay(base, depth, cam, scene.start, {})
        painter.hud(img, scene.start, False)
        frames.append(img)
        durations.append(80)
    cam = Camera(target, yaw, pitch, dist)
    base, depth = render_terrain(terrain, cam)
    best = hl.best
    for t, slow in _schedule(scene, hl):
        k = 0.0 if t < best.t else min(1.0, (t - best.t) / 0.3, max(0.0, (best.t + 6 - t) / 0.6))
        img = painter.overlay(base, depth, cam, t, {"height": k})
        painter.hud(img, t, slow)
        frames.append(img)
        durations.append(FRAME_MS)
    durations[-1] = 900
    frames.append(painter.end_card(frames[-1]))
    durations.append(LAST_MS)
    accents = list(dict.fromkeys(painter.colors.values()))
    data = _encode(frames, durations, INTRO, accents)
    if len(data) > GIF_MAX_BYTES:  # 너무 크면 소개 장면을 한 장 걸러, 그래도 크면 빼고
        keep = list(range(0, INTRO, 2))
        data = _encode([frames[i] for i in keep] + frames[INTRO:], [160] * len(keep) + durations[INTRO:], len(keep),
                       accents)
    if len(data) > GIF_MAX_BYTES:
        data = _encode(frames[INTRO:], durations[INTRO:], 0, accents)
    return data if len(data) <= GIF_MAX_BYTES else None
