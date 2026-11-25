"""FC 온라인 친구 리그 리포트: 친구전 5경기마다 순위표 · 시상식 · 통산 기록 · 플레이 스타일.

사람마다 경기 수가 달라 승점 합계 대신 Elo로 순위를 매긴다 (모든 경기가 1:1 맞대결).
축구 Elo처럼 골 차가 클수록 더 움직이고, 몰수 경기는 뺀다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from imposter_finder.analysis.cards import PAD, WIDTH, font, header, png
from imposter_finder.analysis.fc_awards import render_awards, render_records
from imposter_finder.analysis.fc_report import BG, LABEL, TRACK, WHITE, Friend, goals, is_forfeit
from imposter_finder.analysis.fc_xg import team_xg
from imposter_finder.games.fconline import FcMeta

LEAGUE_EVERY = 5  # 친구전 이만큼 쌓이면 리그 리포트
ELO_START, ELO_K = 1500, 16  # 같은 상대와 자주 붙어 K가 크면 몇 경기에 순위가 뒤집힌다 (30이면 20경기에 ±150)
STYLE_MIN_GAMES = 5  # 미만은 적은 표본임을 표시한다
UP, DOWN, SAME = (62, 207, 142), (239, 91, 91), (120, 126, 138)
ATTACK, DEFENSE, BUILD = (229, 144, 133), (155, 191, 233), (239, 194, 101)
ACCENT = 0x2ECC71


def _played(match: dict[str, Any]) -> bool:
    return len(match.get("matchInfo", [])) == 2 and not is_forfeit(match) and all(
        side.get("player") for side in match["matchInfo"])


def _margin(gd: int) -> float:
    """세계 축구 Elo의 골 차 가중."""
    gd = abs(gd)
    return 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


def elo(matches: list[dict[str, Any]]) -> dict[str, float]:
    ratings: dict[str, float] = defaultdict(lambda: float(ELO_START))
    for match in matches:
        if not _played(match):
            continue
        a, b = match["matchInfo"]
        ga, gb = goals(a), goals(b)
        expected = 1 / (1 + 10 ** ((ratings[b["ouid"]] - ratings[a["ouid"]]) / 400))
        actual = 1 if ga > gb else 0 if ga < gb else 0.5
        change = ELO_K * _margin(ga - gb) * (actual - expected)
        ratings[a["ouid"]] += change
        ratings[b["ouid"]] -= change
    return dict(ratings)


@dataclass
class Row:
    ouid: str
    name: str
    elo: float
    delta: float
    rank: int = 0
    prev_rank: int | None = None
    w: int = 0
    d: int = 0
    l: int = 0
    gf: int = 0
    ga: int = 0
    xgf: float = 0.0
    xga: float = 0.0
    total: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])  # 통산 승·무·패·득·실

    @property
    def games(self) -> int:
        return self.w + self.d + self.l

    @property
    def ppg(self) -> float:
        return (self.w * 3 + self.d) / self.games if self.games else 0.0


def standings(history: list[dict[str, Any]], window: list[dict[str, Any]], friends: dict[str, Friend]) -> list[Row]:
    """history: 이번 구간까지의 모든 친구전(오래된 순), window: 이번 구간 경기."""
    ids = {m["matchId"] for m in window}
    before = elo([m for m in history if m["matchId"] not in ids])
    after = elo(history)
    rows: dict[str, Row] = {}
    for ouid in after:
        if ouid in friends:
            rows[ouid] = Row(ouid, friends[ouid].name, after[ouid], after[ouid] - before.get(ouid, ELO_START))
    for match in window:
        if not _played(match):
            continue
        for me, op in (match["matchInfo"], match["matchInfo"][::-1]):
            row = rows.get(me["ouid"])
            if not row:
                continue
            gf, ga = goals(me), goals(op)
            row.w, row.d, row.l = row.w + (gf > ga), row.d + (gf == ga), row.l + (gf < ga)
            row.gf, row.ga = row.gf + gf, row.ga + ga
            row.xgf, row.xga = row.xgf + team_xg(me), row.xga + team_xg(op)
    for match in history:  # 통산
        if not _played(match):
            continue
        for me, op in (match["matchInfo"], match["matchInfo"][::-1]):
            if me["ouid"] in rows:
                gf, ga = goals(me), goals(op)
                t = rows[me["ouid"]].total
                t[0], t[1], t[2], t[3], t[4] = t[0] + (gf > ga), t[1] + (gf == ga), t[2] + (gf < ga), t[3] + gf, t[4] + ga
    previous = sorted((o for o in rows if o in before), key=lambda o: -before[o])
    ordered = sorted(rows.values(), key=lambda r: -r.elo)
    for n, row in enumerate(ordered, 1):
        row.rank = n
        row.prev_rank = previous.index(row.ouid) + 1 if row.ouid in previous else None
    return ordered


# ── 플레이 스타일 ───────────────────────────────────────
STYLE_METRICS = [
    ("득점", ATTACK, lambda s: s["goals"] / s["n"]),
    ("xG", ATTACK, lambda s: s["xg"] / s["n"]),
    ("슈팅", ATTACK, lambda s: s["shots"] / s["n"]),
    ("실점 억제", DEFENSE, lambda s: -s["goals_allowed"] / s["n"]),
    ("xGA 억제", DEFENSE, lambda s: -s["xga"] / s["n"]),
    ("유효슛 억제", DEFENSE, lambda s: -s["shots_allowed"] / s["n"]),
    ("태클", DEFENSE, lambda s: s["tackle_ok"] / max(s["tackle_try"], 1)),
    ("패스", BUILD, lambda s: s["pass_ok"] / max(s["pass_try"], 1)),
    ("스루패스", BUILD, lambda s: s["through"] / s["n"]),
]


def style_totals(history: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for match in history:
        if not _played(match):
            continue
        for me, op in (match["matchInfo"], match["matchInfo"][::-1]):
            t = totals[me["ouid"]]
            t["n"] += 1
            t["goals"] += goals(me)
            t["goals_allowed"] += goals(op)
            t["xg"] += team_xg(me)
            t["xga"] += team_xg(op)
            t["shots"] += me["shoot"].get("shootTotal") or 0
            t["shots_allowed"] += op["shoot"].get("effectiveShootTotal") or 0
            t["pass_ok"] += me["pass"].get("passSuccess") or 0
            t["pass_try"] += me["pass"].get("passTry") or 0
            t["through"] += me["pass"].get("throughPassSuccess") or 0
            t["tackle_ok"] += me["defence"].get("tackleSuccess") or 0
            t["tackle_try"] += me["defence"].get("tackleTry") or 0
    return {ouid: dict(values) for ouid, values in totals.items()}


# ── 이미지 ──────────────────────────────────────────────
def _canvas(height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, height), BG)
    return img, ImageDraw.Draw(img)


def render_table(rows: list[Row], games: int) -> bytes:
    """순위 · Elo | 이번 구간 승무패·득실·xG 득실 | 통산 경기·승무패·승률·득실."""
    row_h = 64
    height = 128 + row_h * len(rows) + 16
    img, dr = _canvas(height)
    header(dr, "친구 리그", [], font_weights=(7, 6))
    groups = [(f"최근 {games}경기", 420, 640), ("통산", 668, WIDTH - PAD)]
    for label, x0, x1 in groups:
        dr.text(((x0 + x1) / 2, 64), label, font=font(14, 6), fill=WHITE, anchor="mm")
        dr.line((x0, 76, x1, 76), fill=TRACK, width=1)
    cols = [("순위", PAD + 8, "la"), ("감독", PAD + 84, "la"), ("Elo", 380, "ra"),
            ("승 무 패", 500, "ra"), ("득실", 568, "ra"), ("xG 득실", 640, "ra"),
            ("경기", 712, "ra"), ("승 무 패", 812, "ra"), ("승률", 890, "ra"), ("득실", WIDTH - PAD, "ra")]
    for label, x, anchor in cols:
        dr.text((x, 90), label, font=font(16, 6), fill=(191, 203, 219), anchor=anchor)
    for n, row in enumerate(rows):
        y = 112 + n * row_h
        if n % 2 == 0:
            dr.rounded_rectangle((PAD - 8, y, WIDTH - PAD + 8, y + row_h - 6), radius=10, fill=(22, 25, 31))
        cy = y + (row_h - 6) / 2
        dr.text((PAD + 8, cy), str(row.rank), font=font(24, 6), fill=WHITE, anchor="lm")
        if row.prev_rank is not None and row.prev_rank != row.rank:
            up = row.prev_rank > row.rank
            ax, sign = PAD + 40, -1 if up else 1
            dr.polygon([(ax, cy + 6 * sign), (ax - 6, cy - 4 * sign), (ax + 6, cy - 4 * sign)], fill=UP if up else DOWN)
            dr.text((ax + 10, cy), str(abs(row.prev_rank - row.rank)), font=font(13, 6), fill=UP if up else DOWN,
                    anchor="lm")
        dr.text((PAD + 84, cy), row.name, font=font(22, 7), fill=WHITE, anchor="lm")
        dr.text((380, cy - 8), f"{row.elo:.0f}", font=font(22, 6), fill=WHITE, anchor="ra")
        delta = round(row.delta)
        dr.text((380, cy + 12), f"{delta:+d}" if delta else "±0", font=font(13, 6),
                fill=UP if delta > 0 else DOWN if delta < 0 else SAME, anchor="ra")
        recent = WHITE if row.games else LABEL
        cell = lambda x, text, color: dr.text((x, cy), text, font=font(20, 6), fill=color, anchor="rm")
        cell(500, f"{row.w} {row.d} {row.l}" if row.games else "-", recent)
        cell(568, f"{row.gf - row.ga:+d}" if row.games else "-", recent)
        cell(640, f"{row.xgf - row.xga:+.1f}" if row.games else "-", recent)
        w, d, l, gf, ga = row.total
        played = w + d + l
        cell(712, str(played), WHITE)
        cell(812, f"{w} {d} {l}", WHITE)
        cell(890, f"{w / played * 100:.0f}%" if played else "-", WHITE)
        cell(WIDTH - PAD, f"{gf - ga:+d}" if played else "-", WHITE)
    return png(img)


def render_styles(people: list[tuple[str, dict[str, float]]], scale: list[tuple[float, float]]) -> bytes:
    cols = min(len(people), 3)
    cell = (WIDTH - 2 * PAD) / cols
    row_height = 270
    img, dr = _canvas(76 + math.ceil(len(people) / cols) * row_height)
    header(dr, "플레이 스타일 · 통산", [(ATTACK, "공격"), (DEFENSE, "수비"), (BUILD, "빌드업")],
           font_weights=(7, 6))
    angles = [-math.pi / 2 + j * math.tau / len(STYLE_METRICS) for j in range(len(STYLE_METRICS))]
    for i, (name, totals) in enumerate(people):
        row, col = divmod(i, cols)
        top = 76 + row * row_height
        cx, cy = PAD + cell * (col + 0.5), top + 142
        dr.text((cx, top), name, font=font(22, 7), fill=WHITE, anchor="ma")
        count = int(totals.get("n", 0))
        sample = " · 표본 적음" if 0 < count < STYLE_MIN_GAMES else ""
        dr.text((cx, top + 28), f"{count}경기{sample}", font=font(15, 6), fill=LABEL, anchor="ma")
        if not count:
            dr.text((cx, cy), "집계된 친구전 없음", font=font(17, 6), fill=LABEL, anchor="mm")
            continue
        radius = 65

        def point(angle: float, r: float) -> tuple[float, float]:
            return cx + math.cos(angle) * r, cy + math.sin(angle) * r

        for level in (0.25, 0.5, 0.75, 1):
            dr.polygon([point(a, radius * level) for a in angles], outline=(59, 65, 75), width=2)
        for a in angles:
            dr.line(((cx, cy), point(a, radius)), fill=(45, 50, 61))
        values = [f(totals) for _, _, f in STYLE_METRICS]
        pts = [point(a, radius * (0.16 + 0.84 * (min(1, max(0, (v - lo) / (hi - lo))) if hi > lo else 0.5)))
               for a, v, (lo, hi) in zip(angles, values, scale)]
        layer = Image.new("RGBA", img.size)
        paint = ImageDraw.Draw(layer)
        for j, (_, color, _) in enumerate(STYLE_METRICS):
            paint.polygon([(cx, cy), pts[j], pts[(j + 1) % len(pts)]], fill=color + (82,))
        img.paste(layer, (0, 0), layer)
        for j, ((label, color, _), a, (x, y)) in enumerate(zip(STYLE_METRICS, angles, pts)):
            dr.line(((x, y), pts[(j + 1) % len(pts)]), fill=color, width=3)
            dr.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
            direction = math.cos(a)
            anchor = "lm" if direction > 0.2 else "rm" if direction < -0.2 else "mm"
            dr.text(point(a, radius + 17), label, font=font(15, 6), fill=color, anchor=anchor)
    return png(img)


def build_league_report(history: list[dict[str, Any]], window: list[dict[str, Any]],
                        friends: dict[str, Friend], meta: FcMeta, *, roster: set[str] | None = None) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    history = [m for m in history if _played(m)]
    window = [m for m in window if _played(m)]
    rows = standings(history, window, friends)
    files = [("league.png", render_table(rows, len(window)))]
    files.extend(render_awards(history, window, friends, meta))
    files.append(("records.png", render_records(history, friends, meta)))

    totals = style_totals(history)
    people = [(r.name, totals[r.ouid]) for r in rows]
    played_names = {name for name, _ in people}
    all_names = roster or {friend.name for friend in friends.values()}
    people.extend((name, {}) for name in sorted(all_names - played_names))
    eligible = [t for _, t in people if t.get("n", 0) >= STYLE_MIN_GAMES]
    calibration = eligible if len(eligible) >= 2 else [t for _, t in people if t.get("n", 0)]
    scale = [(min((f(t) for t in calibration), default=0), max((f(t) for t in calibration), default=0))
             for _, _, f in STYLE_METRICS]
    if people:
        files.append(("styles.png", render_styles(people, scale)))
    components = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, _ in files]
    return {"flags": 32768, "allowed_mentions": {"users": []},
            "components": [{"type": 17, "accent_color": ACCENT, "components": components}]}, files
