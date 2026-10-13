"""FC 온라인 친구 리그 리포트: 친구전 5경기마다 순위표 · 맞대결 · 플레이 스타일.

사람마다 경기 수가 달라 승점 합계 대신 Elo로 순위를 매긴다 (모든 경기가 1:1 맞대결).
축구 Elo처럼 골 차가 클수록 더 움직이고, 몰수 경기는 뺀다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from imposter_finder.analysis.cards import PAD, SS, WIDTH, font, header, png, split_bar
from imposter_finder.analysis.fc_report import BG, LABEL, TRACK, WHITE, Friend, goals, is_forfeit
from imposter_finder.analysis.fc_xg import team_xg

LEAGUE_EVERY = 5  # 친구전 이만큼 쌓이면 리그 리포트
ELO_START, ELO_K = 1500, 16  # 같은 상대와 자주 붙어 K가 크면 몇 경기에 순위가 뒤집힌다 (30이면 20경기에 ±150)
STYLE_MIN_GAMES = 5  # 플레이 스타일은 이 경기 수 이상만 (적으면 들쭉날쭉)
UP, DOWN, SAME = (62, 207, 142), (239, 91, 91), (120, 126, 138)
ATTACK, BUILD, RESULT = (240, 96, 84), (240, 180, 41), (225, 229, 236)
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
STYLE_METRICS = [  # (이름, 묶음 색, 값)
    ("경기당 골", ATTACK, lambda s: s["goals"] / s["n"]),
    ("경기당 xG", ATTACK, lambda s: s["xg"] / s["n"]),
    ("결정력", ATTACK, lambda s: s["goals"] / max(s["xg"], 0.1)),
    ("패스 성공률", BUILD, lambda s: s["pass_ok"] / max(s["pass_try"], 1)),
    ("스루패스", BUILD, lambda s: s["through"] / s["n"]),
    ("태클 성공률", BUILD, lambda s: s["tackle_ok"] / max(s["tackle_try"], 1)),
    ("드리블", BUILD, lambda s: s["dribble"] / s["n"]),
    ("헤더 슈팅", RESULT, lambda s: s["headers"] / max(s["shots"], 1)),
    ("박스 밖 슈팅", RESULT, lambda s: s["outside"] / max(s["shots"], 1)),
    ("승률", RESULT, lambda s: s["wins"] / s["n"]),
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
            t["wins"] += goals(me) > goals(op)
            t["xg"] += team_xg(me)
            t["shots"] += me["shoot"].get("shootTotal") or 0
            t["headers"] += sum(1 for s in me["shootDetail"] if s.get("type") == 3)
            t["outside"] += me["shoot"].get("shootOutPenalty") or 0
            t["pass_ok"] += me["pass"].get("passSuccess") or 0
            t["pass_try"] += me["pass"].get("passTry") or 0
            t["through"] += me["pass"].get("throughPassTry") or 0
            t["tackle_ok"] += me["defence"].get("tackleSuccess") or 0
            t["tackle_try"] += me["defence"].get("tackleTry") or 0
            t["dribble"] += me["matchDetail"].get("dribble") or 0
    return {o: dict(t) for o, t in totals.items()}


# ── 이미지 ──────────────────────────────────────────────
def _canvas(height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, height), BG)
    return img, ImageDraw.Draw(img)


def render_table(rows: list[Row], games: int) -> bytes:
    """순위 · Elo | 이번 구간 승무패·득실·xG 득실 | 통산 경기·승무패·승률·득실."""
    row_h = 58
    height = 128 + row_h * len(rows) + 16
    img, dr = _canvas(height)
    header(dr, "친구 리그", [])
    groups = [(f"최근 {games}경기", 420, 640), ("통산", 668, WIDTH - PAD)]
    for label, x0, x1 in groups:
        dr.text(((x0 + x1) / 2, 64), label, font=font(14, 6), fill=WHITE, anchor="mm")
        dr.line((x0, 76, x1, 76), fill=TRACK, width=1)
    cols = [("순위", PAD + 8, "la"), ("감독", PAD + 84, "la"), ("Elo", 380, "ra"),
            ("승 무 패", 500, "ra"), ("득실", 568, "ra"), ("xG 득실", 640, "ra"),
            ("경기", 712, "ra"), ("승 무 패", 812, "ra"), ("승률", 890, "ra"), ("득실", WIDTH - PAD, "ra")]
    for label, x, anchor in cols:
        dr.text((x, 90), label, font=font(14), fill=LABEL, anchor=anchor)
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
        dr.text((PAD + 84, cy), row.name, font=font(22, 6), fill=WHITE, anchor="lm")
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


def render_matchups(pairs: list[dict[str, Any]]) -> bytes:
    """pairs: [{"a", "b", "a_color"..., "total": (w, d, l, gf, ga), "form": ["승",...], "window": (w, d, l)}]"""
    row_h = 92
    height = 80 + row_h * len(pairs) + 8
    img, dr = _canvas(height)
    header(dr, "맞대결", [])
    for n, p in enumerate(pairs):
        y = 80 + n * row_h
        w, d, l, gf, ga = p["total"]
        dr.text((PAD, y + 4), p["a"], font=font(20, 6), fill=WHITE)
        dr.text((WIDTH - PAD, y + 4), p["b"], font=font(20, 6), fill=WHITE, anchor="ra")
        dr.text((WIDTH / 2, y + 2), f"{w}승 {d}무 {l}패 · {gf}:{ga}", font=font(17, 6), fill=WHITE, anchor="ma")
        ww, wd, wl = p["window"]
        dr.text((WIDTH / 2, y + 26), f"이번 구간 {ww}승 {wd}무 {wl}패", font=font(14), fill=LABEL, anchor="ma")
        split_bar(img, PAD, WIDTH - PAD, y + 52, w + d / 2, l + d / 2, height=7, left_color=UP, right_color=DOWN)
        r, gap = 6, 18  # 최근 10경기 점 (왼쪽 사람 기준 승 초록·패 빨강, 오래된 순)
        x0 = WIDTH / 2 - (len(p["form"]) - 1) * gap / 2
        for k, res in enumerate(p["form"]):
            c = UP if res == "승" else DOWN if res == "패" else SAME
            dr.ellipse((x0 + k * gap - r, y + 72 - r, x0 + k * gap + r, y + 72 + r), fill=c)
    return png(img)


def render_styles(people: list[tuple[str, dict[str, float]]], scale: list[tuple[float, float]]) -> bytes:
    """people: [(이름, 경기 합계)], scale: 항목별 (최소, 최대) — 리그 전체 기준."""
    cols = min(len(people), 4)
    cell = (WIDTH - PAD * 2) / cols
    rows_n = math.ceil(len(people) / cols)
    height = 76 + rows_n * 300 + 40
    img, dr = _canvas(height)
    header(dr, "플레이 스타일 · 통산", [(ATTACK, "공격"), (BUILD, "빌드업·수비"), (RESULT, "스타일·결과")])
    for i, (name, totals) in enumerate(people):
        cx = PAD + cell * (i % cols + 0.5)
        cy = 76 + (i // cols) * 300 + 160
        R = min(cell / 2 - 46, 96)
        layer = Image.new("RGBA", (WIDTH * SS, height * SS), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for k in (0.25, 0.5, 0.75, 1):
            ld.ellipse(((cx - R * k) * SS, (cy - R * k) * SS, (cx + R * k) * SS, (cy + R * k) * SS),
                       outline=TRACK + (255,), width=SS)
        n = len(STYLE_METRICS)
        for j, ((label, color, f), (lo, hi)) in enumerate(zip(STYLE_METRICS, scale)):
            v = f(totals)
            r = R * (0.2 + 0.8 * ((v - lo) / (hi - lo) if hi > lo else 1))
            a0, a1 = -90 + j * 360 / n + 1.5, -90 + (j + 1) * 360 / n - 1.5
            ld.pieslice(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), a0, a1, fill=color + (230,))
        layer = layer.resize((WIDTH, height), Image.LANCZOS)
        img.paste(layer, (0, 0), layer)
        for j, (label, _, _) in enumerate(STYLE_METRICS):
            am = math.radians(-90 + (j + 0.5) * 360 / n)
            tx, ty = cx + (R + 14) * math.cos(am), cy + (R + 14) * math.sin(am)
            anchor = "lm" if math.cos(am) > 0.2 else "rm" if math.cos(am) < -0.2 else "mm"
            dr.text((tx, ty), label, font=font(12), fill=LABEL, anchor=anchor)
        dr.text((cx, cy - R - 40), f"{name} · {int(totals['n'])}경기", font=font(18, 6), fill=WHITE, anchor="mm")
    return png(img)


# ── 리포트 ──────────────────────────────────────────────
def _highlights(window: list[dict[str, Any]], history: list[dict[str, Any]], friends: dict[str, Friend]) -> list[str]:
    name = lambda o: friends[o].name if o in friends else "?"
    played = [m for m in window if _played(m)]
    if not played:
        return []
    lines = []
    finishing: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for m in played:
        for side in m["matchInfo"]:
            finishing[side["ouid"]][0] += goals(side)
            finishing[side["ouid"]][1] += team_xg(side)
    best = max(finishing, key=lambda o: finishing[o][0] - finishing[o][1])
    g, x = finishing[best]
    lines.append(f"**결정력** {name(best)} {int(g)}골 (xG {x:.1f}, {g - x:+.1f})")
    worst = min(finishing, key=lambda o: finishing[o][0] - finishing[o][1])
    if worst != best:
        g, x = finishing[worst]
        lines.append(f"**아쉬운 마무리** {name(worst)} {int(g)}골 (xG {x:.1f}, {g - x:+.1f})")
    # 이변: 경기 직전 Elo가 낮았던 쪽이 이긴 경기 중 차이가 가장 큰 것
    upset, gap_best = None, 0.0
    for m in played:
        before = elo([h for h in history if h["matchDate"] < m["matchDate"]])
        a, b = m["matchInfo"]
        winner, loser = (a, b) if goals(a) > goals(b) else (b, a) if goals(b) > goals(a) else (None, None)
        if winner:
            gap = before.get(loser["ouid"], ELO_START) - before.get(winner["ouid"], ELO_START)
            if gap > gap_best:
                upset, gap_best = (winner, loser), gap
    if upset and gap_best >= 30:
        w, l = upset
        lines.append(f"**이변** {name(w['ouid'])} {goals(w)}:{goals(l)} {name(l['ouid'])} (Elo {gap_best:.0f} 낮은 쪽 승리)")
    top = max(played, key=lambda m: goals(m["matchInfo"][0]) + goals(m["matchInfo"][1]))
    a, b = top["matchInfo"]
    lines.append(f"**최다 골 경기** {name(a['ouid'])} {goals(a)}:{goals(b)} {name(b['ouid'])}")
    return lines


def build_league_report(history: list[dict[str, Any]], window: list[dict[str, Any]],
                        friends: dict[str, Friend]) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    """history: 이번 구간까지 모든 친구전(오래된 순), window: 이번 구간 경기. friends: ouid → Friend."""
    rows = standings(history, window, friends)
    active = [r for r in rows if r.games]
    files = [("league.png", render_table(rows, len(window)))]

    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for m in window:
        if _played(m):
            a, b = sorted(side["ouid"] for side in m["matchInfo"])
            if a in friends and b in friends:
                pairs.setdefault((a, b), {})
    pair_rows = []
    for (a, b) in pairs:
        games = [m for m in history if _played(m) and {s["ouid"] for s in m["matchInfo"]} == {a, b}]
        res = lambda m: (lambda me, op: "승" if goals(me) > goals(op) else "패" if goals(me) < goals(op) else "무")(
            *(m["matchInfo"] if m["matchInfo"][0]["ouid"] == a else m["matchInfo"][::-1]))
        gf = sum(goals(next(s for s in m["matchInfo"] if s["ouid"] == a)) for m in games)
        ga = sum(goals(next(s for s in m["matchInfo"] if s["ouid"] == b)) for m in games)
        results = [res(m) for m in games]
        ids = {m["matchId"] for m in window}
        in_window = [res(m) for m in games if m["matchId"] in ids]
        pair_rows.append({"a": friends[a].name, "b": friends[b].name,
                          "total": (results.count("승"), results.count("무"), results.count("패"), gf, ga),
                          "form": results[-10:],
                          "window": (in_window.count("승"), in_window.count("무"), in_window.count("패"))})
    pair_rows.sort(key=lambda p: -sum(p["window"]))
    if pair_rows:
        files.append(("matchups.png", render_matchups(pair_rows)))

    totals = style_totals(history)
    eligible = {o: t for o, t in totals.items() if o in friends and t["n"] >= STYLE_MIN_GAMES}
    people = [(r.name, eligible[r.ouid]) for r in active if r.ouid in eligible]
    if people:
        scale = [(min(f(t) for t in eligible.values()), max(f(t) for t in eligible.values())) for _, _, f in STYLE_METRICS]
        files.append(("styles.png", render_styles(people, scale)))

    lines = _highlights(window, history, friends)
    components: list[dict] = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, _ in files]
    text = "**이번 구간**\n" + "\n".join(lines) if lines else ""
    text += ("\n\n" if text else "") + "-# Elo: 강한 상대를 이기면 많이 오르고, 골 차가 클수록 더 움직여요. 처음은 1500."
    components.append({"type": 10, "content": text})
    payload = {"flags": 32768, "allowed_mentions": {"users": []},
               "components": [{"type": 17, "accent_color": ACCENT, "components": components}]}
    return payload, files
