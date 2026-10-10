"""FC 온라인 친구전 리포트: 축구 중계처럼 구역별 이미지 + Gemini 총평·평가.

이미지: 스코어보드 · 경기 MVP/범인 · 경기 기록 · 슈팅맵과 골 타임라인 · 라인업과 평점.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from imposter_finder.analysis.cards import (PAD, SS, WIDTH, _download, font, header, icon, png, split_bar)
from imposter_finder.games.fconline import FcMeta, FcTeamColors, player_image

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
def render_scoreboard(left: Team, right: Team, scorers: dict[str, list[str]], record: tuple, games: int) -> bytes:
    """엠블럼 · 친구 이름 · 큰 점수 · 득점자 · 맞대결."""
    lines = max(len(scorers[left.side["ouid"]]), len(scorers[right.side["ouid"]]))
    height = 236 + max(lines - 1, 0) * 24 + (52 if games else 0)
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    cx = WIDTH // 2
    for team, sign in ((left, -1), (right, 1)):
        crest_x = cx + sign * 400
        crest = icon(team.crest, 92, radius=0)
        if crest:
            img.paste(crest, (crest_x - 46, 26), crest)
        else:  # 팀컬러가 없으면 팀 색 원
            dr.ellipse((crest_x - 40, 32, crest_x + 40, 112), fill=team.color)
        name_x = cx + sign * 160
        anchor = "ra" if sign < 0 else "la"
        dr.text((name_x, 40), team.friend.name, font=font(34, 6), fill=WHITE if team.won or not (left.won or right.won)
                else LABEL, anchor=anchor)
        dr.text((name_x, 86), team.side["nickname"], font=font(16), fill=LABEL, anchor=anchor)
        if team.team_name:
            dr.text((crest_x, 124), team.team_name, font=font(14), fill=LABEL, anchor="ma")
        dr.rectangle((name_x - (0 if sign > 0 else 60), 114, name_x + (60 if sign > 0 else 0), 118), fill=team.color)
    # 점수: 이긴 쪽은 흰색, 진 쪽은 회색
    lg, rg = goals(left.side), goals(right.side)
    dr.text((cx - 22, 18), str(lg), font=font(84, 6), fill=WHITE if lg >= rg else LABEL, anchor="ra")
    dr.text((cx, 26), ":", font=font(64, 6), fill=LABEL, anchor="ma")
    dr.text((cx + 22, 18), str(rg), font=font(84, 6), fill=WHITE if rg >= lg else LABEL, anchor="la")
    dr.rounded_rectangle((cx - 44, 126, cx + 44, 152), radius=13, fill=TRACK)
    dr.text((cx, 130), "경기 종료", font=font(14, 6), fill=WHITE, anchor="ma")
    # 득점자: 각 팀 이름 아래
    for team, sign in ((left, -1), (right, 1)):
        x = cx + sign * 60
        for n, line in enumerate(scorers[team.side["ouid"]]):
            dr.text((x, 172 + n * 24), line, font=font(16), fill=WHITE, anchor="ra" if sign < 0 else "la")
    if scorers[left.side["ouid"]] or scorers[right.side["ouid"]]:
        _ball(img, cx, 182, 8)
    if games:  # 맞대결: 왼쪽 친구 기준 승·무·패 막대
        w, d, l, gf, ga = record
        y = height - 44
        dr.text((cx, y - 22), f"최근 {games}경기 맞대결", font=font(14), fill=LABEL, anchor="ma")
        dr.text((cx - 250, y - 6), f"{left.friend.name} {w}승", font=font(16, 6), fill=WHITE, anchor="ra")
        dr.text((cx + 250, y - 6), f"{l}승 {right.friend.name}", font=font(16, 6), fill=WHITE, anchor="la")
        x0, x1 = cx - 230, cx + 230
        total = max(w + d + l, 1)
        a, b = x0 + (x1 - x0) * w / total, x0 + (x1 - x0) * (w + d) / total
        _segment_bar(img, x0, x1, y, [(a, left.color), (b, TRACK), (x1, right.color)])
        if d:
            dr.text((cx, y + 10), f"무 {d}", font=font(13), fill=LABEL, anchor="ma")
    return png(img)


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


def render_potm(cards: list[dict], left: Team, right: Team) -> bytes:
    """경기 MVP·범인: 선수 사진 카드 두 장."""
    height = 250
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    half = WIDTH // 2
    for n, card in enumerate(cards):
        x0 = PAD + n * half
        box = (x0, 22, x0 + half - PAD * 1.5, height - 22)
        _panel(img, box, card["team"].color)
        photo_url = player_image(card["spid"])
        photo = None
        if photo_url:
            try:
                photo = Image.open(BytesIO(_download(photo_url))).convert("RGBA")
                photo.thumbnail((190, 190), Image.LANCZOS)
            except Exception:
                photo = None
        if photo:
            img.paste(photo, (int(box[0] + 14), int(box[3] - photo.height)), photo)
        tx = int(box[0] + 214)
        accent = (250, 190, 10) if card["badge"] == "MVP" else (255, 70, 85)
        tag = "경기 MVP" if card["badge"] == "MVP" else "범인"
        tw = dr.textlength(tag, font=font(15, 6))
        dr.rounded_rectangle((tx, 42, tx + tw + 20, 68), radius=13, fill=accent)
        dr.text((tx + 10, 46), tag, font=font(15, 6), fill=BG)
        season = icon(card["season_img"], 22, radius=0) if card.get("season_img") else None
        name_x = tx
        if season:
            img.paste(season, (tx, 84), season)
            name_x = tx + 28
        dr.text((name_x, 80), card["name"], font=font(24, 6), fill=WHITE)
        dr.text((tx, 116), f"+{card['grade']} 강화 · {card['friend']}", font=font(15), fill=LABEL)
        dr.text((tx, 146), f"{card['rating']:.1f}", font=font(44, 6), fill=accent)
        dr.text((tx + dr.textlength(f"{card['rating']:.1f}", font=font(44, 6)) + 10, 168), "평점",
                font=font(15), fill=LABEL)
        if card["marks"]:
            dr.text((tx, 206), card["marks"], font=font(15), fill=WHITE)
    return png(img)


def _panel(img: Image.Image, box: tuple, color: tuple, alpha: int = 30) -> None:
    """반투명 둥근 판 + 왼쪽 팀 색 띠."""
    x0, y0, x1, y1 = (round(v) for v in box)
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w * SS, h * SS), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=16 * SS, fill=alpha)
    img.paste(Image.new("RGB", (w, h), WHITE), (x0, y0), mask.resize((w, h), Image.LANCZOS))
    band = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(band).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=16 * SS, fill=255)
    band = band.crop((0, 0, 5 * SS, h * SS)).resize((5, h), Image.LANCZOS)
    img.paste(Image.new("RGB", (5, h), color), (x0, y0), band)


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


def _pitch(w: int, h: int) -> Image.Image:
    """위에서 본 경기장 (가로). 잔디 줄무늬 + 반투명 흰 선, 크게 그려 줄인다."""
    big = Image.new("RGB", (w * SS, h * SS), PITCH)
    d = ImageDraw.Draw(big)
    stripe = w * SS / 12
    for i in range(0, 12, 2):
        d.rectangle((i * stripe, 0, (i + 1) * stripe, h * SS), fill=PITCH_DARK)
    lines = Image.new("L", big.size, 0)
    ld = ImageDraw.Draw(lines)
    lw = 2 * SS
    W, H = w * SS - 1, h * SS - 1
    sx, sy = W / 105, H / 68  # 미터 → 픽셀
    ld.rectangle((0, 0, W, H), outline=110, width=lw)
    ld.line((W / 2, 0, W / 2, H), fill=110, width=lw)
    ld.ellipse((W / 2 - 9.15 * sx, H / 2 - 9.15 * sy, W / 2 + 9.15 * sx, H / 2 + 9.15 * sy), outline=110, width=lw)
    for x_goal, sign in ((0, 1), (W, -1)):
        for depth, half_w in ((16.5, 20.16), (5.5, 9.16)):
            xa, xb = sorted((x_goal, x_goal + sign * depth * sx))
            ld.rectangle((xa, H / 2 - half_w * sy, xb, H / 2 + half_w * sy), outline=110, width=lw)
        gx = x_goal + sign * 11 * sx
        ld.ellipse((gx - 3 * SS, H / 2 - 3 * SS, gx + 3 * SS, H / 2 + 3 * SS), fill=110)
    big.paste(Image.new("RGB", big.size, LINE), (0, 0), lines)
    return big.resize((w, h), Image.LANCZOS)


def render_shots(left: Team, right: Team, names: dict[int, str]) -> bytes:
    """슈팅맵: 왼쪽 팀은 오른쪽 골대로, 오른쪽 팀은 왼쪽 골대로. 아래에 골 타임라인."""
    pw, ph = WIDTH - PAD * 2, int((WIDTH - PAD * 2) * 68 / 105)
    top = 76
    height = top + ph + 110
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
    img.paste(_pitch(pw, ph), (PAD, top))
    layer = Image.new("RGBA", (WIDTH * SS, height * SS), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    goals_on_timeline = []
    for team, flip in ((left, False), (right, True)):
        for shot in team.side.get("shootDetail", []):
            x, y = (1 - shot["x"], 1 - shot["y"]) if flip else (shot["x"], shot["y"])
            px, py = (PAD + x * pw) * SS, (top + y * ph) * SS
            result = shot.get("result")
            if result == SHOT_GOAL:
                if shot.get("assist") and shot.get("assistX") is not None:
                    ax, ay = (1 - shot["assistX"], 1 - shot["assistY"]) if flip else (shot["assistX"], shot["assistY"])
                    _dashed(ld, ((PAD + ax * pw) * SS, (top + ay * ph) * SS), (px, py), team.color + (150,), 2 * SS)
                r = 11 * SS
                ld.ellipse((px - r, py - r, px + r, py + r), fill=team.color + (255,), outline=WHITE + (255,),
                           width=3 * SS)
                goals_on_timeline.append((minute_of(shot["goalTime"]), team, names.get(shot["spId"], "")))
            elif result == SHOT_ON:
                r = 8 * SS
                ld.ellipse((px - r, py - r, px + r, py + r), outline=team.color + (255,), width=3 * SS)
            else:
                r = 5 * SS
                ld.ellipse((px - r, py - r, px + r, py + r), fill=team.color + (110,))
    layer = layer.resize((WIDTH, height), Image.LANCZOS)
    img.paste(layer, (0, 0), layer)
    # 골 타임라인: 0' ~ 90'(연장이면 더), 왼쪽 팀 골은 위, 오른쪽 팀 골은 아래
    end = max([90] + [m for m, _, _ in goals_on_timeline])
    ty = top + ph + 52
    x0, x1 = PAD + 30, WIDTH - PAD - 30
    tx = lambda m: x0 + (x1 - x0) * min(m, end) / end
    _segment_bar(img, x0, x1, ty - 2, [(x1, TRACK)], height=4)
    for m in (0, 45, 90):
        dr.text((tx(m), ty + 34), f"{m}'", font=font(13), fill=LABEL, anchor="ma")
    for team in (left, right):
        up = team is left
        groups: list[list[tuple[int, str]]] = []
        for minute, t, name in sorted(goals_on_timeline, key=lambda g: g[0]):
            if t is not team:
                continue
            if groups and tx(minute) - tx(groups[-1][-1][0]) < 110:  # 라벨이 겹칠 만큼 가까우면 묶는다
                groups[-1].append((minute, name))
            else:
                groups.append([(minute, name)])
        for group in groups:
            for minute, _ in group:
                x = tx(minute)
                dr.line((x, ty, x, ty + (-12 if up else 12)), fill=team.color, width=2)
                _ball(img, x, ty + (-19 if up else 19), 7)
            label = " · ".join(f"{m}' {n}" for m, n in group)
            mid_x = sum(tx(m) for m, _ in group) / len(group)
            mid_x = min(max(mid_x, x0 + dr.textlength(label, font=font(13, 6)) / 2),
                        x1 - dr.textlength(label, font=font(13, 6)) / 2)
            dr.text((mid_x, ty + (-30 if up else 30)), label, font=font(13, 6), fill=WHITE, anchor="md" if up else "ma")
    return png(img)


def _dashed(draw: ImageDraw.ImageDraw, a: tuple, b: tuple, color: tuple, width: int, dash: int = 10) -> None:
    (x1, y1), (x2, y2) = a, b
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1)
    steps = int(length / (dash * SS))
    for i in range(0, steps, 2):
        t0, t1 = i / steps, min((i + 1) / steps, 1)
        draw.line((x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0, x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1),
                  fill=color, width=width)


# spposition 번호 → (자기 골대에서 깊이 0~1, 공격 방향 기준 왼쪽 0 ~ 오른쪽 1)
POSITION_XY = {0: (0.05, 0.5), 1: (0.17, 0.5), 2: (0.36, 0.92), 3: (0.25, 0.88), 4: (0.2, 0.66), 5: (0.2, 0.5),
               6: (0.2, 0.34), 7: (0.25, 0.12), 8: (0.36, 0.08), 9: (0.37, 0.64), 10: (0.37, 0.5), 11: (0.37, 0.36),
               12: (0.56, 0.9), 13: (0.52, 0.68), 14: (0.52, 0.5), 15: (0.52, 0.32), 16: (0.56, 0.1),
               17: (0.68, 0.7), 18: (0.68, 0.5), 19: (0.68, 0.3), 20: (0.8, 0.7), 21: (0.8, 0.5), 22: (0.8, 0.3),
               23: (0.82, 0.88), 24: (0.88, 0.63), 25: (0.88, 0.5), 26: (0.88, 0.37), 27: (0.82, 0.12)}


def rating_color(rating: float) -> tuple:
    """중계·축구 앱에서 흔한 평점 색: 9+ 파랑, 8+ 진초록, 7+ 초록, 6+ 주황, 그 아래 빨강."""
    if rating >= 9:
        return (30, 136, 229)
    if rating >= 8:
        return (24, 170, 90)
    if rating >= 7:
        return (110, 190, 70)
    if rating >= 6:
        return (240, 150, 40)
    return (230, 70, 60)


def render_lineup(left: Team, right: Team, names: dict[int, str], mvp: int, culprit: int) -> bytes:
    """라인업: 경기장 반씩, 포메이션 위치에 평점 원 + 이름. MVP는 금테, 범인은 빨간 테."""
    pw, ph = WIDTH - PAD * 2, int((WIDTH - PAD * 2) * 68 / 105)
    top = 76
    height = top + ph + 52
    img = _stage(height, left, right)
    dr = ImageDraw.Draw(img)
    for team, x, anchor in ((left, PAD, "la"), (right, WIDTH - PAD, "ra")):
        text = team.friend.name + (f" · {team.team_name}" if team.team_name else "")
        dr.text((x, top + ph + 14), text, font=font(16, 6), fill=WHITE, anchor=anchor)
    header(dr, "라인업 · 평점", [(rating_color(9), "9+"), (rating_color(8), "8+"), (rating_color(7), "7+"),
                               (rating_color(6), "6+"), (rating_color(5), "6 미만")])
    img.paste(_pitch(pw, ph), (PAD, top))
    layer = Image.new("RGBA", (WIDTH * SS, height * SS), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    labels = []
    for team, flip in ((left, False), (right, True)):
        for p in _starters(team.side):
            depth, across = POSITION_XY.get(p["spPosition"], (0.5, 0.5))
            # 공격 방향 기준 오른쪽(across 1)은 화면에서 아래쪽 (왼쪽 팀이 오른쪽으로 공격)
            x = depth * 0.5 * 0.94 + 0.02
            y = across
            if flip:
                x, y = 1 - x, 1 - y
            px, py = PAD + x * pw, top + y * ph
            st = p["status"]
            badge = "MVP" if p["spId"] == mvp else "범인" if p["spId"] == culprit else None
            r = 19
            if badge:
                ring = (250, 190, 10) if badge == "MVP" else WHITE
                ld.ellipse(((px - r - 4) * SS, (py - r - 4) * SS, (px + r + 4) * SS, (py + r + 4) * SS),
                           outline=ring + (255,), width=3 * SS)
            ld.ellipse(((px - r) * SS, (py - r) * SS, (px + r) * SS, (py + r) * SS),
                       fill=rating_color(st["spRating"]) + (255,))
            labels.append((px, py, f"{st['spRating']:.1f}", names.get(p["spId"], ""), st.get("goal", 0),
                           st.get("assist", 0), badge))
    layer = layer.resize((WIDTH, height), Image.LANCZOS)
    img.paste(layer, (0, 0), layer)
    for px, py, rating, name, goal, assist, badge in labels:
        dr.text((px, py), rating, font=font(15, 6), fill=WHITE, anchor="mm")
        dr.text((px, py + 25), name, font=font(13, 6), fill=WHITE, anchor="ma", stroke_width=2,
                stroke_fill=PITCH_DARK)
        for n in range(goal):  # 오른쪽 위에 공, 그 옆에 도움
            _ball(img, px + 22 + n * 13, py - 18, 6)
        for n in range(assist):
            _assist_mark(img, px + 22 + (goal + n) * 13, py - 18, 6)
        if badge:
            color = (250, 190, 10) if badge == "MVP" else (255, 70, 85)
            tw = dr.textlength(badge, font=font(11, 6))
            dr.rounded_rectangle((px - tw / 2 - 6, py - 40, px + tw / 2 + 6, py - 25), radius=7, fill=color)
            dr.text((px, py - 38), badge, font=font(11, 6), fill=BG, anchor="ma")
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
                 gemini_model: str) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    """반환: (Components V2 payload, [(파일 이름, PNG)]). friends는 ouid → Friend."""
    left, right = _teams(match, friends, colors)
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
        marks = " ".join(x for x in (f"{st['goal']}골" if st.get("goal") else "",
                                     f"{st['assist']}도움" if st.get("assist") else "",
                                     f"슈팅 {st['shoot']}" if st.get("shoot") else "") if x)
        cards.append({"badge": badge, "spid": p["spId"], "name": meta.player_name(p["spId"]),
                      "season_img": meta.season_image(p["spId"]), "grade": p["spGrade"], "rating": st["spRating"],
                      "friend": team_of[side["ouid"]].friend.name, "team": team_of[side["ouid"]], "marks": marks})

    record = _record(head_to_head, left.side["ouid"])
    files = [("score.png", render_scoreboard(left, right, scorers, record, len(head_to_head))),
             ("potm.png", render_potm(cards, left, right)),
             ("stats.png", render_stats(_stat_rows(left.side, right.side), left, right)),
             ("shots.png", render_shots(left, right, names)),
             ("lineup.png", render_lineup(left, right, names, mvp["spId"], culprit["spId"]))]

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
