"""FC 온라인 베스트 골 3D 리플레이 GIF (Pillow 소프트웨어 3D).

넥슨 API가 주는 건 슈팅 위치·종류·결과와 도움 위치뿐이다. 공의 궤적, 골문 안 위치, 다른 선수와
골키퍼의 움직임은 슈팅 종류에 맞게 그럴듯하게 지어낸다 (같은 골은 늘 같은 장면).
평면(잔디·관중석·광고판)은 투시 변환으로 텍스처를 붙이고, 골대·그물·선수·공은 먼 것부터 그린다.
위: 두 팀 점수판 · 가운데: 3D 화면 · 아래: 득점자 카드와 작은 슈팅맵 (fc_flow와 같은 중계 톤).
"""
from __future__ import annotations

import math
import random
import re
from functools import lru_cache
from io import BytesIO
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFilter

from imposter_finder import monitoring
from imposter_finder.analysis.cards import GIF_MAX_BYTES, font, icon
from imposter_finder.analysis.fc_report import (ACCENT, BG, LABEL, SHOT_GOAL, WHITE, Friend, Team, _face, _glass_box,
                                                _half_pitch, _stage, _teams, goals, minute_of)
from imposter_finder.analysis.fc_xg import FREE_KICK, HEADER, PENALTY, geometry, shot_label, shot_xg
from imposter_finder.games.fconline import FcMeta, FcTeamColors, face_image

W, PAD = 800, 24
TOP = 72  # 점수판
VX, VY, VW, VH = PAD, TOP, W - PAD * 2, 423  # 3D 화면 (16:9)
PANEL_Y, PANEL_H = VY + VH + 14, 96  # 득점자 카드
H = PANEL_Y + PANEL_H + 20
S = 2  # 3D 화면은 두 배로 그려 줄인다
NEAR = 0.3
GOLD = (240, 180, 41)
LAST_MS = 3500

# 슈팅 종류 → 장면. 4·10·12·13·14(미상)는 일반 슈팅처럼
KINDS = {PENALTY: "penalty", FREE_KICK: "freekick", HEADER: "header", 7: "volley", 2: "curl", 6: "low"}
SPEED = {"penalty": 25, "freekick": 24, "header": 14, "volley": 29, "curl": 23, "low": 26, "shot": 29}  # m/s
BONUS = {"freekick": .8, "volley": .5, "curl": .4, "header": .2, "low": 0, "shot": 0, "penalty": -1.5}
# spposition → 등번호 (흔한 번호)
NUMBERS = {0: 1, 1: 5, 2: 2, 3: 2, 4: 4, 5: 5, 6: 6, 7: 3, 8: 3, 9: 6, 10: 6, 11: 16, 12: 7, 13: 8, 14: 8, 15: 14,
           16: 11, 17: 10, 18: 10, 19: 20, 20: 7, 21: 9, 22: 11, 23: 7, 24: 9, 25: 9, 26: 19, 27: 11}


def kind_of(shot: dict[str, Any]) -> str:
    return KINDS.get(shot.get("type"), "shot")


# ── 베스트 골 ──────────────────────────────────────────
def best_goal(left: Team, right: Team) -> tuple[Team, dict[str, Any]] | None:
    """xG가 낮을수록, 멀수록, 어려운 종류일수록, 늦을수록, 결승골이면 높게. 골이 없으면 None."""
    scored = sorted(((team, s) for team in (left, right) for s in team.side.get("shootDetail", [])
                     if s.get("result") == SHOT_GOAL), key=lambda item: item[1]["goalTime"])
    if not scored:
        return None
    winner = None  # 결승골: 이긴 팀의 (진 팀 골 수 + 1)번째 골
    lg, rg = goals(left.side), goals(right.side)
    if lg != rg:
        win, lose = (left, rg) if lg > rg else (right, lg)
        mine = [s for team, s in scored if team is win]
        winner = mine[lose] if len(mine) > lose else None

    def score(item: tuple[Team, dict]) -> float:
        _, s = item
        dist, _ = geometry(s["x"], s["y"])
        return ((1 - shot_xg(s)) * 2 + min(dist, 35) / 25 + BONUS[kind_of(s)]
                + (.4 if minute_of(s["goalTime"]) >= 80 else 0) + (.3 if s is winner else 0))

    return max(scored, key=score)


# ── 벡터 ───────────────────────────────────────────────
def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def mul(a, k):
    return tuple(x * k for x in a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def lerp(a, b, u):
    return tuple(x + (y - x) * u for x, y in zip(a, b))


def norm(a):
    n = math.sqrt(dot(a, a)) or 1.0
    return mul(a, 1 / n)


def ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _inv3(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    A, B, C = e * i - f * h, -(d * i - f * g), d * h - e * g
    det = a * A + b * B + c * C
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
            [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
            [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]]


class Cam:
    def __init__(self, pos, target, fov: float) -> None:
        self.pos = pos
        self.fw = norm(sub(target, pos))
        self.r = norm(cross(self.fw, (0, 0, 1)))
        self.u = cross(self.r, self.fw)
        self.f = (VW * S / 2) / math.tan(math.radians(fov / 2))
        self.cx, self.cy = VW * S / 2, VH * S / 2

    def to_cam(self, p):
        v = sub(p, self.pos)
        return (dot(v, self.r), dot(v, self.u), dot(v, self.fw))

    def dir_cam(self, d):
        return (dot(d, self.r), dot(d, self.u), dot(d, self.fw))

    def scr(self, c):
        return (self.cx + self.f * c[0] / c[2], self.cy - self.f * c[1] / c[2])

    def proj(self, p):
        c = self.to_cam(p)
        return (self.scr(c) if c[2] > NEAR else None), c[2]


def _clip(poly):
    out = []
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        if a[2] > NEAR:
            out.append(a)
        if (a[2] > NEAR) != (b[2] > NEAR):
            t = (NEAR - a[2]) / (b[2] - a[2])
            out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))
    return out


def _plane(im: Image.Image, cam: Cam, O, U, V, tex: Image.Image, normal) -> None:
    """평면 사각형 O + sU + tV (s,t ∈ [0,1])에 텍스처를 투시로 붙인다. 화면에 걸친 부분만 변환한다."""
    if dot(normal, sub(cam.pos, O)) <= 0:
        return
    poly = _clip([cam.to_cam(p) for p in (O, add(O, U), add(add(O, U), V), add(O, V))])
    if len(poly) < 3:
        return
    pts = [cam.scr(c) for c in poly]
    x0, y0 = max(int(min(p[0] for p in pts)), 0), max(int(min(p[1] for p in pts)), 0)
    x1, y1 = min(int(max(p[0] for p in pts)) + 1, im.width), min(int(max(p[1] for p in pts)) + 1, im.height)
    if x1 <= x0 or y1 <= y0:
        return
    K = lambda c: (cam.f * c[0] + cam.cx * c[2], -cam.f * c[1] + cam.cy * c[2], c[2])
    cu, cv, co = K(cam.dir_cam(U)), K(cam.dir_cam(V)), K(cam.to_cam(O))
    mi = _inv3([[cu[0], cv[0], co[0]], [cu[1], cv[1], co[1]], [cu[2], cv[2], co[2]]])
    tw, th = tex.size
    g, h, i = mi[2]
    a, b, c = tw * mi[0][0] / i, tw * mi[0][1] / i, tw * mi[0][2] / i
    d, e, f = th * mi[1][0] / i, th * mi[1][1] / i, th * mi[1][2] / i
    g, h = g / i, h / i
    den = g * x0 + h * y0 + 1  # 잘라 낸 영역의 원점으로 옮긴다
    coeffs = (a / den, b / den, (a * x0 + b * y0 + c) / den, d / den, e / den, (d * x0 + e * y0 + f) / den,
              g / den, h / den)
    warped = tex.transform((x1 - x0, y1 - y0), Image.PERSPECTIVE, coeffs, Image.BILINEAR)
    mask = Image.new("L", warped.size, 0)
    ImageDraw.Draw(mask).polygon([(p[0] - x0, p[1] - y0) for p in pts], fill=255)
    im.paste(warped, (x0, y0), mask)


def _seg(d: ImageDraw.ImageDraw, cam: Cam, a, b, color, thick_m: float, min_px: float = 1.0) -> None:
    ca, cb = cam.to_cam(a), cam.to_cam(b)
    if ca[2] <= NEAR and cb[2] <= NEAR:
        return
    if ca[2] <= NEAR or cb[2] <= NEAR:
        poly = _clip([ca, cb])
        if len(poly) < 2:
            return
        ca, cb = poly[0], poly[1]
    z = (ca[2] + cb[2]) / 2
    d.line((cam.scr(ca), cam.scr(cb)), fill=color, width=max(1, round(max(min_px, cam.f * thick_m / z))))


# ── 텍스처 (한 번만 만든다) ─────────────────────────────
PX = 12  # 잔디 텍스처 픽셀/m
GX0, GY0, GX1, GY1 = -12, -10, 117, 78  # 잔디 범위 (경기장 0~105 × 0~68)


def _shade(c: tuple, k: float) -> tuple:
    return tuple(min(255, max(0, round(v * k))) for v in c)


@lru_cache(maxsize=1)  # 읽기만 하니 여러 골에 같이 쓴다
def _pitch_texture() -> Image.Image:
    """잔디 깎은 줄무늬(가로) + 옅은 바둑판, 골문 앞 닳은 자국, 가운데가 밝은 조명, 흰 선."""
    k = 2  # 선을 크게 그려 줄인다
    px = PX * k
    w, h = (GX1 - GX0) * px, (GY1 - GY0) * px
    X = lambda x: (x - GX0) * px
    Y = lambda y: (y - GY0) * px
    im = Image.new("RGB", (w, h), (36, 98, 52))
    d = ImageDraw.Draw(im)
    light, dark = (52, 128, 66), (44, 114, 58)
    for i in range(20):  # 경기장 길이 방향 줄무늬
        x0 = -3 + i * 111 / 20
        for j in range(6):  # 폭 방향으로 아주 옅게 엇갈린다
            y0 = -3 + j * 74 / 6
            base = light if i % 2 == 0 else dark
            d.rectangle((X(x0), Y(y0), X(x0 + 111 / 20), Y(y0 + 74 / 6)), fill=_shade(base, 1.03 if j % 2 else 0.98))
    wear = Image.new("L", (w, h), 0)  # 골문 앞·페널티 지점 닳은 잔디
    wd = ImageDraw.Draw(wear)
    for gx in (0, 105):
        wd.ellipse((X(gx - 4), Y(29), X(gx + 4), Y(39)), fill=70)
        wd.ellipse((X(abs(gx - 11) - 1.2), Y(32.8), X(abs(gx - 11) + 1.2), Y(35.2)), fill=60)
    wear = wear.filter(ImageFilter.GaussianBlur(px * 1.2))
    im.paste(Image.new("RGB", (w, h), (112, 128, 74)), (0, 0), wear)
    white = (238, 242, 238)
    lw = round(0.13 * px)
    d = ImageDraw.Draw(im)
    d.rectangle((X(0), Y(0), X(105), Y(68)), outline=white, width=lw)
    d.line((X(52.5), Y(0), X(52.5), Y(68)), fill=white, width=lw)
    r = 9.15
    d.ellipse((X(52.5 - r), Y(34 - r), X(52.5 + r), Y(34 + r)), outline=white, width=lw)
    d.ellipse((X(52.5) - lw, Y(34) - lw, X(52.5) + lw, Y(34) + lw), fill=white)
    for left in (True, False):
        for depth, half in ((16.5, 20.16), (5.5, 9.16)):
            x0, x1 = (0, depth) if left else (105 - depth, 105)
            d.rectangle((X(x0), Y(34 - half), X(x1), Y(34 + half)), outline=white, width=lw)
        spot = 11 if left else 94
        d.ellipse((X(spot) - lw, Y(34) - lw, X(spot) + lw, Y(34) + lw), fill=white)
        start, end = (-53, 53) if left else (127, 233)  # 페널티 아크: 박스 밖만
        d.arc((X(spot - r), Y(34 - r), X(spot + r), Y(34 + r)), start, end, fill=white, width=lw)
    for cx, cy in ((0, 0), (105, 0), (0, 68), (105, 68)):
        d.arc((X(cx - 1), Y(cy - 1), X(cx + 1), Y(cy + 1)), 0, 360, fill=white, width=lw)
    im = im.resize((w // k, h // k), Image.LANCZOS)
    # 조명: 가운데 밝고 가장자리로 갈수록 어둡게
    glow = Image.radial_gradient("L").resize(im.size).point(lambda v: int(min(255, v * 1.1) * 0.32))
    im = Image.composite(Image.new("RGB", im.size, (10, 26, 16)), im, glow)
    return im.filter(ImageFilter.GaussianBlur(0.4))


@lru_cache(maxsize=4)
def _crowd_texture(home: tuple, away: tuple, w: int = 1600, h: int = 320, seed: int = 3) -> Image.Image:
    """관중석: 좌석 줄 + 관중 (팀 색이 섞인 옷). 대비를 낮춰 GIF를 가볍게."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), (26, 30, 38))
    d = ImageDraw.Draw(im)
    rows = 26
    shirts = [_shade(home, .62), _shade(home, .5), _shade(away, .55), _shade(away, .45), (92, 96, 104),
              (64, 68, 78), (44, 48, 56), (120, 112, 100)]
    for r in range(rows):
        y0 = r * h / rows
        d.rectangle((0, y0, w, y0 + h / rows * 0.22), fill=(18, 21, 27))
        x = 0
        while x < w:
            step = rnd.choice((7, 8, 9))
            if rnd.random() < 0.85:
                c = rnd.choice(shirts)
                d.rectangle((x + 1, y0 + h / rows * 0.35, x + step - 2, y0 + h / rows * 0.98), fill=c)
                d.ellipse((x + 1.5, y0 + h / rows * 0.18, x + step - 2.5, y0 + h / rows * 0.48), fill=(150, 120, 100))
            x += step
    im = Image.blend(im, Image.new("RGB", (w, h), (22, 26, 34)), 0.42)
    return im.filter(ImageFilter.GaussianBlur(1.6))


@lru_cache(maxsize=16)
def _ribbon_texture(home: tuple, away: tuple, length_m: float, h: int = 40) -> Image.Image:
    """광고판 (0.9m): 팀 색 판과 글자가 번갈아."""
    pw = round(7 / 0.9 * h)
    w = round(length_m / 0.9 * h)
    im = Image.new("RGB", (w, h), (14, 18, 30))
    d = ImageDraw.Draw(im)
    ink_on = lambda c: BG if sum(c) > 450 else WHITE
    words = [("FC ONLINE", (14, 18, 30), WHITE), ("친구 리그", home, ink_on(home)),
             ("IMPOSTER FINDER", (14, 18, 30), GOLD), ("베스트 골", away, ink_on(away))]
    for n, x in enumerate(range(0, w, pw)):
        text, bg, ink = words[n % len(words)]
        d.rectangle((x, 0, x + pw - 2, h), fill=bg)
        d.text((x + pw / 2, h / 2), text, font=font(20, 6), fill=ink, anchor="mm")
    return im


@lru_cache(maxsize=1)
def _roof_texture(w: int = 800, h: int = 60) -> Image.Image:
    """지붕 아래: 어두운 철골 + 조명 줄."""
    im = Image.new("RGB", (w, h), (16, 18, 24))
    d = ImageDraw.Draw(im)
    for x in range(0, w, 40):
        d.line((x, 0, x, h), fill=(26, 29, 36), width=3)
    for x in range(10, w, 26):
        d.rectangle((x, h - 14, x + 12, h - 6), fill=(255, 248, 225))
    return im.filter(ImageFilter.GaussianBlur(0.8))


@lru_cache(maxsize=1)
def _ball_sprite(n: int = 96) -> Image.Image:
    """흰 공 + 검은 무늬 + 왼쪽 위 빛 (오른쪽 아래가 어둡다)."""
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, n - 1, n - 1), fill=(250, 250, 248, 255))
    for k in range(5):
        a = k * 2 * math.pi / 5 + 0.3
        cx, cy, r = n / 2 + math.cos(a) * n * 0.36, n / 2 + math.sin(a) * n * 0.36, n * 0.11
        d.regular_polygon((cx, cy, r), 5, rotation=math.degrees(a), fill=(36, 38, 44, 255))
    d.regular_polygon((n / 2, n / 2, n * 0.13), 5, fill=(36, 38, 44, 255))
    disc = Image.new("L", (n, n), 0)
    ImageDraw.Draw(disc).ellipse((0, 0, n - 1, n - 1), fill=255)
    shade = Image.radial_gradient("L").resize((n * 2, n * 2)).crop((round(n * .65), round(n * .65),
                                                                    round(n * 1.65), round(n * 1.65)))
    shade = shade.point(lambda v: int(min(v, 255) * 0.62))
    shade = Image.composite(shade, Image.new("L", (n, n), 0), disc)
    im.paste(Image.new("RGBA", (n, n), (52, 56, 70, 255)), (0, 0), shade)
    im.putalpha(disc)
    return im


# ── 장면 ───────────────────────────────────────────────
GOAL_Y0, GOAL_Y1, BAR = 30.34, 37.66, 2.44
TOP_BACK, BOT_BACK = (106.2, 2.3), (107.2, 0.0)  # 그물 뒤쪽 위·아래 (x, z)
R_BALL = 0.11
JUMP = 0.5  # 헤딩 점프 높이 (m)
FOREHEAD = (0.18, 1.71)  # 선 자세 기준 이마: 몸 앞쪽 · 높이 (m)


def _world(x: float, y: float) -> tuple[float, float]:
    """API 좌표 → 세계 좌표 (m). 오른쪽(+x) 골문으로 공격하고, 공격수의 왼쪽(API y 0)이 +y."""
    return (clamp(x, 0, 1) * 105, (1 - clamp(y, 0, 1)) * 68)


class Scene:
    """골 하나의 3D 장면. t초 → 공·선수·카메라. 시각: T0 패스 → T_PASS 받음 → T_SHOT 슈팅 → T_LINE 골라인 → T_NET 그물."""

    def __init__(self, shot: dict[str, Any], scorer_no: int, passer_no: int) -> None:
        self.kind = kind = kind_of(shot)
        self.numbers = (scorer_no, passer_no)
        rng = random.Random(int(shot.get("spId") or 0) * 31 + int(shot.get("goalTime") or 0))
        sx, sy = _world(shot["x"], shot["y"])
        self.S = (94.0, 34.0) if kind == "penalty" else (min(sx, 103.0), clamp(sy, 1.0, 67.0))
        S = self.S
        A = None
        if shot.get("assist") and shot.get("assistX") is not None and kind not in ("penalty", "freekick"):
            A = _world(shot["assistX"], shot["assistY"])
            A = (clamp(A[0], 0.5, 104.5), clamp(A[1], 0.5, 67.5))
        self.assisted = bool(shot.get("assist") and shot.get("assistSpId"))
        if A is None and kind in ("header", "volley"):  # 도움 기록이 없어도 머리·발리는 날아온 공이다: 측면 크로스로
            A = (clamp(S[0] - 10, 60, 100), 5.0 if S[1] > 34 else 63.0)
        self.A = A
        # 골문 안 어디로: 먼 쪽 구석이 기본, 종류마다 높이가 다르다
        far, near = (31.3, 36.6) if S[1] > 34 else (36.7, 31.4)
        if kind == "curl":
            gy, gz = far, 2.0
        elif kind == "freekick":
            gy, gz = far, 2.05
        elif kind == "low":
            gy, gz = rng.choice((far, near)), 0.22
        elif kind == "header":
            gy, gz = rng.choice((far, 34 + rng.uniform(-2.2, 2.2))), 0.45
        elif kind == "volley":
            gy, gz = far, 1.7
        elif kind == "penalty":
            gy, gz = rng.choice((31.2, 36.8)), rng.choice((0.4, 1.7))
        else:
            gy, gz = rng.choice((far, near)), rng.uniform(0.5, 1.9)
        if abs(S[1] - 34) < 4 and kind in ("shot", "low", "volley"):
            gy = rng.choice((31.4, 36.6))
        self.G = (105.0, gy, gz)
        self.keeper_wrong = kind == "penalty" and rng.random() < 0.5  # 페널티: 반대로 뜨기도
        self.z0 = {"header": JUMP + FOREHEAD[1], "volley": 0.75}.get(kind, R_BALL)

        self.T0 = 0.3  # 패스 전 잠깐
        self.direct = A is not None and kind in ("header", "volley")  # 크로스를 바로 머리·발리로
        if A is not None:
            self.R = S if self.direct else add(S, mul(norm(sub(A, S)), 1.6))  # 받는 곳 (슈팅 직전 터치)
            d = math.dist(A, self.R)
            self.lofted = self.direct or d > 24
            self.apex = clamp(2 + d * 0.13, 2.5, 10) if self.lofted else 0
            self.T_PASS = self.T0 + clamp(d / (15 if self.lofted else 18), .55, 2.1) + (.25 if self.lofted else 0)
            self.T_SHOT = self.T_PASS + (0 if self.direct else .5)
        elif kind in ("penalty", "freekick"):
            self.T_SHOT = 1.6
        else:  # 혼자 몰고 들어와서
            self.D = (S[0] - 6.5, S[1] + (2.0 if S[1] < 34 else -2.0))
            self.T_SHOT = 1.5
        self.P0 = (S[0], S[1], self.z0)
        dist = math.dist(self.P0, self.G)
        self.T_LINE = self.T_SHOT + clamp(dist / SPEED[kind], .35, 1.25)
        self.T_NET = self.T_LINE + .14
        self.END = self.T_NET + .8
        # 슈팅 궤적 (2차 베지어): 감아차기는 바깥으로 나갔다가 휘어 들어온다
        mid = lerp(self.P0, self.G, .5)
        flat = norm((self.G[0] - S[0], self.G[1] - S[1]))
        perp = (-flat[1], flat[0])
        out = 1 if (self.G[1] - 34) * perp[1] >= 0 else -1
        curl = {"curl": .11, "freekick": .09, "shot": .03}.get(kind, 0) * dist * out
        lift = {"curl": .6 + dist * .06, "freekick": 2.2 + dist * .05, "shot": .3 + self.G[2] * .3,
                "low": 0, "header": .2, "volley": .6, "penalty": .2}[kind]
        self.P1 = (mid[0] + perp[0] * curl, mid[1] + perp[1] * curl,
                   R_BALL if kind == "low" else (self.P0[2] + self.G[2]) / 2 + lift)
        self.N = (106.35, self.G[1] + (self.G[1] - 34) * .04, max(self.G[2] - .2, .2))  # 그물에 닿는 곳
        self.flat = flat
        side = (flat[1], -flat[0])  # 슈터 뒤 카메라가 비켜서는 쪽 (y<0, 중계석 쪽)
        self.cam_side = side if side[1] <= 0 else (-side[0], -side[1])

    # 공
    def ball(self, t: float) -> tuple[float, float, float]:
        S = self.S
        if t < self.T_SHOT:
            if self.A is not None:
                A, R = self.A, self.R
                if t < self.T0:
                    return (A[0], A[1], R_BALL)
                if t < self.T_PASS:
                    u = (t - self.T0) / (self.T_PASS - self.T0)
                    if self.lofted:
                        end_z = self.z0 if self.direct else 0.5
                        x, y = lerp(A, R, u)
                        return (x, y, R_BALL + (end_z - R_BALL) * u + 4 * self.apex * u * (1 - u))
                    x, y = lerp(A, R, 1 - (1 - u) ** 1.6)
                    return (x, y, R_BALL)
                u = (t - self.T_PASS) / (self.T_SHOT - self.T_PASS)
                x, y = lerp(R, S, 1 - (1 - u) ** 2)
                bounce = .4 * max(0, 1 - u * 3) * abs(math.sin(u * 8)) if self.lofted else 0
                return (x, y, R_BALL + bounce)
            if self.kind in ("penalty", "freekick"):
                return (S[0], S[1], R_BALL)
            u = t / self.T_SHOT  # 드리블: 툭툭 치고 간다
            x, y = lerp(self.D, S, u)
            return (x + .25 * math.sin(u * math.pi * 4) ** 2, y, R_BALL)
        if t < self.T_LINE:
            u = (t - self.T_SHOT) / (self.T_LINE - self.T_SHOT)
            u = 1 - (1 - u) ** 1.12
            return tuple((1 - u) ** 2 * a + 2 * (1 - u) * u * b + u * u * c for a, b, c in zip(self.P0, self.P1, self.G))
        if t < self.T_NET:
            return lerp(self.G, self.N, 1 - (1 - (t - self.T_LINE) / (self.T_NET - self.T_LINE)) ** 2)
        u = min((t - self.T_NET) / .6, 1)  # 그물 안으로 떨어진다
        z = self.N[2] * (1 - u) ** 2 + R_BALL + .3 * abs(math.sin(u * math.pi * 1.6)) * (1 - u)
        return (self.N[0] + .2 * u, self.N[1], max(R_BALL, z))

    def bulge(self, t: float) -> float:
        dt = t - self.T_NET + .04
        return .75 * math.exp(-dt / .28) * math.cos(dt * 11) if 0 <= dt < 1.5 else 0.0

    # 선수: (이름, 팀, 발 위치(뛰면 z>0), 자세, 다이빙, 걸음 위상, 등번호)
    def _players(self, t: float) -> list[tuple]:
        S, flat, kind = self.S, self.flat, self.kind
        T_SHOT = self.T_SHOT
        contact = (S[0] - flat[0] * .45, S[1] - flat[1] * .45)
        side = 1 if S[1] < 34 else -1  # 가운데 쪽
        cam_side = self.cam_side
        out = []
        sn, pn = self.numbers

        # 득점자
        z = 0.0
        if kind in ("penalty", "freekick"):
            start = (contact[0] - flat[0] * 3.2 - flat[1] * 1.4, contact[1] - flat[1] * 3.2 + flat[0] * 1.4)
            u = ease((t - (T_SHOT - .95)) / .95)
            p = lerp(start, contact, u)
            pose = "stand" if u <= 0 else "run"
        elif self.A is not None:
            R = self.R
            start = (R[0] - 7.5, R[1] + 3.0 * side)
            if t < self.T_PASS:
                p = lerp(start, R, ease((t - .1) / (self.T_PASS - .1)) * .85 + .15 * t / self.T_PASS)
            else:
                p = lerp(R, contact, ease((t - self.T_PASS) / max(T_SHOT - self.T_PASS, .01)))
            pose = "run"
            if kind == "header":
                jump = clamp((t - (T_SHOT - .35)) / .7, 0, 1)
                z = JUMP * math.sin(math.pi * jump)
                take_off = sub(S, mul(flat, FOREHEAD[0]))  # 공이 이마에 닿도록 몸은 공 뒤에
                p = lerp(start, take_off, ease((t - .1) / (T_SHOT - .15))) if t < T_SHOT else take_off
                pose = "header" if 0 < jump < 1 else "run"
        else:
            b = self.ball(min(t, T_SHOT))
            p = (b[0] - flat[0]*.45, b[1] - flat[1]*.45) if t < T_SHOT else contact
            pose = "run"
        if T_SHOT - .16 <= t < T_SHOT + .32 and kind != "header":
            pose = "volley" if kind == "volley" else "kick"
            if t >= T_SHOT:
                p = contact
        land = T_SHOT + (.36 if kind == "header" else .32)  # 헤딩은 착지한 뒤에 달려 나간다
        if t >= land:  # 카메라 반대쪽으로 달려가 세리머니 (화면 안에 남게)
            u = ease((t - land) / 1.6)
            begin = contact if kind != "header" else sub(S, mul(flat, FOREHEAD[0]))
            end = add(begin, add(mul(flat, 5), mul(cam_side, -1.5)))
            end = (min(end[0], 103), clamp(end[1], 2, 66))
            p = lerp(begin, end, u)
            pose = "celebrate" if t > self.T_NET + .1 else "run"
            z = 0
        out.append(("scorer", "home", (p[0], p[1], z), pose, 0, t * 9, sn))

        # 도움 선수
        if self.A is not None:
            A = self.A
            back = norm(sub(A, self.R))
            spot = (A[0] + back[0] * .45, A[1] + back[1] * .45)
            pose = "kick" if self.T0 - .08 <= t < self.T0 + .3 else ("stand" if t < self.T0 else "run")
            run = max(0, t - self.T0 - .3) * 2.4
            out.append(("passer", "home", (spot[0] + run, spot[1] + run * .2 * side, 0), pose, 0, t * 7, pn))

        # 동료 한 명 (박스로 침투)
        if kind != "penalty":
            m0 = (S[0] - 14, clamp(34 + (34 - S[1]) * .8, 6, 62))
            m1 = (min(S[0] + 4, 100), clamp(34 + (34 - S[1]) * .35, 10, 58))
            out.append(("mate", "home", lerp(m0, m1, ease(t / self.T_LINE)) + (0,), "run", 0, t * 8.5 + 1, 17))
        else:
            for n, y in enumerate((22, 46)):
                x = 88 + (ease((t - T_SHOT) / 1.2) * 6 if t > T_SHOT else 0)
                out.append((f"mate{n}", "home", (x, y, 0), "run" if t > T_SHOT else "stand", 0, t * 8 + n, 7 + n * 3))

        # 수비
        if kind == "freekick":  # 9.15m 앞 벽, 슈팅 때 뛴다
            wall = (S[0] + flat[0] * 9.15, S[1] + flat[1] * 9.15)
            jump = clamp((t - T_SHOT + .05) / .5, 0, 1)
            for n in range(4):
                off = (n - 1.5) * .55 + .7 * (1 if self.G[1] > 34 else -1) * -1
                pos = (wall[0] - flat[1] * off, wall[1] + flat[0] * off, .4 * math.sin(math.pi * jump))
                out.append((f"wall{n}", "away", pos, "wall", 0, 0, (4, 5, 6, 8)[n]))
        elif kind == "penalty":
            for n, y in enumerate((26, 42)):
                x = 87.5 + (ease((t - T_SHOT) / 1.2) * 5 if t > T_SHOT else 0)
                out.append((f"d{n}", "away", (x, y, 0), "run" if t > T_SHOT else "stand", 0, t * 8 + 2 + n, 4 + n))
        else:
            d1a = (S[0] + flat[0] * 6 + flat[1] * 2, S[1] + flat[1] * 6 - flat[0] * 2)
            d1b = (S[0] + flat[0] * 1.6 + flat[1] * .8, S[1] + flat[1] * 1.6 - flat[0] * .8)
            out.append(("d1", "away", lerp(d1a, d1b, ease((t - .2) / (T_SHOT - .1))) + (0,),
                        "run" if t < T_SHOT + .3 else "stand", 0, t * 8, 4))
            d2 = lerp((99.0, 34 + (S[1] - 34) * .3 + 3), (101.0, 34 + (S[1] - 34) * .3 + 1.5), ease(t / T_SHOT))
            out.append(("d2", "away", d2 + (0,), "run" if t < T_SHOT else "stand", 0, t * 7.5 + 2, 5))
            far = (-cam_side[0], -cam_side[1])  # 카메라 반대쪽에서 쫓아온다 (화면을 가리지 않게)
            d3a = (S[0] - flat[0] * 8 + far[0] * 4, S[1] - flat[1] * 8 + far[1] * 4)
            d3b = (S[0] - flat[0] * 2 + far[0] * 1.8, S[1] - flat[1] * 2 + far[1] * 1.8)
            d3 = lerp(d3a, d3b, ease(t / (T_SHOT + .3)))
            out.append(("d3", "away", d3 + (0,), "run", 0, t * 9 + 3, 6))

        # 골키퍼: 늦게 몸을 던진다
        b = self.ball(min(t, T_SHOT))
        ky = 34 + (b[1] - 34) * .22
        start = T_SHOT - .05 if kind == "penalty" else T_SHOT + (self.T_LINE - T_SHOT) * .35
        dive = ease((t - start) / .5)
        if dive <= 0:
            out.append(("gk", "keeper", (104.1, ky, 0), "keeper", 0, 0, 1))
        else:
            ty = (68 - self.G[1]) if self.keeper_wrong else self.G[1]
            c = lerp((104.1, ky, .66), (104.2, ky + (ty - ky) * .62, .55 + min(self.G[2], 1.8) * .35), dive)
            if t > start + .75:  # 떨어진다
                c = (c[0], c[1], max(.25, c[2] - (t - start - .75) * 2.5))
            out.append(("gk", "keeper", c, "dive", dive * (1 if ty > ky else -1), 0, 1))
        return out

    def _prepare_motion(self) -> None:
        """Fixed-rate root tracks make gait depend on metres travelled, not wall-clock time."""
        if hasattr(self, '_motion'):
            return
        self._motion = {}
        dt = 1 / 60
        for i in range(math.ceil((self.END + .2) / dt) + 1):
            t = i * dt
            for row in self._players(t):
                name, team, pos, pose, dive, _, number = row
                records = self._motion.setdefault(name, [])
                distance = records[-1][4] + math.dist(pos[:2], records[-1][1][:2]) if records else 0.0
                records.append((t, pos, pose, dive, distance))
        for rows in self._motion.values():
            for i,row in enumerate(rows):
                a,b=rows[max(0,i-2)],rows[min(len(rows)-1,i+2)]
                speed=math.dist(a[1][:2],b[1][:2])/max(b[0]-a[0],.001)
                if row[2]=='run' and speed<.12:
                    rows[i]=(row[0],row[1],'stand',row[3],row[4])

    def motion(self, name: str, t: float) -> tuple[float, float, tuple]:
        self._prepare_motion()
        rows = self._motion[name]
        f = clamp(t * 60, 0, len(rows)-1)
        i = min(int(f), len(rows)-2)
        distance = rows[i][4] + (rows[i+1][4] - rows[i][4]) * (f-i)
        # Look through short stand-stills instead of flipping toward the camera.
        a, b = rows[max(0, i-2)], rows[min(len(rows)-1, i+2)]
        velocity = sub(b[1][:2], a[1][:2])
        speed = math.hypot(*velocity) / max(b[0]-a[0], .001)
        if speed < .05:
            for j in range(i, -1, -1):
                if j and math.dist(rows[j][1][:2], rows[j-1][1][:2]) > .0001:
                    velocity = sub(rows[j][1][:2], rows[j-1][1][:2])
                    break
        heading = norm(velocity) if math.hypot(*velocity) > .0001 else self.flat
        if name == 'gk':
            heading = (-1.0, 0.0)
        if name.startswith('wall'):
            heading = mul(self.flat, -1)
        phase = distance / 1.8 * math.tau + sum(map(ord, name)) * .13
        return phase, speed, heading

    def facing(self, name: str, t: float, pose: str) -> tuple:
        heading = self.motion(name, t)[2]
        contact = self.T0 if name == 'passer' else self.T_SHOT
        if pose in ('kick','volley','header') or contact-.22 <= t <= contact:
            target = norm(sub(self.R, self.A)) if name == 'passer' and self.A is not None else self.flat
            heading = norm(lerp(heading, target, ease((t-contact+.22)/.22)))
        return heading

    def action_phase(self, name: str, pose: str, t: float) -> float:
        if pose in ('kick', 'volley'):
            contact = self.T0 if name == 'passer' else self.T_SHOT
            return clamp((t-contact+.16)/.48, 0, 1)
        if pose == 'header':  # 0 도약 · .5 공에 닿음 · 1 착지
            return clamp((t-self.T_SHOT+.35)/.7, 0, 1)
        return self.motion(name, t)[0]

    def players(self, t: float) -> list[tuple]:
        out = []
        for name, team, pos, pose, dive, _, number in self._players(t):
            phase, speed, _ = self.motion(name, t)
            if pose == 'run' and speed < .12:
                pose = 'stand'
            if pose == 'celebrate' and speed < .6:  # 멈추면 두 팔을 번쩍
                pose, phase = 'cheer', clamp(t - self.T_NET, 0, 3)
            out.append((name, team, pos, pose, dive, self.action_phase(name, pose, t), number))
        return out

    def body(self, name: str, t: float, pose: str, phase: float) -> dict:
        current = _pose(pose, phase)
        rows = self._motion[name]
        i = min(max(0, int(t*60)), len(rows)-1)
        # Blend discrete stance changes over 160ms rather than popping limbs.
        for j in range(i, max(0, i-11), -1):
            if rows[j][2] != rows[j-1][2]:
                u = ease((t-rows[j][0])/.16)
                previous = _pose(rows[j-1][2], self.action_phase(name, rows[j-1][2], rows[j][0]))
                return {k: [lerp(a,b,u) for a,b in zip(previous[k],current[k])] if k in ('feet','hands')
                        else lerp(previous[k],current[k],u) if k=='hip'
                        else previous[k]+(current[k]-previous[k])*u for k in current}
        return current

    # 카메라
    def cam(self, t: float) -> Cam:
        S, G, flat = self.S, self.G, self.flat
        side = self.cam_side
        back = 13 if self.kind in ("penalty", "freekick") else 10  # 세트피스는 키커가 다 보이게 더 뒤에서
        behind = (S[0] - flat[0] * back + side[0] * 3.5, S[1] - flat[1] * back + side[1] * 3.5, 3.0 + (back - 10) * .15)
        look = (104.0, 34 + (G[1] - 34) * .6, 1.1)
        fov_b = 36  # 슈팅부터는 카메라를 세운다: 바뀌는 픽셀만 저장돼 GIF가 가볍다
        if self.kind in ("penalty", "freekick"):  # 세트피스: 높은 곳에서 키커 뒤로 내려온다
            high = (S[0] - flat[0] * 15 + side[0] * 6, S[1] - flat[1] * 15 + side[1] * 6, 8.0)
            u = ease(t / self.T_SHOT)
            pos = lerp(high, behind, u)
            return Cam(pos, lerp((104, 34, 0), look, u), 44 + (fov_b - 44) * u)
        swoop = max(0.0, self.T_SHOT - 1.0)
        bx = clamp(self.ball(min(t, swoop))[0], 30, 82)
        broad_pos, broad_tgt = (bx - 7, -23.0, 17.0), (bx + 7, 34.0, 0.0)
        u = ease((t - swoop) / max(self.T_SHOT - swoop, .01))
        pos = lerp(broad_pos, behind, u)
        pos = (pos[0], pos[1], pos[2] + math.sin(u * math.pi) * 2.5)  # 크레인처럼 곡선으로
        return Cam(pos, lerp(broad_tgt, look, u), 42 + (fov_b - 42) * u)


# ── 선수 그리기 ────────────────────────────────────────
# 팀컬러 이름 → 홈 유니폼 (상의, 하의, 스타킹). 엠블럼 색(Team.color)은 맨시티처럼 유니폼과 다를 수 있다
# 밀라노 FC·롬바르디아 FC는 FC 온라인의 AC 밀란·인테르
KITS = {"맨체스터 시티": ((108, 172, 222), (245, 245, 245), (108, 172, 222)),
        "맨체스터 유나이티드": ((214, 30, 36), (245, 245, 245), (24, 24, 28)),
        "리버풀": ((200, 16, 46), (200, 16, 46), (200, 16, 46)),
        "아스널": ((219, 0, 7), (245, 245, 245), (245, 245, 245)),
        "첼시": ((3, 70, 148), (3, 70, 148), (245, 245, 245)),
        "토트넘 홋스퍼": ((245, 245, 245), (19, 34, 87), (245, 245, 245)),
        "바이에른 뮌헨": ((220, 5, 45), (220, 5, 45), (220, 5, 45)),
        "레알 마드리드": ((245, 245, 245), (245, 245, 245), (245, 245, 245)),
        "FC 바르셀로나": ((165, 0, 68), (0, 77, 152), (165, 0, 68)),
        "파리 생제르맹": ((0, 65, 112), (0, 65, 112), (0, 65, 112)),
        "유벤투스": ((245, 245, 245), (24, 24, 28), (245, 245, 245)),
        "밀라노 FC": ((251, 9, 11), (245, 245, 245), (24, 24, 28)),
        "롬바르디아 FC": ((0, 104, 168), (24, 24, 28), (24, 24, 28))}


def _kit(color: tuple, shorts: tuple | None = None, socks: tuple | None = None) -> dict:
    light = sum(color) > 450
    return {"shirt": color, "shade": _shade(color, .72),
            "shorts": shorts or ((34, 38, 48) if light else (240, 242, 246)), "socks": socks or color,
            "ink": BG if light else WHITE}


def _team_kit(team: Team) -> dict:
    """팀컬러 이름으로 실제 유니폼 색, 모르는 팀은 엠블럼 색."""
    name = re.sub(r"^\d\d-\d\d ", "", team.team_name or "")  # '21-22 맨체스터 시티' 같은 시즌 팀컬러도
    return _kit(*KITS[name]) if name in KITS else _kit(team.color)


def _match_kits(home: Team, away: Team) -> tuple[dict, dict]:
    """Resolve a kit clash independently of crest/UI colors, including same-club matches."""
    a, b = _team_kit(home), _team_kit(away)
    if sum(abs(x-y) for x,y in zip(a['shirt'], b['shirt'])) < 180:
        alternate = (242, 244, 247) if sum(a['shirt']) < 560 else (28, 52, 100)
        b = _kit(alternate, (28, 34, 48), alternate)
    return a, b


SKIN, HAIR, BOOT = (222, 178, 144), (44, 33, 26), (24, 24, 28)


def _joint(a, b, length: float, forward: float) -> tuple:
    """두 마디 관절: a→b 사이가 짧으면 forward 쪽(+x면 앞)으로 굽힌다."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    dist = math.hypot(dx, dy) or 1e-6
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    half = length / 2
    h = math.sqrt(max(half * half - (dist / 2) ** 2, 0))
    px, py = -dy / dist, dx / dist
    if px * forward < 0:
        px, py = -px, -py
    return (mid[0] + px * h, mid[1] + py * h)


def _pose(pose: str, phase: float) -> dict:
    """몸 기준 좌표 (m, 오른쪽이 앞, 위가 +). 발 두 개(뒤·앞), 손 두 개, 엉덩이, 어깨 기울기."""
    hip, lean = (0.0, 0.95), 0.0
    if pose == "run":
        feet=[]
        for angle in (phase,phase+math.pi):
            u=(angle/math.tau)%1
            if u<.5: feet.append((.45-1.8*u,.025))
            else:
                k=(u-.5)*2;feet.append((-.45+.9*ease(k),.025+.22*math.sin(math.pi*k)))
        hands=[(-.24*math.sin(x),1.05+.04*abs(math.sin(x))) for x in (phase,phase+math.pi)]
        lean,hip=.075,(0,.90+.018*math.cos(phase*2))
    elif pose in ("kick","volley"):
        u=clamp(phase,0,1);contact=1/3
        back=(-.40,.09);hit=(.5,.16 if pose=='kick' else .78);follow=(.66,.55 if pose=='kick' else 1.02)
        if u<contact: foot=lerp(back,hit,ease(u/contact))
        elif u<.7: foot=lerp(hit,follow,ease((u-contact)/(.7-contact)))
        else: foot=lerp(follow,(.12,.03),ease((u-.7)/.3))
        feet,hands,lean=[(-.08,.025),foot],[(-.37,1.23),(.32,1.18)],-.04
    elif pose == "header":
        feet, hands, lean = [(-.18, .3), (.12, .18)], [(-.42, 1.45), (.4, 1.42)], .12
    elif pose == "celebrate":
        a = phase
        feet = [(.2 * math.sin(a), .04 + .12 * max(0.0, -math.cos(a))), (-.2 * math.sin(a), .04)]
        hands, lean = [(-.36, 2.0), (.36, 2.02)], 0.0
    elif pose == "keeper":
        feet, hands, hip, lean = [(-.22, .03), (.22, .03)], [(-.38, 1.12), (.38, 1.12)], (0.0, .82), .08
    elif pose == "wall":
        feet, hands = [(-.12, .03), (.12, .03)], [(-.05, .9), (.07, .9)]
    elif pose == "dive":
        feet, hands = [(-.12, 0.0), (.12, .05)], [(-.12, 2.05), (.12, 2.08)]
    else:
        feet, hands = [(-.13, .03), (.13, .03)], [(-.3, .98), (.3, .98)]
    return {"feet": feet, "hands": hands, "hip": hip, "lean": lean}


def _figure_procedural(d: ImageDraw.ImageDraw, cam: Cam, anchor, kit: dict, pose: str, phase: float, facing: int,
            dive: float = 0.0, number: int | None = None, heading: tuple | None = None, body: dict | None = None) -> None:
    """Articulated world-space player: limbs and shirt rotate with the runner, never billboard-flip."""
    projected, depth = cam.proj(anchor)
    if projected is None or depth < 3.5 or cam.f/depth*1.8 < 6:
        return
    forward = norm(heading or (float(facing), 0.0))
    right = (-forward[1], forward[0])
    body = body or _pose(pose, phase)
    hip, lean = body['hip'], body['lean']
    meshes = []
    light = norm((-.4, -.5, 1.0))

    def point(x, z, side=0.0):
        if pose == 'dive':
            # Rotate around the body centre along the actual dive direction.
            theta = math.radians(78)*abs(dive)
            sign=-1 if dive<0 else 1
            side,z=side*math.cos(theta)+(z-.95)*math.sin(theta)*sign,(z-.95)*math.cos(theta)-side*math.sin(theta)*sign
        return (anchor[0]+forward[0]*x+right[0]*side, anchor[1]+forward[1]*x+right[1]*side, anchor[2]+z)

    def face(poly, color):
        screens = [cam.proj(p)[0] for p in poly]
        if any(p is None for p in screens):
            return
        normal = norm(cross(sub(poly[1],poly[0]),sub(poly[2],poly[0])))
        lighting = .68+.32*max(0,dot(normal,light))
        meshes.append((sum(cam.to_cam(p)[2] for p in poly)/len(poly),screens,_shade(color,lighting)))

    def bone(a,b,r0,r1,color):
        axis = norm(sub(b,a));side = norm(cross(axis,(0,0,1)))
        if math.hypot(*side[:2]) < .01:
            side = (1,0,0)
        other = norm(cross(axis,side))
        rings=[]
        # Anatomical profile: rounded joints, muscle belly, tapered wrist/ankle.
        for t,k in ((0,.75),(.18,1.12),(.48,1.05),(.82,.85),(1,.70)):
            center=lerp(a,b,t);radius=(r0*(1-t)+r1*t)*k
            rings.append([add(center,add(mul(side,math.cos(i*math.tau/10)*radius),mul(other,math.sin(i*math.tau/10)*radius*.85))) for i in range(10)])
        for ring in range(len(rings)-1):
            for i in range(10):
                j=(i+1)%10;face([rings[ring][i],rings[ring][j],rings[ring+1][j],rings[ring+1][i]],color)
        face(rings[0][::-1],color);face(rings[-1],color)

    def box(x,z,front,width,height,color,side=0):
        corners=[point(x+dx,z+dz,sy+side) for dz in (-height/2,height/2) for sy in (-width/2,width/2) for dx in (-front/2,front/2)]
        for indices in ((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)):
            face([corners[i] for i in indices],color)

    shoulder_z=hip[1]+.48
    for i,side in enumerate((-.13,.13)):
        foot=body['feet'][i];knee=_joint(hip,foot,1.02,1)
        h=point(*hip,side);k=point(*knee,side);f=point(*foot,side)
        bone(h,k,.085,.067,SKIN)
        # Socks begin below the knee; thighs remain skin and shorts cover the root.
        bone(k,f,.066,.045,kit['socks'])
        box(foot[0]+.05,foot[1]+.018,.23,.12,.09,BOOT,side)
        hand=body['hands'][i];shoulder=(lean,shoulder_z);elbow=_joint(shoulder,hand,.60,-1)
        s=point(*shoulder,side*1.7);e=point(*elbow,side*1.85);w=point(*hand,side*2)
        bone(s,lerp(s,e,.5),.072,.064,kit['shirt'])
        bone(lerp(s,e,.5),e,.063,.052,SKIN);bone(e,w,.052,.036,SKIN)
    box(hip[0],hip[1]-.08,.28,.36,.25,kit['shorts'])
    # Tailored jersey silhouette: waist, chest, shoulders and neckline.
    torso=[]
    for dz,half_width,half_front in ((.01,.16,.12),(.15,.17,.115),(.34,.235,.14),(.46,.21,.12),(.49,.09,.07)):
        torso.append([point(lean*dz/.49+math.cos(i*math.tau/12)*half_front,
                            hip[1]+dz,math.sin(i*math.tau/12)*half_width) for i in range(12)])
    for row in range(len(torso)-1):
        for i in range(12):
            j=(i+1)%12
            face([torso[row][i],torso[row][j],torso[row+1][j],torso[row+1][i]],kit['shirt'])
    face(torso[-1],kit['ink'])
    # Front crest and placket rotate with the shirt, never appear on its back.
    face([point(lean*.7+.145,hip[1]+z,side) for z,side in ((.29,-.08),(.29,-.035),(.35,-.035),(.35,-.08))],kit['ink'])
    face([point(lean*.8+.145,hip[1]+z,side) for z,side in ((.36,-.015),(.36,.015),(.43,.015),(.43,-.015))],kit['ink'])
    bone(point(lean,shoulder_z+.01),point(lean,shoulder_z+.13),.065,.061,SKIN)
    # Small faceted ellipsoid: shaded head volume and a hair cap.
    center=point(lean+.01,shoulder_z+.24)
    rows=[]
    for lat in range(6):
        angle=-math.pi/2+lat*math.pi/5
        rows.append([add(center,(math.cos(lon*math.tau/8)*math.cos(angle)*.115,
                                 math.sin(lon*math.tau/8)*math.cos(angle)*.115,math.sin(angle)*.145)) for lon in range(8)])
    for lat in range(5):
        for lon in range(8):
            nxt=(lon+1)%8
            face([rows[lat][lon],rows[lat][nxt],rows[lat+1][nxt],rows[lat+1][lon]],HAIR if lat>=3 else SKIN)
    # Nose, eyes and front hairline make the world facing direction legible.
    nose=point(lean+.14,shoulder_z+.24)
    face([point(lean+.11,shoulder_z+.28,-.024),nose,point(lean+.11,shoulder_z+.21,.024)],_shade(SKIN,.85))
    for side in (-.047,.047):
        face([point(lean+.113,shoulder_z+z,side+dy) for z,dy in ((.28,-.015),(.28,.015),(.30,.015),(.30,-.015))],HAIR)
    for _,poly,color in sorted(meshes,key=lambda item:-item[0]):
        d.polygon(poly,fill=color)
    # Number follows the shirt's back face rather than floating on the viewer-facing plane.
    back=point(-.14,hip[1]+.25)
    if number is not None and dot((forward[0],forward[1],0),cam.fw)>.25:
        pos,z=cam.proj(back)
        if pos and cam.f/z>34:
            d.text(pos,str(number),font=font(max(8,round(cam.f/z*.22)),6),fill=kit['ink'],anchor='mm')


def _figure(d, cam, anchor, kit, pose, phase, facing, dive=0.0, number=None, heading=None, body=None):
    from imposter_finder.analysis.fc_player_mesh import draw_player
    try:
        draw_player(d, cam, anchor, kit, pose, phase, heading or (float(facing),0.0), dive, number)
    except (OSError, ValueError, KeyError):
        _figure_procedural(d, cam, anchor, kit, pose, phase, facing, dive, number, heading, body)


def _shadow(d: ImageDraw.ImageDraw, cam: Cam, pos, size: float = .38, alpha: int = 80, long: bool = True) -> None:
    """발밑 그림자 + (선수는) 조명 쪽으로 길게 늘어진 그림자."""
    base = (pos[0], pos[1], .01)
    p, z = cam.proj(base)
    if p is None:
        return
    k = cam.f / z
    q, _ = cam.proj((pos[0] + 1.1, pos[1] + .75, .01))
    if long and q is not None:
        d.line((p, q), fill=(0, 0, 0, alpha // 2), width=max(1, round(.34 * k)))
    tilt = max(.18, min(.6, abs(cam.fw[2]) * 1.4 + .12))
    d.ellipse((p[0] - size * k, p[1] - size * k * tilt, p[0] + size * k, p[1] + size * k * tilt), fill=(0, 0, 0, alpha))


# ── 골대 · 그물 ────────────────────────────────────────
def _net_point(panel: str, a: float, b: float, bulge: float, hit: tuple) -> tuple:
    if panel == "back":
        y = GOAL_Y0 + (GOAL_Y1 - GOAL_Y0) * a
        x = TOP_BACK[0] + (BOT_BACK[0] - TOP_BACK[0]) * b
        zz = TOP_BACK[1] * (1 - b)
    elif panel == "roof":
        y = GOAL_Y0 + (GOAL_Y1 - GOAL_Y0) * a
        x = 105 + (TOP_BACK[0] - 105) * b
        zz = BAR + (TOP_BACK[1] - BAR) * b
    else:  # 옆: a = 앞→뒤, b = 위→아래
        y = GOAL_Y0 if panel == "side0" else GOAL_Y1
        top, bot = lerp((105, BAR), TOP_BACK, a), lerp((105, 0), BOT_BACK, a)
        x, zz = lerp(top, bot, b)
    if bulge:
        g = math.exp(-((y - hit[1]) ** 2 + (zz - hit[2]) ** 2) / (2 * .75 ** 2))
        x += bulge * g * (1.0 if panel == "back" else .5)
    return (x, y, zz)


def _draw_net(d, cam, panel: str, bulge: float, hit: tuple) -> None:
    na, nb = (30, 10) if panel == "back" else (30, 4) if panel == "roof" else (6, 10)
    col = (235, 238, 240, 135)
    grid = [[_net_point(panel, i / na, j / nb, bulge, hit) for j in range(nb + 1)] for i in range(na + 1)]
    for i in range(na + 1):
        for a, b in zip(grid[i], grid[i][1:]):
            _seg(d, cam, a, b, col, .022)
    for j in range(nb + 1):
        for i in range(na):
            _seg(d, cam, grid[i][j], grid[i + 1][j], col, .022)


def _draw_posts(d, cam) -> None:
    white, shade, frame = (252, 252, 252), (196, 200, 206), (170, 174, 180)
    for y in (GOAL_Y0, GOAL_Y1):
        _seg(d, cam, (105, y, 0), (105, y, BAR), shade, .13, 2)
        _seg(d, cam, (104.98, y - .02, 0), (104.98, y - .02, BAR), white, .08, 1)
        _seg(d, cam, (105, y, BAR), (TOP_BACK[0], y, TOP_BACK[1]), frame, .05)
        _seg(d, cam, (TOP_BACK[0], y, TOP_BACK[1]), (BOT_BACK[0], y, 0), frame, .05)
    _seg(d, cam, (105, GOAL_Y0, BAR), (105, GOAL_Y1, BAR), shade, .13, 2)
    _seg(d, cam, (104.98, GOAL_Y0, BAR + .02), (104.98, GOAL_Y1, BAR + .02), white, .08, 1)
    _seg(d, cam, (BOT_BACK[0], GOAL_Y0, 0), (BOT_BACK[0], GOAL_Y1, 0), frame, .05)


# ── 3D 화면 ────────────────────────────────────────────
class Renderer:
    def __init__(self, scene: Scene, home: dict, away: dict) -> None:
        """home·away: 유니폼 (_team_kit)."""
        self.scene = scene
        keeper = (196, 232, 60)
        if not (_far(away["shirt"], keeper) and _far(home["shirt"], keeper)):
            keeper = (240, 120, 40)
        self.kits = {"home": home, "away": away, "keeper": _kit(keeper, (30, 34, 40))}
        home, away = home["shirt"], away["shirt"]
        self.colors = (home, away)
        self.pitch = _pitch_texture()
        self.crowd = _crowd_texture(home, away)
        self.led = _ribbon_texture(home, away, 160, h=24)
        self.roof = _roof_texture()
        self.ball_img = _ball_sprite()
        w, h = VW * S, VH * S
        sky = Image.linear_gradient("L").resize((w, h))
        self.sky = Image.composite(Image.new("RGB", (w, h), (30, 36, 50)), Image.new("RGB", (w, h), (6, 8, 13)), sky)
        vignette = Image.radial_gradient("L").resize((w, h)).point(lambda v: int(clamp((v - 120) * 0.9, 0, 110)))
        self.vignette = vignette
        self.black = Image.new("RGB", (w, h), (0, 0, 0))

    def _stands(self, im: Image.Image, cam: Cam) -> None:
        """네 방향 관중석: 아래층 경사 · LED 띠 · 위층 경사 · 지붕 아래. 긴 쪽은 모서리까지 이어 빈틈을 막고, 먼 것부터."""
        ends = 30  # 긴 쪽 관중석을 양 끝으로 늘린다
        stands = [((GX1, GY1, 0), (0, GY0 - GY1, 0), (1, 0, 0), (-1, 0, 0)),  # 골대 뒤
                  ((GX0, GY0, 0), (0, GY1 - GY0, 0), (-1, 0, 0), (1, 0, 0)),  # 반대쪽 골대 뒤
                  ((GX0 - ends, GY1, 0), (GX1 - GX0 + ends * 2, 0, 0), (0, 1, 0), (0, -1, 0)),  # 건너편
                  ((GX1 + ends, GY0, 0), (GX0 - GX1 - ends * 2, 0, 0), (0, -1, 0), (0, 1, 0))]  # 이쪽
        center = lambda st: add(st[0], add(mul(st[1], .5), mul(st[2], 20)))
        stands.sort(key=lambda st: -math.dist(center(st), cam.pos))
        for edge, U, back, n in stands:
            lower = mul(back, 22)
            _plane(im, cam, edge, U, add(lower, (0, 0, 11)), self.crowd, add(mul(n, 11), (0, 0, 22)))
            o2 = add(edge, add(lower, (0, 0, 11)))
            _plane(im, cam, add(o2, (0, 0, 1.6)), U, (0, 0, -1.6), self.led, n)  # 텍스처 위가 위로
            o3 = add(o2, (0, 0, 1.6))
            _plane(im, cam, o3, U, add(mul(back, 18), (0, 0, 12)), self.crowd, add(mul(n, 12), (0, 0, 18)))
            o4 = add(o3, add(mul(back, 18), (0, 0, 12.5)))
            _plane(im, cam, o4, U, mul(back, -16), self.roof, (0, 0, -1))

    def render(self, t: float, frame_dt: float) -> tuple[Image.Image, dict]:
        sc = self.scene
        cam = sc.cam(t)
        im = self.sky.copy()
        self._stands(im, cam)
        _plane(im, cam, (GX0, GY0, 0), (GX1 - GX0, 0, 0), (0, GY1 - GY0, 0), self.pitch, (0, 0, 1))
        # 광고판: 골대 뒤 판은 그물 끝(107.2m)에서 9m 가까이 떨어뜨린다 (골대와 붙어 보이지 않게)
        for O, U, n in (((-8, 74.5, .9), (124, 0, 0), (0, -1, 0)), ((116, 74.5, .9), (0, -81, 0), (-1, 0, 0)),
                        ((116, -6.5, .9), (-124, 0, 0), (0, 1, 0))):
            ribbon = _ribbon_texture(*self.colors, round(math.hypot(*U[:2])))
            _plane(im, cam, O, U, (0, 0, -.9), ribbon, n)
        d = ImageDraw.Draw(im, "RGBA")
        ball = sc.ball(t)
        players = sc.players(t)
        ahead = {name: pos for name, _, pos, *_ in sc.players(t + .05)}
        for name, team, pos, pose, dive, *_ in players:
            _shadow(d, cam, pos, .36 if pose != "dive" else .7, 70)
        _shadow(d, cam, ball, .13, int(110 * max(.15, 1 - ball[2] / 8)), long=False)
        if t > sc.T_SHOT:  # 슈팅 궤적: 금색 꼬리
            pts = [sc.ball(t - i * .025) for i in range(11) if t - i * .025 > sc.T_SHOT]
            for i, (a, b) in enumerate(zip(pts, pts[1:])):
                _seg(d, cam, a, b, GOLD + (int(210 * (1 - i / 11)),), .07, 2)
        bulge = sc.bulge(t)
        items: list[tuple[float, Callable[[], None]]] = []
        for panel in ("back", "roof", "side0", "side1"):
            c = _net_point(panel, .5, .5, 0, sc.N)
            items.append((cam.to_cam(c)[2] + (.6 if panel == "back" else 0),
                          lambda panel=panel: _draw_net(d, cam, panel, bulge, sc.N)))
        items.append((cam.to_cam((105, 34, 1.2))[2] - .2, lambda: _draw_posts(d, cam)))
        for name, team, pos, pose, dive, phase, number in players:
            center = pos if pose == "dive" else add(pos, (0, 0, .9))
            nxt = ahead.get(name, pos)
            sp, _ = cam.proj(pos)
            sq, _ = cam.proj(nxt)
            facing = 1 if not (sp and sq) or sq[0] - sp[0] >= -0.5 else -1
            if pose in ("kick", "volley", "header"):  # 차는 쪽은 골문 방향
                gp, _ = cam.proj((105, 34, 0))
                facing = 1 if not (gp and sp) or gp[0] >= sp[0] else -1
            items.append((cam.to_cam(center)[2],
                          lambda pos=pos, team=team, pose=pose, phase=phase, dive=dive, number=number, facing=facing, name=name:
                          _figure(d, cam, pos, self.kits[team], pose, phase, facing, dive, number,
                                  sc.facing(name,t,pose),sc.body(name,t,pose,phase))))
        items.append((cam.to_cam(ball)[2], lambda: self._ball(im, cam, t, frame_dt)))
        for _, fn in sorted(items, key=lambda it: -it[0]):
            fn()
        im = Image.composite(self.black, im, self.vignette)
        out = im.resize((VW, VH), Image.LANCZOS)
        heads = {}
        for name, team, pos, pose, *_ in players:
            hp, _ = cam.proj(add(pos, (0, 0, 2.05)))
            heads[name] = (hp[0] / S, hp[1] / S) if hp else None
        return out, heads

    def _ball(self, im: Image.Image, cam: Cam, t: float, frame_dt: float) -> None:
        """공 + 빠를 때 잔상 (움직임 흐림)."""
        sc = self.scene
        ghosts = 4 if frame_dt >= .06 and t > sc.T_SHOT - .02 and t < sc.T_NET else 0
        for g in range(ghosts, -1, -1):
            b = sc.ball(t - g * frame_dt / (ghosts + 1)) if g else sc.ball(t)
            p, z = cam.proj(b)
            if p is None:
                continue
            r = max(2.4, cam.f * R_BALL / z)
            size = max(4, round(r * 2))
            sprite = self.ball_img.rotate(-t * 900 % 360, resample=Image.BILINEAR).resize((size, size), Image.LANCZOS)
            if g:
                a = sprite.getchannel("A").point(lambda v, g=g: int(v * (.5 - g * .09)))
                sprite.putalpha(a)
            im.paste(sprite, (round(p[0] - size / 2), round(p[1] - size / 2)), sprite)


def _far(a: tuple, b: tuple) -> bool:
    return sum(abs(x - y) for x, y in zip(a, b)) > 160


# ── 중계 그래픽 ────────────────────────────────────────
def _pill(d, box, fill, r=None) -> None:
    d.rounded_rectangle(box, radius=r if r is not None else (box[3] - box[1]) // 2, fill=fill)


def _tag(im: Image.Image, text: str) -> None:
    """3D 화면 오른쪽 위: ● 리플레이 / 슬로 모션."""
    d = ImageDraw.Draw(im, "RGBA")
    f = font(14, 6)
    tw = d.textlength(text, font=f)
    x1, y = VW - 14, 14
    x0 = x1 - tw - 40
    _pill(d, (x0, y, x1, y + 30), BG + (220,), 8)
    d.ellipse((x0 + 12, y + 10, x0 + 22, y + 20), fill=(235, 50, 60))
    d.text((x1 - 12, y + 15), text, font=f, fill=WHITE, anchor="rm")


def _name_label(im: Image.Image, xy, text: str, color: tuple) -> None:
    if xy is None or not text or not 0 <= xy[0] <= VW or not 0 <= xy[1] <= VH:  # 화면 밖 선수는 이름도 없다
        return
    d = ImageDraw.Draw(im, "RGBA")
    f = font(13, 6)
    tw = d.textlength(text, font=f)
    x, y = clamp(xy[0], tw / 2 + 10, VW - tw / 2 - 10), max(xy[1], 64)
    _pill(d, (x - tw / 2 - 8, y - 22, x + tw / 2 + 8, y - 2), color + (235,), 6)
    d.text((x, y - 12), text, font=f, fill=BG if sum(color) > 450 else WHITE, anchor="mm")


def _goal_banner(im: Image.Image, color: tuple, scale: float) -> None:
    """fc_flow와 같은 골 판: 팀 색 알약 + 흰 테두리 + 60px 글자."""
    word, f = "골!", font(60, 6)
    tmp = Image.new("RGBA", (360, 140), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    tw, ph = d.textlength(word, font=f), 92
    d.rounded_rectangle((180 - tw / 2 - 36, 70 - ph / 2, 180 + tw / 2 + 36, 70 + ph / 2), radius=ph // 2,
                        fill=color + (255,), outline=WHITE + (255,), width=3)
    d.text((180, 70), word, font=f, fill=BG if sum(color) > 450 else WHITE, anchor="mm")
    if scale != 1:
        tmp = tmp.resize((max(1, round(360 * scale)), max(1, round(140 * scale))), Image.LANCZOS)
    im.paste(tmp, (round(VW / 2 - tmp.width / 2), round(VH * .42 - tmp.height / 2)), tmp)


def _backdrop(left: Team, right: Team) -> Image.Image:
    """fc_report의 중계 바탕(팀 색 빛 · 옅은 엠블럼)을 이 폭에 맞춰."""
    return _stage(round(H * 1000 / W), left, right).resize((W, H), Image.LANCZOS)


def _header(img: Image.Image, left: Team, right: Team, score: tuple[int, int], minute: int, scorer: Team) -> None:
    d = ImageDraw.Draw(img)
    for team, x, anchor in ((left, PAD, "lm"), (right, W - PAD, "rm")):
        crest = icon(team.crest, 36, radius=0)
        tx = x
        if crest:
            img.paste(crest, (x if anchor == "lm" else x - 36, 18), crest)
            tx = x + 46 if anchor == "lm" else x - 46
        else:
            cx = x + 9 if anchor == "lm" else x - 9
            d.ellipse((cx - 9, 27, cx + 9, 45), fill=team.color)
            tx = x + 26 if anchor == "lm" else x - 26
        d.text((tx, 36), team.nickname, font=font(18, 6), fill=WHITE, anchor=anchor)
    ls, rs = score
    cx = W / 2
    f = font(40, 6)
    d.text((cx - 16, 6), str(ls), font=f, fill=left.color if scorer is left and ls else WHITE, anchor="ra")
    d.text((cx, 10), ":", font=font(32, 6), fill=LABEL, anchor="ma")
    d.text((cx + 16, 6), str(rs), font=f, fill=right.color if scorer is right and rs else WHITE, anchor="la")
    d.text((cx, 54), f"{minute}'", font=font(14), fill=LABEL, anchor="ma")


def _panel(img: Image.Image, team: Team, shot: dict, meta: FcMeta, scene: Scene) -> None:
    """득점자 카드: 얼굴 · 시즌 · 이름 · 슈팅 정보 · 도움, 오른쪽에 작은 슈팅맵."""
    x0, y0, x1, y1 = PAD, PANEL_Y, W - PAD, PANEL_Y + PANEL_H
    _glass_box(img, (x0, y0, x1, y1), radius=14, alpha=24)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0 + 14, x0 + 5, y1 - 14), radius=2, fill=team.color)
    face = _face(face_image(shot["spId"]), 68, team.color)
    img.paste(face, (x0 + 20, y0 + 14), face)
    tx = x0 + 104
    name = meta.player_name(shot["spId"])
    f = font(22, 6)
    d.text((tx, y0 + 16), name, font=f, fill=WHITE, anchor="la")
    season = icon(meta.season_image(shot["spId"]), 22, radius=0)
    if season:
        img.paste(season, (round(tx + d.textlength(name, font=f) + 8), y0 + 18), season)
    dist, _ = geometry(shot["x"], shot["y"])
    kind = shot_label(shot) or "슈팅"
    d.text((tx, y0 + 50), f"{kind} · {dist:.1f}m · xG {shot_xg(shot):.2f}", font=font(16, 6), fill=GOLD, anchor="la")
    if scene.assisted and shot.get("assistSpId"):
        helper = f"도움 {meta.player_name(shot['assistSpId'])}"
    else:
        helper = {"penalty": "페널티킥", "freekick": "직접 프리킥", "header": "세컨드 볼", "volley": "세컨드 볼"}.get(
            scene.kind, "단독 돌파")
    d.text((tx, y0 + 74), f"{helper} · {minute_of(shot['goalTime'])}' · {team.nickname}", font=font(14), fill=LABEL,
           anchor="la")
    # 작은 슈팅맵: 골대가 위, 도움 → 슈팅 → 골문
    pw, ph = 100, 76
    mx, my = x1 - 18 - pw, y0 + (PANEL_H - ph) // 2
    img.paste(_half_pitch(pw, ph), (mx, my))
    layer = Image.new("RGBA", (pw * 4, ph * 4), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    to_px = lambda wx, wy: ((1 - wy / 68) * pw * 4, clamp((105 - wx) / 52.5, 0, 1) * ph * 4)
    sx, sy = to_px(*scene.S)
    if scene.assisted:
        ax, ay = to_px(*scene.A)
        ld.line((ax, ay, sx, sy), fill=team.color + (255,), width=8)
        ld.ellipse((ax - 10, ay - 10, ax + 10, ay + 10), fill=team.color + (255,))
    gx, gy = to_px(105, scene.G[1])
    ld.line((sx, sy, gx, gy), fill=GOLD + (255,), width=8)
    ld.ellipse((sx - 18, sy - 18, sx + 18, sy + 18), fill=team.color + (255,), outline=WHITE + (255,), width=6)
    layer = layer.resize((pw, ph), Image.LANCZOS)
    img.paste(layer, (mx, my), layer)


# ── 만들기 ─────────────────────────────────────────────
def render(left: Team, right: Team, meta: FcMeta, picked: tuple[Team, dict] | None = None,
           size: float = 1.0) -> bytes | None:
    """베스트 골 GIF. 골이 없으면 None. size < 1은 테스트용 (작게 · 빨리)."""
    picked = picked or best_goal(left, right)
    if picked is None:
        return None
    team, shot = picked
    other = right if team is left else left
    players = {p["spId"]: p for p in team.side.get("player", [])}
    number = lambda spid, default: NUMBERS.get((players.get(spid) or {}).get("spPosition"), default)
    scene = Scene(shot, number(shot["spId"], 9), number(shot.get("assistSpId"), 8))
    rd = Renderer(scene, *_match_kits(team, other))

    before = {id(t): sum(1 for s in t.side.get("shootDetail", []) if s.get("result") == SHOT_GOAL
                         and s["goalTime"] < shot["goalTime"]) for t in (left, right)}
    after = dict(before)
    after[id(team)] += 1
    minute = minute_of(shot["goalTime"])
    short = lambda spid: meta.player_name(spid).split()[-1] if spid else ""

    base = _backdrop(left, right)
    _panel(base, team, shot, meta, scene)
    heads_score = {}
    for key, sc in (("before", before), ("after", after)):
        im = base.copy()
        _header(im, left, right, (sc[id(left)], sc[id(right)]), minute, team if key == "after" else None)
        heads_score[key] = im
    mask = Image.new("L", (VW * 4, VH * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, VW * 4 - 1, VH * 4 - 1), radius=12 * 4, fill=255)
    mask = mask.resize((VW, VH), Image.LANCZOS)

    # (장면 시각, 프레임 ms, 표시)
    plan: list[tuple[float, int, str]] = []
    swoop = max(0.0, scene.T_SHOT - 1.0)
    t = 0.0
    while t < swoop:
        plan.append((t, 100, "live"))
        t += .1
    while t < scene.T_SHOT - .1:
        plan.append((t, 90, "swoop"))
        t += .055
    while t < scene.T_NET + .05:  # 슬로 모션: 카메라가 서 있어 프레임이 많아도 가볍다
        plan.append((t, 90, "slow"))
        t += .032
    while t < scene.END:
        plan.append((t, 90, "net"))
        t += .06
    if size < 1:
        plan = plan[::8]

    frames = []
    for n, (t, ms, state) in enumerate(plan):
        nxt = plan[n + 1][0] if n + 1 < len(plan) else t + .05
        view, heads = rd.render(t, nxt - t)
        goal_in = t >= scene.T_LINE + .04
        _tag(view, "슬로 모션" if state in ("slow", "net") else "베스트 골 리플레이")
        if state in ("live", "swoop") and t < scene.T_SHOT - .3:
            _name_label(view, heads.get("passer"), short(shot.get("assistSpId")) if scene.assisted else "", team.color)
            _name_label(view, heads.get("scorer"), short(shot["spId"]), team.color)
        if goal_in:
            k = min((t - scene.T_LINE - .04) / .18, 1)
            _goal_banner(view, team.color, .55 + .45 * ease(k) + .08 * math.sin(k * math.pi))
        im = heads_score["after" if goal_in else "before"].copy()
        im.paste(view, (VX, VY), mask)
        frames.append((im, ms))
    frames[-1] = (frames[-1][0], LAST_MS)
    if size < 1:
        frames = [(im.resize((round(W * size), round(H * size))), ms) for im, ms in frames]
    return _encode(frames)


def _encode(frames: list[tuple[Image.Image, int]]) -> bytes:
    """한 팔레트 (여러 장면에서 뽑은 255색), 점 섞기 없이. 너무 크면 느린 장면 밖의 프레임을 줄인다."""
    w, h = frames[0][0].size
    picks = sorted({0, len(frames) // 3, len(frames) // 2, 2 * len(frames) // 3, len(frames) - 1})
    sample = Image.new("RGB", (w, h * len(picks)))
    for n, idx in enumerate(picks):
        sample.paste(frames[idx][0], (0, h * n))
    palette = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    indexed = [(im.quantize(palette=palette, dither=Image.Dither.NONE), ms) for im, ms in frames]
    for _ in range(3):
        out = BytesIO()
        indexed[0][0].save(out, "GIF", save_all=True, append_images=[im for im, _ in indexed[1:]],
                           duration=[ms for _, ms in indexed], loop=0, optimize=True)
        data = out.getvalue()
        if len(data) <= GIF_MAX_BYTES:
            return data
        # 두 장면마다 하나만 남기고 그 시간만큼 보여 준다 (마지막 장면은 그대로)
        body, last = indexed[:-1], indexed[-1]
        indexed = [(body[n][0], sum(ms for _, ms in body[n:n + 2])) for n in range(0, len(body), 2)] + [last]
    raise ValueError("FC 베스트 골 GIF 용량 초과")


def build_highlight(match: dict[str, Any], friends: dict[str, Friend], meta: FcMeta,
                    colors: FcTeamColors | None) -> tuple[dict[str, Any], list[tuple]] | None:
    """리포트 다음에 보내는 베스트 골 메시지. 골이 없거나 실패하면 None (리포트는 그대로)."""
    try:
        left, right = _teams(match, friends, colors)
        picked = best_goal(left, right)
        if picked is None:
            return None
        data = render(left, right, meta, picked)
        monitoring.observe("fc.highlight")
    except Exception as exc:
        monitoring.error("fc.highlight", exc, "FC 베스트 골 GIF 실패 — 리포트는 그대로")
        print(f"[fc] highlight rendering failed: {exc}")
        return None
    team, shot = picked
    who = meta.player_name(shot["spId"]).split()[-1]
    text = f"**베스트 골** · {who} {minute_of(shot['goalTime'])}' · {shot_label(shot) or '슈팅'}"
    payload = {
        "flags": 32768,  # Components V2
        "components": [{"type": 17, "accent_color": ACCENT, "components": [
            {"type": 10, "content": text},
            {"type": 12, "items": [{"media": {"url": "attachment://goal.gif"}}]}]}],
    }
    return payload, [("goal.gif", data, "image/gif")]
