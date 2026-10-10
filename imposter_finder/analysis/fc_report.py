"""FC 온라인 친구전 리포트: 축구 중계처럼 구역별 이미지 + Gemini 총평·평가.

이미지: 스코어보드 · 경기 MVP/범인 · 경기 기록 · 슈팅맵과 골 타임라인 · 라인업과 평점.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from imposter_finder.analysis.cards import (PAD, SS, WIDTH, _download, font, header, icon, png, split_bar)
from imposter_finder.games.fconline import FcMeta, FcPrices, FcTeamColors, face_image, format_bp, player_image

# 축구 중계 톤: 거의 검정 바탕 + 두 팀의 엠블럼 색
BG, TRACK, LABEL, WHITE = (13, 15, 19), (48, 52, 60), (150, 156, 168), (245, 247, 250)
PITCH, PITCH_DARK, LINE = (24, 74, 46), (19, 62, 38), (255, 255, 255)
FALLBACK_LEFT, FALLBACK_RIGHT = (56, 128, 255), (245, 245, 245)
ACCENT = 0x2ECC71
STYLE = {"glow": 0.22, "crest_alpha": 0.07}  # 바탕에 팀 색 빛과 큰 엠블럼을 얼마나 진하게 (변형 비교용)
MAX_LINE_CHARS, MAX_TRIES = 52, 3


@dataclass(frozen=True)
class Friend:
    name: str
    discord_user_id: str | None


FORFEIT_WIN, FORFEIT_LOSS = 1, 2  # matchEndType: 0 정상, 1 몰수승, 2 몰수패(중도 이탈)
SUB_POSITION = 28  # spposition.json: SUB
SHOT_ON, SHOT_OFF, SHOT_GOAL = 1, 2, 3  # shootDetail.result
HALF_START = {0: 0, 1: 45, 2: 90, 3: 105, 4: 120}  # goalTime 상위 비트: 전반·후반·연장 전후반·승부차기


def goals(side: dict[str, Any]) -> int:
    # 몰수패 쪽은 기록이 비어 있어 표시용 점수를 쓴다
    return side["shoot"].get("goalTotal") or side["shoot"].get("goalTotalDisplay") or 0


def is_forfeit(match: dict[str, Any]) -> bool:
    return any(side["matchDetail"].get("matchEndType") for side in match["matchInfo"])


def minute_of(goal_time: int) -> int:
    """goalTime: 상위 비트는 경기 구간, 하위 24비트는 그 구간의 초. 중계처럼 올림한 분으로."""
    half, seconds = goal_time >> 24, goal_time & 0xFFFFFF
    return HALF_START.get(half, 0) + seconds // 60 + 1


def build_forfeit_report(match: dict[str, Any], head_to_head: list[dict[str, Any]],
                         friends: dict[str, Friend]) -> dict[str, Any]:
    """중도 이탈 경기는 스탯이 없어 한 줄로만 알린다."""
    left, right = sorted(match["matchInfo"], key=lambda side: side["matchDetail"].get("matchEndType") == FORFEIT_LOSS)
    lf, rf = friends[left["ouid"]], friends[right["ouid"]]
    w, d, l, gf, ga = _record(head_to_head, left["ouid"])
    text = (f"## {lf.name} 몰수승\n**{rf.name}** ({right['nickname']}) 중도 이탈\n"
            f"최근 {len(head_to_head)}경기 맞대결 **{lf.name} {w}승 {d}무 {l}패** · {gf}득 {ga}실")
    mention_ids = [f.discord_user_id for f in (lf, rf) if f.discord_user_id]
    return {
        "flags": 32768 | 4096,
        "allowed_mentions": {"users": mention_ids},
        "components": [
            {"type": 10, "content": " ".join(f"<@{uid}>" for uid in mention_ids)},
            {"type": 17, "accent_color": 0x99AAB5, "components": [{"type": 10, "content": text}]},
        ],
    }


# ── 팀 ──────────────────────────────────────────────────
@dataclass
class Team:
    side: dict[str, Any]
    friend: Friend
    crest: str | None
    team_name: str | None
    color: tuple
    value: int | None = None  # 구단가치 (출전 명단 선수 시세 합)

    @property
    def won(self) -> bool:
        return self.side["matchDetail"]["matchResult"] == "승"


def _starters(side: dict) -> list[dict]:
    return [p for p in side["player"] if p["spPosition"] != SUB_POSITION]


def crest_color(url: str | None, fallback: tuple) -> tuple:
    """엠블럼에서 가장 많이 쓰인 선명한 색."""
    if not url:
        return fallback
    try:
        im = Image.open(BytesIO(_download(url))).convert("RGBA").resize((64, 64))
    except Exception:
        return fallback
    counts: dict[tuple, int] = {}
    for r, g, b, a in im.getdata():
        hi, lo = max(r, g, b), min(r, g, b)
        if a < 200 or hi < 60 or hi - lo < 50:  # 투명·검정·흰색·회색은 뺀다
            continue
        key = (r // 24 * 24, g // 24 * 24, b // 24 * 24)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return fallback
    r, g, b = max(counts, key=counts.get)
    # 어두운 바탕에서 보이게 밝기를 끌어올린다
    k = max(1.0, 150 / max(r, g, b))
    return tuple(min(255, int(c * k)) for c in (r, g, b))


def _distinct(a: tuple, b: tuple) -> bool:
    return sum(abs(x - y) for x, y in zip(a, b)) > 120


def _teams(match: dict, friends: dict[str, Friend], colors: FcTeamColors | None) -> tuple[Team, Team]:
    # 이긴 쪽을 왼쪽에 둔다. 무승부면 받은 순서대로
    left, right = sorted(match["matchInfo"], key=lambda side: side["matchDetail"]["matchResult"] != "승")
    teams = []
    for side, fallback in ((left, FALLBACK_LEFT), (right, FALLBACK_RIGHT)):
        found = colors.of_squad([p["spId"] for p in _starters(side)]) if colors else None
        name, crest = found if found else (None, None)
        teams.append(Team(side, friends[side["ouid"]], crest, name, crest_color(crest, fallback)))
    if not _distinct(teams[0].color, teams[1].color):  # 두 팀 색이 비슷하면 오른쪽을 흰색으로
        teams[1].color = FALLBACK_RIGHT
    return teams[0], teams[1]


# ── 바탕 ────────────────────────────────────────────────
def _stage(height: int, left: Team, right: Team) -> Image.Image:
    """중계 그래픽 바탕: 양쪽에 팀 색 빛과 아주 옅은 큰 엠블럼."""
    img = Image.new("RGB", (WIDTH, height), BG)
    glow = Image.new("RGB", (WIDTH, height), BG)
    g = ImageDraw.Draw(glow)
    k = STYLE["glow"]
    mix = lambda c: tuple(int(BG[i] * (1 - k) + c[i] * k) for i in range(3))
    g.ellipse((-WIDTH * 0.25, -height * 0.6, WIDTH * 0.32, height * 1.6), fill=mix(left.color))
    g.ellipse((WIDTH * 0.68, -height * 0.6, WIDTH * 1.25, height * 1.6), fill=mix(right.color))
    img.paste(glow.filter(ImageFilter.GaussianBlur(min(height, 300) * 0.5)))
    for team, x in ((left, -60), (right, WIDTH - 260 + 60)):
        crest = icon(team.crest, 260, radius=0)
        if crest is not None and STYLE["crest_alpha"]:
            alpha = crest.getchannel("A").point(lambda v: int(v * STYLE["crest_alpha"]))
            img.paste(crest.convert("RGB"), (x, (height - 260) // 2), alpha)
    return img


# ── 이미지 ──────────────────────────────────────────────
MVP_GOLD, CULPRIT_RED = (250, 190, 10), (255, 70, 85)
# FC 온라인 강화 배지 (데이터센터 CSS .en_levelN): 글자색, 그라데이션 시작·끝, 밝은 테두리(위·왼쪽), 어두운 테두리
GRADE_STYLES = {0: ((197, 200, 201), (81, 84, 90), (66, 70, 77), (98, 103, 109), (57, 58, 60)),
                2: ((126, 63, 39), (222, 148, 107), (173, 95, 66), (228, 183, 162), (134, 66, 41)),
                5: ((78, 84, 94), (216, 217, 220), (184, 189, 202), (216, 218, 220), (169, 170, 174)),
                8: ((105, 81, 0), (249, 221, 98), (220, 169, 8), (233, 211, 108), (205, 160, 0)),
                11: ((45, 43, 67), None, None, (189, 197, 229), (82, 116, 192))}
PLATINUM_BG = "https://ssl.nexon.com/s2/game/fc/online/obt/datacenter/bg_plt.png"  # 11강 이상 배경


def grade_badge(img: Image.Image, x: float, y: float, grade: int, h: int = 22) -> int:
    """FC 온라인과 같은 강화 배지 (140도 그라데이션 + 입체 테두리 + 숫자). 너비를 돌려준다."""
    level = max(k for k in GRADE_STYLES if k <= max(grade, 0))
    text_color, start, end, light, dark = GRADE_STYLES[level]
    f = font(int(h * 0.68), 6)
    w = max(h, int(ImageDraw.Draw(img).textlength(str(grade), font=f)) + 12)
    big_w, big_h = w * SS, h * SS
    fill = None
    if start is None:
        try:
            fill = Image.open(BytesIO(_download(PLATINUM_BG))).convert("RGB").resize((big_w, big_h))
        except Exception:
            start, end = (200, 210, 240), (120, 140, 200)
    if fill is None:
        ramp = Image.linear_gradient("L").rotate(45, expand=True).resize((big_w, big_h))
        fill = Image.composite(Image.new("RGB", (big_w, big_h), end), Image.new("RGB", (big_w, big_h), start), ramp)
    d = ImageDraw.Draw(fill)
    bw = int(1.5 * SS)
    d.line((0, 0, big_w, 0), fill=light, width=bw * 2)  # 위·왼쪽 밝게, 오른쪽·아래 어둡게
    d.line((0, 0, 0, big_h), fill=light, width=bw * 2)
    d.line((big_w - 1, 0, big_w - 1, big_h), fill=dark, width=bw * 2)
    d.line((0, big_h - 1, big_w, big_h - 1), fill=dark, width=bw * 2)
    mask = Image.new("L", (big_w, big_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big_w - 1, big_h - 1), radius=4 * SS, fill=255)
    img.paste(fill.resize((w, h), Image.LANCZOS), (round(x), round(y)), mask.resize((w, h), Image.LANCZOS))
    ImageDraw.Draw(img).text((x + w / 2, y + h / 2), str(grade), font=f, fill=text_color, anchor="mm")
    return w


def _star(img: Image.Image, cx: float, cy: float, r: float, color: tuple) -> None:
    size = round(r * 2 * SS)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(layer).regular_polygon((size / 2, size / 2 + size * 0.04, size / 2), 5, rotation=0,
                                          fill=color + (255,))
    # 오각형을 별로: 안쪽 꼭짓점을 직접 계산
    import math
    pts = []
    for i in range(10):
        rad = size / 2 if i % 2 == 0 else size / 2 * 0.45
        a = math.pi / 2 + i * math.pi / 5
        pts.append((size / 2 + rad * math.cos(a), size / 2 - rad * math.sin(a)))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(pts, fill=color + (255,))
    layer = layer.resize((round(r * 2), round(r * 2)), Image.LANCZOS)
    img.paste(layer, (round(cx - r), round(cy - r)), layer)


def _magnifier(img: Image.Image, cx: float, cy: float, r: float, color: tuple) -> None:
    size = round(r * 2 * SS)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lens = size * 0.62
    d.ellipse((0, 0, lens, lens), outline=color + (255,), width=round(size * 0.13))
    d.line((lens * 0.85, lens * 0.85, size - 1, size - 1), fill=color + (255,), width=round(size * 0.16))
    layer = layer.resize((round(r * 2), round(r * 2)), Image.LANCZOS)
    img.paste(layer, (round(cx - r), round(cy - r)), layer)


def _ball(img: Image.Image, cx: float, cy: float, r: float) -> None:
    """작은 축구공 (이미지 글꼴에 이모지가 없어 직접 그린다)."""
    size = round(r * 2 * SS)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((0, 0, size - 1, size - 1), fill=WHITE + (255,), outline=(30, 30, 30, 255), width=max(SS, size // 14))
    c, k = size / 2, size * 0.2
    d.regular_polygon((c, c, k), 5, fill=(30, 30, 30, 255))
    layer = layer.resize((round(r * 2), round(r * 2)), Image.LANCZOS)
    img.paste(layer, (round(cx - r), round(cy - r)), layer)


def _assist_mark(img: Image.Image, cx: float, cy: float, r: float) -> None:
    dr = ImageDraw.Draw(img)
    dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(90, 96, 108))
    dr.text((cx, cy), "A", font=font(int(r * 1.5), 6), fill=WHITE, anchor="mm")


def _boot(img: Image.Image, cx: float, cy: float, r: float) -> None:
    """슈팅 수 표시용 작은 과녁."""
    dr = ImageDraw.Draw(img)
    dr.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LABEL, width=2)
    dr.ellipse((cx - r * 0.35, cy - r * 0.35, cx + r * 0.35, cy + r * 0.35), fill=LABEL)


def _chip(img: Image.Image, x: float, y: float, draw_icon, text: str, h: int = 30) -> int:
    """반투명 칩: 아이콘 + 숫자. 너비를 돌려준다."""
    dr = ImageDraw.Draw(img)
    f = font(16, 6)
    w = int(dr.textlength(text, font=f)) + h + 14
    _glass_box(img, (x, y, x + w, y + h), radius=h // 2, alpha=34)
    draw_icon(img, x + h / 2 + 2, y + h / 2, h * 0.3)
    dr.text((x + h + 2, y + h / 2), text, font=f, fill=WHITE, anchor="lm")
    return w


def _glass_box(img: Image.Image, box: tuple, radius: int = 16, alpha: int = 30) -> None:
    x0, y0, x1, y1 = (round(v) for v in box)
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=alpha)
    img.paste(Image.new("RGB", (w, h), WHITE), (x0, y0), mask.resize((w, h), Image.LANCZOS))


def _segment_bar(img: Image.Image, x0: float, x1: float, y: float, stops: list[tuple[float, tuple]],
                 height: int = 8) -> None:
    """여러 색 구간을 한 둥근 막대로."""
    x0, x1, y = round(x0), round(x1), round(y)
    w = x1 - x0
    fill = Image.new("RGB", (w, height), TRACK)
    fd = ImageDraw.Draw(fill)
    start = 0
    for end, color in stops:
        end = round(end) - x0
        if end > start:
            fd.rectangle((start, 0, end, height), fill=color)
        start = end
    mask = Image.new("L", (w * SS, height * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, height * SS - 1), radius=height * SS // 2, fill=255)
    img.paste(fill, (x0, y), mask.resize((w, height), Image.LANCZOS))


def render_scoreboard(left: Team, right: Team, scorers: dict[str, list[str]], form: list[str]) -> bytes:
    """엠블럼 · 친구 이름 · 구단가치 · 큰 점수 · 득점자 · 최근 맞대결 (왼쪽 친구 기준 결과, 오래된 순)."""
    lines = max(len(scorers[left.side["ouid"]]), len(scorers[right.side["ouid"]]), 1)
    height = 196 + lines * 24 + (70 if form else 0)
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    cx = WIDTH // 2
    draw_game = not (left.won or right.won)
    for team, sign in ((left, -1), (right, 1)):
        crest_x = cx + sign * 400
        crest = icon(team.crest, 92, radius=0)
        if crest:
            img.paste(crest, (crest_x - 46, 26), crest)
        else:  # 팀컬러가 없으면 팀 색 원
            dr.ellipse((crest_x - 40, 32, crest_x + 40, 112), fill=team.color)
        if team.team_name:
            dr.text((crest_x, 126), team.team_name, font=font(14), fill=LABEL, anchor="ma")
        name_x = cx + sign * 160
        anchor = "ra" if sign < 0 else "la"
        dr.text((name_x, 34), team.friend.name, font=font(34, 6), fill=WHITE if team.won or draw_game else LABEL,
                anchor=anchor)
        dr.text((name_x, 80), team.side["nickname"], font=font(15), fill=LABEL, anchor=anchor)
        if team.value:
            dr.text((name_x, 102), f"구단가치 {format_bp(team.value)}", font=font(15, 6), fill=WHITE, anchor=anchor)
        bar_x0 = name_x - 60 if sign < 0 else name_x
        dr.rectangle((bar_x0, 130, bar_x0 + 60, 133), fill=team.color)
    lg, rg = goals(left.side), goals(right.side)
    dr.text((cx - 22, 18), str(lg), font=font(84, 6), fill=WHITE if lg >= rg else LABEL, anchor="ra")
    dr.text((cx, 26), ":", font=font(64, 6), fill=LABEL, anchor="ma")
    dr.text((cx + 22, 18), str(rg), font=font(84, 6), fill=WHITE if rg >= lg else LABEL, anchor="la")
    dr.rounded_rectangle((cx - 44, 126, cx + 44, 152), radius=13, fill=TRACK)
    dr.text((cx, 130), "경기 종료", font=font(14, 6), fill=WHITE, anchor="ma")
    for team, sign in ((left, -1), (right, 1)):
        for n, line in enumerate(scorers[team.side["ouid"]]):
            dr.text((cx + sign * 60, 172 + n * 24), line, font=font(16), fill=WHITE, anchor="ra" if sign < 0 else "la")
    if scorers[left.side["ouid"]] or scorers[right.side["ouid"]]:
        _ball(img, cx, 182, 8)
    if form:  # 최근 맞대결: 경기마다 이긴 사람 색 점 (오래된 → 최근)
        y = height - 52
        w, d, l = form.count("승"), form.count("무"), form.count("패")
        dr.text((cx, y - 8), f"최근 {len(form)}경기 맞대결", font=font(14), fill=LABEL, anchor="ma")
        dr.text((cx - 150, y + 24), f"{left.friend.name} {w}승", font=font(17, 6), fill=WHITE, anchor="rm")
        dr.text((cx + 150, y + 24), f"{l}승 {right.friend.name}", font=font(17, 6), fill=WHITE, anchor="lm")
        step = 24
        x0 = cx - step * (len(form) - 1) / 2
        for n, result in enumerate(form):
            color = left.color if result == "승" else right.color if result == "패" else TRACK
            r = 8 if n < len(form) - 1 else 10  # 이번 경기는 조금 크게
            x = x0 + n * step
            dr.ellipse((x - r, y + 24 - r, x + r, y + 24 + r), fill=color)
            if result == "무":
                dr.text((x, y + 24), "무", font=font(10, 6), fill=LABEL, anchor="mm")
    return png(img)


def render_potm(cards: list[dict], left: Team, right: Team) -> bytes:
    """경기 MVP·범인: 선수 사진 + 시즌·이름 + 강화 배지 + 평점 + 골·도움·슈팅 칩."""
    height = 260
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    half = WIDTH // 2
    for n, card in enumerate(cards):
        x0 = PAD + n * half
        box = (x0, 20, x0 + half - PAD * 1.5, height - 20)
        _glass_box(img, box, alpha=26)
        photo_url = player_image(card["spid"])
        if photo_url:
            try:
                photo = Image.open(BytesIO(_download(photo_url))).convert("RGBA")
                photo.thumbnail((200, 200), Image.LANCZOS)
                img.paste(photo, (int(box[0] + 12), int(box[3] - photo.height)), photo)
            except Exception:
                pass
        tx = int(box[0] + 214)
        is_mvp = card["badge"] == "MVP"
        accent = MVP_GOLD if is_mvp else CULPRIT_RED
        (_star if is_mvp else _magnifier)(img, tx + 8, 50, 8, accent)
        dr.text((tx + 22, 50), "경기 MVP" if is_mvp else "범인", font=font(15, 6), fill=accent, anchor="lm")
        # 시즌 아이콘과 이름은 같은 가운데 높이에
        name_y = 88
        nx = tx
        season = icon(card.get("season_img"), 24, radius=0) if card.get("season_img") else None
        if season:
            img.paste(season, (tx, name_y - season.height // 2), season)
            nx = tx + season.width + 8
        dr.text((nx, name_y), card["name"], font=font(24, 6), fill=WHITE, anchor="lm")
        gw = grade_badge(img, tx, 112, card["grade"], h=22)
        dr.text((tx + gw + 10, 123), card["friend"], font=font(15), fill=LABEL, anchor="lm")
        rating = f"{card['rating']:.1f}"
        dr.text((tx, 186), rating, font=font(44, 6), fill=accent, anchor="ls")
        dr.text((tx + dr.textlength(rating, font=font(44, 6)) + 8, 184), "평점", font=font(15), fill=LABEL, anchor="ls")
        cx = tx
        for kind, value in card["chips"]:
            draw_icon = {"goal": _ball, "assist": _assist_mark, "shot": _boot}[kind]
            cx += _chip(img, cx, 200, draw_icon, str(value), h=28) + 8
    return png(img)


def render_stats(rows: list[tuple], left: Team, right: Team) -> bytes:
    """경기 기록: 왼쪽 값 · 가운데 항목 · 오른쪽 값, 아래 팀 색 비율 막대."""
    row_h = 50
    height = 90 + row_h * len(rows) + 10
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    top = header(dr, "경기 기록", [(left.color, left.friend.name), (right.color, right.friend.name)])
    x0, x1 = PAD + 90, WIDTH - PAD - 90
    for n, (label, lv, lt, rv, rt) in enumerate(rows):
        y = top + n * row_h
        dr.text((PAD, y + 4), lt, font=font(22, 6), fill=WHITE if lv >= rv else LABEL)
        dr.text((WIDTH - PAD, y + 4), rt, font=font(22, 6), fill=WHITE if rv >= lv else LABEL, anchor="ra")
        dr.text((WIDTH // 2, y + 2), label, font=font(16), fill=LABEL, anchor="ma")
        split_bar(img, x0, x1, y + 28, lv, rv, height=7, left_color=left.color, right_color=right.color)
    return png(img)


def _half_pitch(w: int, h: int, goal_at_top: bool = True) -> Image.Image:
    """세로로 세운 반쪽 경기장 (골대가 위). 잔디 줄무늬 + 반투명 흰 선."""
    big = Image.new("RGB", (w * SS, h * SS), PITCH)
    d = ImageDraw.Draw(big)
    stripes = 7
    for i in range(0, stripes, 2):
        d.rectangle((0, i * h * SS / stripes, w * SS, (i + 1) * h * SS / stripes), fill=PITCH_DARK)
    lines = Image.new("L", big.size, 0)
    ld = ImageDraw.Draw(lines)
    lw = 2 * SS
    W, H = w * SS - 1, h * SS - 1
    sx, sy = W / 68, H / 52.5  # 미터 → 픽셀 (가로 68m, 세로 52.5m)
    ld.rectangle((0, 0, W, H), outline=110, width=lw)
    for depth, half_w in ((16.5, 20.16), (5.5, 9.16)):
        ld.rectangle((W / 2 - half_w * sx, 0, W / 2 + half_w * sx, depth * sy), outline=110, width=lw)
    ld.ellipse((W / 2 - 3 * SS, 11 * sy - 3 * SS, W / 2 + 3 * SS, 11 * sy + 3 * SS), fill=110)
    ld.arc((W / 2 - 9.15 * sx, H - 9.15 * sy, W / 2 + 9.15 * sx, H + 9.15 * sy), 180, 360, fill=110, width=lw)
    ld.rectangle((W / 2 - 3.66 * sx, 0, W / 2 + 3.66 * sx, 2 * SS), fill=200)  # 골대
    big.paste(Image.new("RGB", big.size, LINE), (0, 0), lines)
    out = big.resize((w, h), Image.LANCZOS)
    return out if goal_at_top else out.transpose(Image.FLIP_TOP_BOTTOM)


def render_shots(left: Team, right: Team, names: dict[int, str]) -> bytes:
    """왼쪽: 세로 골 타임라인. 오른쪽: 두 팀의 공격 진영 슈팅맵 (골대가 위)."""
    top, tl_w, gap = 76, 190, 20
    pw = (WIDTH - PAD * 2 - tl_w - gap * 2) // 2
    ph = int(pw * 52.5 / 68)
    height = top + 30 + ph + 24
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    header(dr, "슈팅맵", [])
    lx = WIDTH - PAD
    for kind, text in (("off", "빗나감"), ("on", "유효 슈팅"), ("goal", "골")):
        lx -= dr.textlength(text, font=font(15))
        dr.text((lx, 23), text, font=font(15), fill=LABEL)
        lx -= 16
        if kind == "goal":
            dr.ellipse((lx - 7, 23, lx + 7, 37), fill=WHITE)
        elif kind == "on":
            dr.ellipse((lx - 6, 24, lx + 6, 36), outline=WHITE, width=2)
        else:
            dr.ellipse((lx - 4, 26, lx + 4, 34), fill=(120, 126, 138))
        lx -= 22

    # ── 골 타임라인 (세로): 0'이 위, 왼쪽 팀 골은 선 왼쪽, 오른쪽 팀 골은 선 오른쪽
    goal_events = []
    for team in (left, right):
        for shot in team.side.get("shootDetail", []):
            if shot.get("result") == SHOT_GOAL:
                goal_events.append((minute_of(shot["goalTime"]), team, names.get(shot["spId"], "")))
    end = max([90] + [m for m, _, _ in goal_events])
    line_x = PAD + tl_w // 2
    ty0, ty1 = top + 30, top + 30 + ph
    ty = lambda m: ty0 + (ty1 - ty0) * min(m, end) / end
    _segment_bar(img, line_x - 2, line_x + 2, ty0, [(line_x + 2, TRACK)], height=ty1 - ty0)
    for m in (0, 45, 90):
        dr.text((line_x, ty(m)), f"{m}'", font=font(12), fill=LABEL, anchor="mm", stroke_width=3, stroke_fill=BG)
    last_y = {id(left): -99.0, id(right): -99.0}
    for minute, team, name in sorted(goal_events, key=lambda g: g[0]):
        y = ty(minute)
        side = -1 if team is left else 1
        _ball(img, line_x + side * 14, y, 7)
        label_y = max(y, last_y[id(team)] + 34)  # 가까운 골은 아래로 밀어 겹치지 않게
        last_y[id(team)] = label_y
        anchor = "rm" if side < 0 else "lm"
        tx = line_x + side * 28
        dr.text((tx, label_y - 7), f"{minute}'", font=font(13, 6), fill=team.color, anchor=anchor)
        dr.text((tx, label_y + 9), name, font=font(13, 6), fill=WHITE, anchor=anchor)

    # ── 슈팅맵: 팀마다 상대 골대 쪽 반쪽 경기장
    for n, team in enumerate((left, right)):
        px0 = PAD + tl_w + gap + n * (pw + gap)
        py0 = top + 30
        dr.text((px0, top + 6), team.friend.name, font=font(15, 6), fill=WHITE)
        dr.rectangle((px0, top + 26, px0 + 28, top + 28), fill=team.color)
        img.paste(_half_pitch(pw, ph), (px0, py0))
        layer = Image.new("RGBA", (pw * SS, ph * SS), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        to_px = lambda x, y: (min(max(y, 0), 1) * pw * SS, min(max((1 - x) / 0.5, 0), 1) * ph * SS)
        shots = sorted(team.side.get("shootDetail", []), key=lambda s: s.get("result") == SHOT_GOAL)  # 골을 맨 위에
        for shot in shots:
            px, py = to_px(shot["x"], shot["y"])
            result = shot.get("result")
            if result == SHOT_GOAL:
                if shot.get("assist") and shot.get("assistX") is not None:
                    _dashed(ld, to_px(shot["assistX"], shot["assistY"]), (px, py), WHITE + (130,), 2 * SS)
                r = 10 * SS
                ld.ellipse((px - r, py - r, px + r, py + r), fill=team.color + (255,), outline=WHITE + (255,),
                           width=3 * SS)
            elif result == SHOT_ON:
                r = 8 * SS
                ld.ellipse((px - r, py - r, px + r, py + r), outline=team.color + (255,), width=3 * SS)
            else:
                r = 5 * SS
                ld.ellipse((px - r, py - r, px + r, py + r), fill=WHITE + (120,))
        layer = layer.resize((pw, ph), Image.LANCZOS)
        img.paste(layer, (px0, py0), layer)
    return png(img)


def _dashed(draw: ImageDraw.ImageDraw, a: tuple, b: tuple, color: tuple, width: int, dash: int = 10) -> None:
    (x1, y1), (x2, y2) = a, b
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1)
    steps = max(int(length / (dash * SS)), 1)
    for i in range(0, steps, 2):
        t0, t1 = i / steps, min((i + 1) / steps, 1)
        draw.line((x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0, x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1),
                  fill=color, width=width)


# spposition 번호 → (자기 골대에서 깊이 0~1, 공격 방향 기준 왼쪽 0 ~ 오른쪽 1)
POSITION_XY = {0: (0.04, 0.5), 1: (0.17, 0.5), 2: (0.36, 0.92), 3: (0.25, 0.9), 4: (0.2, 0.68), 5: (0.2, 0.5),
               6: (0.2, 0.32), 7: (0.25, 0.1), 8: (0.36, 0.08), 9: (0.38, 0.64), 10: (0.38, 0.5), 11: (0.38, 0.36),
               12: (0.56, 0.9), 13: (0.54, 0.68), 14: (0.54, 0.5), 15: (0.54, 0.32), 16: (0.56, 0.1),
               17: (0.7, 0.72), 18: (0.7, 0.5), 19: (0.7, 0.28), 20: (0.84, 0.7), 21: (0.84, 0.5), 22: (0.84, 0.3),
               23: (0.82, 0.9), 24: (0.9, 0.64), 25: (0.9, 0.5), 26: (0.9, 0.36), 27: (0.82, 0.1)}


def rating_color(rating: float) -> tuple:
    """축구 앱에서 흔한 평점 색: 9+ 파랑, 8+ 진초록, 7+ 초록, 6+ 주황, 그 아래 빨강."""
    if rating >= 9:
        return (30, 136, 229)
    if rating >= 8:
        return (24, 170, 90)
    if rating >= 7:
        return (110, 190, 70)
    if rating >= 6:
        return (240, 150, 40)
    return (230, 70, 60)


def _face(url: str, d: int, ring: tuple | None) -> Image.Image | None:
    """선수 얼굴을 원 안에. 팀 색 원 바탕 위에 얹는다."""
    size = d * SS
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    disc = Image.new("L", (size, size), 0)
    ImageDraw.Draw(disc).ellipse((0, 0, size - 1, size - 1), fill=255)
    layer.paste(Image.new("RGBA", (size, size), (34, 38, 46, 255)), (0, 0), disc)
    try:
        face = Image.open(BytesIO(_download(url))).convert("RGBA")
        face = face.resize((size, round(face.height * size / face.width)), Image.LANCZOS)
        crop = face.crop((0, 0, size, size))
        layer.paste(crop, (0, 0), ImageChops.multiply(crop.getchannel("A"), disc))
    except Exception:
        pass
    if ring:
        ImageDraw.Draw(layer).ellipse((0, 0, size - 1, size - 1), outline=ring + (255,), width=3 * SS)
    return layer.resize((d, d), Image.LANCZOS)


def render_lineup(left: Team, right: Team, meta: FcMeta, names: dict[int, str], mvp: int, culprit: int) -> bytes:
    """라인업: 팀마다 세로 반쪽 경기장, 포메이션 위치에 얼굴 · 평점 · 시즌 · 이름."""
    top, gap = 76, 20
    pw = (WIDTH - PAD * 2 - gap) // 2
    ph = 440
    height = top + 34 + ph + 14
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    header(dr, "라인업 · 평점", [(rating_color(9), "9+"), (rating_color(8), "8+"), (rating_color(7), "7+"),
                               (rating_color(6), "6+"), (rating_color(5), "6 미만")])
    for n, team in enumerate((left, right)):
        px0, py0 = PAD + n * (pw + gap), top + 34
        crest = icon(team.crest, 26, radius=0)
        nx = px0
        if crest:
            img.paste(crest, (px0, top + 2), crest)
            nx += 34
        title = team.friend.name + (f" · {team.team_name}" if team.team_name else "")
        dr.text((nx, top + 15), title, font=font(16, 6), fill=WHITE, anchor="lm")
        img.paste(_half_pitch(pw, ph, goal_at_top=False), (px0, py0))  # 자기 골대가 아래, 위로 공격
        for p in _starters(team.side):
            depth, across = POSITION_XY.get(p["spPosition"], (0.5, 0.5))
            x = px0 + 36 + across * (pw - 72)
            y = py0 + ph - 40 - depth * (ph - 90)
            st = p["status"]
            badge = "MVP" if p["spId"] == mvp else "범인" if p["spId"] == culprit else None
            ring = MVP_GOLD if badge == "MVP" else CULPRIT_RED if badge == "범인" else None
            face = _face(face_image(p["spId"]), 46, ring)
            img.paste(face, (round(x - 23), round(y - 30)), face)
            # 평점 칩: 얼굴 오른쪽 아래
            rating = f"{st['spRating']:.1f}"
            rw = dr.textlength(rating, font=font(12, 6)) + 10
            dr.rounded_rectangle((x + 8, y + 2, x + 8 + rw, y + 20), radius=9, fill=rating_color(st["spRating"]))
            dr.text((x + 8 + rw / 2, y + 11), rating, font=font(12, 6), fill=WHITE, anchor="mm")
            # 시즌 + 이름
            season = icon(meta.season_image(p["spId"]), 14, radius=0)
            name = names.get(p["spId"], "")
            nw = dr.textlength(name, font=font(12, 6)) + (17 if season else 0)
            sx = x - nw / 2
            if season:
                img.paste(season, (round(sx), round(y + 22)), season)
                sx += 17
            dr.text((sx, y + 29), name, font=font(12, 6), fill=WHITE, anchor="lm", stroke_width=2, stroke_fill=PITCH_DARK)
            for k in range(st.get("goal", 0)):  # 얼굴 왼쪽 위에 공, 그 옆에 도움
                _ball(img, x - 22 + k * 12, y - 28, 6)
            for k in range(st.get("assist", 0)):
                _assist_mark(img, x - 22 + (st.get("goal", 0) + k) * 12, y - 28, 6)
            if badge:
                color = ring
                tw = dr.textlength(badge, font=font(10, 6))
                dr.rounded_rectangle((x + 10, y - 34, x + 22 + tw, y - 20), radius=7, fill=color)
                dr.text((x + 16 + tw / 2, y - 27), badge, font=font(10, 6), fill=BG, anchor="mm")
    return png(img)


# ── 리포트 ──────────────────────────────────────────────
def _record(games: list[dict[str, Any]], ouid: str) -> tuple[int, int, int, int, int]:
    w = d = l = gf = ga = 0
    for game in games:
        me = next(side for side in game["matchInfo"] if side["ouid"] == ouid)
        op = next(side for side in game["matchInfo"] if side["ouid"] != ouid)
        result = me["matchDetail"]["matchResult"]
        w, d, l = w + (result == "승"), d + (result == "무"), l + (result == "패")
        gf, ga = gf + goals(me), ga + goals(op)
    return w, d, l, gf, ga


def _form(games: list[dict[str, Any]], ouid: str) -> list[str]:
    """맞대결 결과 (ouid 기준 승·무·패), 오래된 경기부터."""
    ordered = sorted(games, key=lambda g: g.get("matchDate", ""))
    return [next(side for side in g["matchInfo"] if side["ouid"] == ouid)["matchDetail"]["matchResult"]
            for g in ordered]


def _short(name: str) -> str:
    """중계처럼 성(마지막 단어)만. '로빈 반페르시' → '반페르시'."""
    return name.split()[-1] if name else name


def _pct(success: int, attempt: int) -> str:
    return f"{round(success / attempt * 100)}%" if attempt else "-"


def _stat_rows(l: dict, r: dict) -> list[tuple]:
    def pair(label: str, get, fmt=str) -> tuple:
        lv, rv = get(l), get(r)
        return label, lv, fmt(lv), rv, fmt(rv)

    rows = [pair("점유율", lambda s: s["matchDetail"]["possession"], lambda v: f"{v}%"),
            pair("슈팅", lambda s: s["shoot"]["shootTotal"]),
            pair("유효 슈팅", lambda s: s["shoot"]["effectiveShootTotal"]),
            ("패스 성공률", l["pass"]["passSuccess"] / max(l["pass"]["passTry"], 1),
             _pct(l["pass"]["passSuccess"], l["pass"]["passTry"]),
             r["pass"]["passSuccess"] / max(r["pass"]["passTry"], 1), _pct(r["pass"]["passSuccess"], r["pass"]["passTry"])),
            pair("스루패스 성공", lambda s: s["pass"]["throughPassSuccess"]),
            pair("태클 성공", lambda s: s["defence"]["tackleSuccess"]),
            pair("코너킥", lambda s: s["matchDetail"]["cornerKick"]),
            pair("파울", lambda s: s["matchDetail"]["foul"]),
            pair("평균 평점", lambda s: s["matchDetail"]["averageRating"] or 0, lambda v: f"{v:.1f}")]
    return rows


def _mvp(match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    played = [(side, p) for side in match["matchInfo"] for p in side["player"] if p["status"].get("spRating", 0) > 0]
    return max(played, key=lambda item: item[1]["status"]["spRating"])


def _culprit(match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """선발 중 평점이 가장 낮은 선수. 교체로 잠깐 뛴 선수는 평점이 낮게 나오기 쉬워 뺀다."""
    starters = [(side, p) for side in match["matchInfo"] for p in _starters(side) if p["status"].get("spRating", 0) > 0]
    return min(starters, key=lambda item: item[1]["status"]["spRating"])


def build_report(match: dict[str, Any], head_to_head: list[dict[str, Any]], friends: dict[str, Friend],
                 meta: FcMeta, colors: FcTeamColors | None, gemini_api_key: str | None,
                 gemini_model: str, prices: FcPrices | None = None) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    """반환: (Components V2 payload, [(파일 이름, PNG)]). friends는 ouid → Friend."""
    left, right = _teams(match, friends, colors)
    if prices:
        for team in (left, right):
            team.value = prices.squad_value([p for p in team.side["player"] if p["spGrade"]])
    team_of = {left.side["ouid"]: left, right.side["ouid"]: right}
    names = {p["spId"]: _short(meta.player_name(p["spId"])) for t in (left, right) for p in t.side["player"]}

    scorers: dict[str, list[str]] = {left.side["ouid"]: [], right.side["ouid"]: []}
    for team in (left, right):
        by_player: dict[int, list[int]] = {}
        for shot in team.side.get("shootDetail", []):
            if shot.get("result") == SHOT_GOAL:
                by_player.setdefault(shot["spId"], []).append(minute_of(shot["goalTime"]))
        for spid, minutes in sorted(by_player.items(), key=lambda item: min(item[1])):
            scorers[team.side["ouid"]].append(f"{names.get(spid, '')} {', '.join(f'{m}' + chr(39) for m in sorted(minutes))}")

    mvp_side, mvp = _mvp(match)
    culprit_side, culprit = _culprit(match)
    cards = []
    for badge, side, p in (("MVP", mvp_side, mvp), ("범인", culprit_side, culprit)):
        st = p["status"]
        chips = [(kind, st[key]) for kind, key in (("goal", "goal"), ("assist", "assist"), ("shot", "shoot"))
                 if st.get(key)]
        cards.append({"badge": badge, "spid": p["spId"], "name": meta.player_name(p["spId"]),
                      "season_img": meta.season_image(p["spId"]), "grade": p["spGrade"], "rating": st["spRating"],
                      "friend": team_of[side["ouid"]].friend.name, "team": team_of[side["ouid"]], "chips": chips})

    files = [("score.png", render_scoreboard(left, right, scorers, _form(head_to_head, left.side["ouid"]))),
             ("potm.png", render_potm(cards, left, right)),
             ("stats.png", render_stats(_stat_rows(left.side, right.side), left, right)),
             ("shots.png", render_shots(left, right, names)),
             ("lineup.png", render_lineup(left, right, meta, names, mvp["spId"], culprit["spId"]))]

    ai = _evaluate(left, right, cards, meta, gemini_api_key, gemini_model)
    mention_ids = [t.friend.discord_user_id for t in (left, right) if t.friend.discord_user_id]
    components: list[dict] = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, _ in files]
    words = []
    if ai.get("summary"):
        words.append(f"**총평**\n{ai['summary']}")
    if ai.get("lines"):
        words.append("**평가**\n" + "\n".join(ai["lines"]))
    if words:
        components.append({"type": 10, "content": "\n\n".join(words)})
    payload = {
        "flags": 32768,  # Components V2 (@silent는 전송할 때 붙는다)
        "allowed_mentions": {"users": mention_ids},
        "components": [{"type": 10, "content": " ".join(f"<@{uid}>" for uid in mention_ids)},
                       {"type": 17, "accent_color": ACCENT, "components": components}],
    }
    return payload, files


def _evaluate(left: Team, right: Team, cards: list[dict], meta: FcMeta, api_key: str | None, model: str) -> dict:
    """Gemini: 경기 한 문장 + 두 사람에게 한마디씩 (해설자·코치 톤). 실패하면 빈 값."""
    if not api_key:
        return {}

    def facts(team: Team) -> dict:
        side = team.side
        top = sorted((p for p in side["player"] if p["status"].get("spRating", 0) > 0),
                     key=lambda p: -p["status"]["spRating"])[:3]
        sh, ps = side["shoot"], side["pass"]
        return {"이름": team.friend.name, "결과": side["matchDetail"]["matchResult"], "골": goals(side),
                "팀컬러": team.team_name or "없음", "점유율": side["matchDetail"]["possession"],
                "슈팅": sh["shootTotal"], "유효 슈팅": sh["effectiveShootTotal"], "박스 안 슈팅": sh["shootInPenalty"],
                "박스 밖 슈팅": sh["shootOutPenalty"], "헤딩 슈팅": sh["shootHeading"],
                "패스 성공률": _pct(ps["passSuccess"], ps["passTry"]),
                "스루패스": f"{ps['throughPassSuccess']}/{ps['throughPassTry']}",
                "태클": f"{side['defence']['tackleSuccess']}/{side['defence']['tackleTry']}",
                "상위 선수": [f"{meta.player_name(p['spId'])} 평점 {p['status']['spRating']:.1f}" for p in top]}

    names = [left.friend.name, right.friend.name]
    keys = ["summary"] + names
    prompt = ("FC 온라인 1:1 친선 경기 기록이야. 친구끼리 보는 디스코드 리포트에 넣을 한마디를 써줘. "
              "스탯은 이미 이미지로 보여주니까 숫자를 읊지 말고, 축구 해설자나 감독처럼 느낌과 조언을 말해줘.\n"
              f"summary: 이 경기를 한 문장으로 ({MAX_LINE_CHARS - 4}자 이내, 넘으면 안 됨). 무엇이 승부를 갈랐는지.\n"
              f"각 이름: 그 친구에게 하는 한마디 ({MAX_LINE_CHARS - 7}자 이내로 짧게, 넘으면 안 됨, 1~2문장, 이름으로 시작, 은/는 받침에 맞게). "
              "이긴 사람은 무엇이 좋았는지 칭찬 (예: 기가 막히네요, 결정력이 빛났어요), "
              "진 사람은 무엇이 아쉬웠는지와 다음에 해볼 것 (예: 다음 경기엔 ~해보세요). 무승부면 둘 다 공평하게.\n"
              "판단은 기록(점유율, 슈팅 위치, 패스, 태클, 상위 선수)에 근거하고, 숫자는 꼭 필요할 때 하나만.\n"
              "좋은 예 (형식만 참고, 내용은 이 경기 기록으로): '○○는 측면을 끝까지 흔들었네요. 오늘 경기의 주인공입니다.'\n"
              "좋은 예: '○○는 점유는 잘했는데 마무리가 아쉬웠어요. 다음엔 한 번 더 패스해보세요.'\n"
              "나쁜 예: '○○는 유효슈팅 5개 중 4골을 기록했습니다.' (숫자 나열), 예시 문장이나 표현을 그대로 베끼기\n"
              "기록에 없는 장면(역습, 세트피스 골 등)은 지어내지 마. 이모지·줄표 금지, 존댓말로 친근하게.\n\n"
              + json.dumps({"선수": [facts(left), facts(right)],
                            "경기 MVP": f"{cards[0]['name']} ({cards[0]['friend']})",
                            "범인": f"{cards[1]['name']} ({cards[1]['friend']})"}, ensure_ascii=False))
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)  # 변수로 잡아둬야 호출 도중 연결이 닫히지 않는다
        best, best_over = None, None
        for _ in range(MAX_TRIES):  # 글자 수를 잘 못 지키니 길면 다시 받는다
            resp = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema={
                    "type": "object", "properties": {k: {"type": "string"} for k in keys}, "required": keys}))
            result = {k: v.strip() for k, v in json.loads(resp.text).items() if isinstance(v, str)}
            over = sum(max(len(result.get(k, "")) - MAX_LINE_CHARS, 0) for k in keys)
            if best_over is None or over < best_over:
                best, best_over = result, over
            if not over:
                break
        return {"summary": best.get("summary", ""), "lines": [best[n] for n in names if best.get(n)]}
    except Exception as exc:  # 한도 초과 등: 평가만 빼고 리포트는 보낸다
        print(f"[fc] gemini evaluation failed: {exc}")
        return {}
