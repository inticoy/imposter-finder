"""배그 친구전 리포트: 경기 분석 화면처럼 구역마다 이미지 한 장 + 총평/평가 글.

배그 느낌: 검정 반투명 판, 흰 글자, 배그 노랑, 스쿼드 번호 색(노랑·주황·파랑·초록),
게임 HUD와 같은 흰 실루엣 무기·킬피드 아이콘(공식 pubg/api-assets), 좁은 영문 숫자(Teko).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from imposter_finder.analysis.cards import FONT_DIR, GIF_MAX_BYTES, SS, WIDTH, _download, font, gif, png
from imposter_finder.analysis.pubg import analyze_pubg_match, _pubg_map_name
from imposter_finder.games.pubg_telemetry import (ASSETS, Story, build_story, map_image, weapon_icon_url,
                                                  weapon_name, zone_at)
from imposter_finder.registry import PubgPlayer

# ── 배그 색 ──────────────────────────────────────────────
BG = (14, 14, 14)
YELLOW = (242, 169, 0)  # 배그 로고 노랑 #F2A900
TEXT, MUTED, DIM = (236, 236, 236), (150, 150, 150), (96, 96, 96)
RED = (230, 62, 50)  # 사망·범인
BLUE_ZONE = (44, 92, 230)
SQUAD = [(250, 206, 62), (70, 166, 245), (104, 206, 100), (230, 62, 50)]  # 노랑·파랑·초록·빨강
ACCENT_WIN, ACCENT_LOSE = 0xF2A900, 0x5A5A5A  # 카드 왼쪽 색: 치킨이면 노랑
MAX_LINE_CHARS, MAX_TRIES = 52, 3
TEKO = FONT_DIR / "Teko-Variable.ttf"  # google/fonts ofl/teko (OFL)
KILLFEED = f"{ASSETS}/Assets/Icons/Killfeed"
MODES = {"squad": "스쿼드 TPP", "squad-fpp": "스쿼드 FPP", "duo": "듀오 TPP", "duo-fpp": "듀오 FPP",
         "solo": "솔로 TPP", "solo-fpp": "솔로 FPP", "tdm": "팀 데스매치"}


@lru_cache(maxsize=None)
def teko(size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
    """배그 화면의 좁고 굵은 영문·숫자."""
    f = ImageFont.truetype(str(TEKO), size)
    f.set_variation_by_axes([weight])
    return f


@lru_cache(maxsize=64)
def _white(url: str | None, height: int) -> Image.Image | None:
    """흰 실루엣 아이콘을 높이에 맞춰 (투명 여백 제거)."""
    if not url:
        return None
    try:
        im = Image.open(BytesIO(_download(url))).convert("RGBA")
    except Exception:
        return None
    box = im.getchannel("A").getbbox()
    im = im.crop(box) if box else im
    w = max(1, round(im.width * height / im.height))
    return im.resize((w, height), Image.LANCZOS)


def _tint(im: Image.Image, color: tuple) -> Image.Image:
    out = Image.new("RGBA", im.size, color + (255,))
    out.putalpha(im.getchannel("A"))
    return out


def _panel(img: Image.Image, box: tuple, radius: int = 14, alpha: int = 150, color: tuple = (0, 0, 0)) -> None:
    """검정 반투명 둥근 판 (배그 HUD 판). 선 없이."""
    x0, y0, x1, y1 = (round(v) for v in box)
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=alpha)
    mask = mask.resize((w, h), Image.LANCZOS)
    img.paste(Image.new("RGB", (w, h), color), (x0, y0), mask)


def _bar(img: Image.Image, box: tuple, color: tuple, alpha: int = 255) -> None:
    x0, y0, x1, y1 = box
    h = y1 - y0
    x1 = max(x1, x0 + h)
    _panel(img, (x0, y0, x1, y1), radius=max(1, round(h / 2)), alpha=alpha, color=color)


def _layer(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def _flatten(img: Image.Image, layer: Image.Image, at: tuple[int, int] = (0, 0)) -> None:
    small = layer.resize((layer.width // SS, layer.height // SS), Image.LANCZOS)
    img.paste(small, at, small)


def _mmss(sec: float) -> str:
    return f"{int(sec // 60)}분 {int(sec % 60):02d}초"


# ── 지도 자르기 ──────────────────────────────────────────
def _crop_box(story: Story, w: int, h: int, focus: list[tuple[float, float]], min_frac: float = 0.16):
    """focus 점들이 다 들어가게, 가로세로 w:h, 지도 밖으로 나가지 않게."""
    size = story.map_size
    xs, ys = [p[0] for p in focus], [p[1] for p in focus]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span_w = max((max(xs) - min(xs)) * 1.3, (max(ys) - min(ys)) * 1.42 * w / h, size * min_frac)
    span_w = min(span_w, size)
    span_h = span_w * h / w
    if span_h > size:
        span_h, span_w = size, size * w / h
    x0 = min(max(cx - span_w / 2, 0), size - span_w)
    y0 = min(max(cy - span_h / 2, 0), size - span_h)
    return x0, y0, x0 + span_w, y0 + span_h


def _map_crop(story: Story, box: tuple, w: int, h: int) -> Image.Image:
    im = map_image(story.map_name)
    k = im.width / story.map_size
    return im.crop(tuple(round(v * k) for v in box)).resize((w, h), Image.LANCZOS)


def _route_focus(story: Story) -> list[tuple[float, float]]:
    pts = []
    for m in story.members:
        land = m.landing[0] if m.landing else 0
        pts += [(x, y) for t, x, y, _ in m.path if t >= land]  # 낙하산 타는 동안은 빼고
        pts += [p[1:] for p in (m.landing, m.death) if p]  # 뛰어내린 곳은 멀어서 넣지 않는다 (점선은 화면 밖으로)
        pts += [k["xy"] for k in m.kills + m.knocks]
    return pts or [(story.map_size / 2, story.map_size / 2)]


# ── 1. 결과 ──────────────────────────────────────────────
def render_result(story: Story | None, place: int, teams: int, map_ko: str, mode: str, minutes: float,
                  names: list[tuple[str, tuple]], totals: list[tuple[str, str]]) -> bytes:
    W, H = WIDTH, 300
    if story:
        focus = [m.death[1:] for m in story.members if m.death] or _route_focus(story)
        bg = _map_crop(story, _crop_box(story, W, H, focus, 0.3), W, H).filter(ImageFilter.GaussianBlur(2))
        img = Image.blend(bg, Image.new("RGB", (W, H), BG), 0.6)
    else:
        img = Image.new("RGB", (W, H), BG)
    # 왼쪽 글자 쪽을 더 어둡게
    ramp = Image.linear_gradient("L").rotate(90).resize((W, H))
    img = Image.composite(img, Image.new("RGB", (W, H), BG), ramp.point(lambda v: 110 + v * 145 // 255))
    d = ImageDraw.Draw(img)
    x = 44
    d.text((x, 40), f"{map_ko} · {mode} · {minutes:.0f}분", font=font(18, 4), fill=MUTED, anchor="lm")
    if place == 1:
        d.text((x - 2, 124), "WINNER WINNER", font=teko(70, 600), fill=YELLOW, anchor="ls")
        d.text((x - 2, 192), "CHICKEN DINNER!", font=teko(70, 600), fill=YELLOW, anchor="ls")
    else:
        d.text((x - 6, 190), f"#{place}", font=teko(170, 600), fill=TEXT, anchor="ls")
        rank_w = d.textlength(f"#{place}", font=teko(170, 600))
        d.text((x + rank_w + 4, 190), f"/{teams}", font=teko(64, 500), fill=MUTED, anchor="ls")
    # 스쿼드 이름 (번호 색)
    cx = x
    for name, color in names:
        _panel(img, (cx, 228, cx + 6, 250), radius=3, alpha=255, color=color)
        d.text((cx + 14, 239), name, font=font(19, 6), fill=TEXT, anchor="lm")
        cx += 14 + d.textlength(name, font=font(19, 6)) + 26
    # 오른쪽 팀 합계
    col_w = 120
    rx = W - 44 - col_w * len(totals)
    _panel(img, (rx - 10, 68, W - 34, 180), radius=16, alpha=150)
    for i, (label, value) in enumerate(totals):
        mx = rx + col_w * i + col_w / 2
        d.text((mx, 139), value, font=teko(58, 600), fill=TEXT, anchor="ms")
        d.text((mx, 160), label, font=font(15, 4), fill=MUTED, anchor="mm")
    return png(img)


def _tdm_stage(height: int, weapon: str | None = None) -> Image.Image:
    """PUBG HUD colors over a quiet tactical texture; weapon art is an actual PUBG asset."""
    img = Image.new("RGB", (WIDTH, height), BG)
    glow = Image.new("RGBA", img.size)
    g = ImageDraw.Draw(glow)
    g.ellipse((-230, -height, 560, height * 2), fill=YELLOW + (36,))
    g.ellipse((590, -height, 1340, height * 2), fill=SQUAD[1] + (25,))
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(95))).convert("RGB")
    d = ImageDraw.Draw(img)
    for x in range(-height, WIDTH + height, 66):
        d.line((x, 0, x + height, height), fill=(34, 35, 35), width=1)
    for y in range(28, height, 42):
        d.line((0, y, WIDTH, y), fill=(29, 30, 30), width=1)
    for x in range(25, WIDTH, 190):
        d.rectangle((x, 15, x + 3, 21), fill=(75, 69, 48))
    if weapon:
        icon = _white(weapon_icon_url(weapon), round(height * 0.60))
        if icon:
            alpha = icon.getchannel("A").point(lambda value: round(value * 0.10))
            img.paste(icon.convert("RGB"), (WIDTH - icon.width - 10, round(height * 0.20)), alpha)
    return img


def render_tdm_result(map_name: str, minutes: float, teams: list[dict],
                      rounds: list[dict], weapon: str | None) -> bytes:
    """Match winner and supported round score; total kills belong in the detail card."""
    W, H = WIDTH, 300
    img = _tdm_stage(H, weapon)
    d = ImageDraw.Draw(img)
    winner = next((team for team in teams if team.get("rank") == 1), None)
    loser = next((team for team in teams if team is not winner), None)
    d.text((44, 40), f"{map_name} · 팀 데스매치 · {minutes:.0f}분", font=font(18, 4), fill=MUTED, anchor="lm")
    if winner:
        d.text((42, 128), "MATCH WINNER", font=teko(68, 600), fill=YELLOW, anchor="ls")
        d.text((44, 184), winner["label"], font=teko(65, 600), fill=TEXT, anchor="ls")
    else:
        d.text((44, 154), "TEAM DEATHMATCH", font=teko(62, 600), fill=TEXT, anchor="ls")
    if winner and loser:
        _panel(img, (635, 68, W - 34, 181), radius=16, alpha=165)
        if rounds:
            win_rounds = sum(r["winner"] == winner["id"] for r in rounds)
            loss_rounds = len(rounds) - win_rounds
            d.text((801, 135), f"{win_rounds} : {loss_rounds}", font=teko(76, 600), fill=TEXT, anchor="ms")
            d.text((801, 160), "라운드 승리", font=font(15, 4), fill=MUTED, anchor="mm")
        else:
            d.text((801, 137), "WIN", font=teko(73, 600), fill=YELLOW, anchor="ms")
            d.text((801, 160), "라운드 기록 확인 불가", font=font(14, 4), fill=MUTED, anchor="mm")
    if rounds:
        labels = {team["id"]: team["label"] for team in teams}
        for i, item in enumerate(rounds):
            x = 44 + i * 187
            color = YELLOW if winner and item["winner"] == winner["id"] else SQUAD[1]
            _panel(img, (x, 220, x + 169, 261), radius=9, alpha=110)
            _panel(img, (x + 10, 230, x + 15, 251), radius=2, alpha=255, color=color)
            text = f"R{i + 1}  {labels[item['winner']]}"
            size = next((n for n in (15, 14, 13, 12) if d.textlength(text, font=font(n, 6)) <= 138), 11)
            d.text((x + 24, 241), text, font=font(size, 6), fill=TEXT, anchor="lm")
    elif winner and loser:
        for i, team in enumerate((winner, loser)):
            x = 44 + i * 187
            color = YELLOW if i == 0 else SQUAD[1]
            _panel(img, (x, 228, x + 5, 251), radius=2, alpha=255, color=color)
            d.text((x + 16, 239), f"{team['label']} · {'승리' if i == 0 else '패배'}",
                   font=font(16, 6), fill=TEXT, anchor="lm")
    return png(img)


def render_tdm_comparison(teams: list[dict], mvp: str | None, ace: str | None,
                          culprit: str | None, weapon: str | None) -> bytes:
    """Team comparison with the same HUD panels and accent palette as squad reports."""
    W = WIDTH
    H = max(470, 280 + 56 * max((len(team["players"]) for team in teams), default=0))
    img = _tdm_stage(H, weapon)
    d = ImageDraw.Draw(img)
    d.text((44, 38), "팀별 기록", font=font(22, 6), fill=TEXT, anchor="lm")
    d.text((W - 40, 38), "승리 팀 / 패배 팀", font=font(15, 4), fill=MUTED, anchor="rm")
    margin, gap = 44, 20
    card_w = (W - margin * 2 - gap) / 2
    max_damage = max((team["damage"] for team in teams), default=1) or 1
    for idx, team in enumerate(teams[:2]):
        x = margin + idx * (card_w + gap)
        accent = YELLOW if team.get("rank") == 1 else SQUAD[1]
        _panel(img, (x, 64, x + card_w, H - 20), radius=15, alpha=174)
        _panel(img, (x + 18, 79, x + 24, 104), radius=3, alpha=255, color=accent)
        title = team["label"]
        d.text((x + 38, 91), title, font=teko(33, 600), fill=TEXT, anchor="lm")
        if team.get("rank") == 1:
            d.text((x + card_w - 18, 91), "WIN", font=font(14, 6), fill=YELLOW, anchor="rm")
        elif team.get("rank") == 2:
            d.text((x + card_w - 18, 91), "LOSS", font=font(14, 5), fill=MUTED, anchor="rm")
        d.text((x + 18, 119), f"킬 {team['kills']}   ·   피해량 {team['damage']:.0f}",
               font=font(15, 4), fill=MUTED, anchor="lm")
        _panel(img, (x + 14, 140, x + card_w - 14, 141), radius=1, alpha=70)
        name_x = x + 18
        metric_left = x + 201
        metric_w = (card_w - (metric_left - x) - 14) / 4
        for j, label in enumerate(("킬", "딜", "HS", "사망")):
            d.text((metric_left + metric_w * (j + 0.5), 161), label, font=font(13, 4), fill=DIM, anchor="mm")
        for row, player in enumerate(team["players"]):
            cy = 204 + row * 56
            if row:
                _panel(img, (x + 16, cy - 21, x + card_w - 16, cy - 20), radius=1, alpha=28)
            name = player["name"]
            name_font = font(16 if d.textlength(name, font=font(16, 6)) <= 118 else 13, 6)
            d.text((name_x, cy - 6), name, font=name_font,
                   fill=TEXT if player["friend"] else MUTED, anchor="lm")
            badge = ("MVP" if player["friend"] and player["friend_name"] == mvp else
                     "ACE" if player["friend"] and player["friend_name"] == ace else
                     "범인" if player["friend"] and player["friend_name"] == culprit else None)
            if badge:
                color = YELLOW if badge == "MVP" else SQUAD[1] if badge == "ACE" else RED
                _panel(img, (name_x, cy + 8, name_x + (40 if badge != "범인" else 38), cy + 25),
                       radius=8, alpha=255, color=color)
                d.text((name_x + 19, cy + 16), badge, font=font(11, 6), fill=BG, anchor="mm")
            values = (player["kills"], round(player["damage"]), player["headshots"], player["deaths"])
            for j, value in enumerate(values):
                d.text((metric_left + metric_w * (j + 0.5), cy), str(value), font=teko(27, 500),
                       fill=TEXT if player["friend"] else MUTED, anchor="mm")
        d.text((x + 18, H - 85), "팀 피해량", font=font(13, 4), fill=MUTED, anchor="lm")
        d.text((x + card_w - 18, H - 85), f"{team['damage']:.0f}", font=teko(25, 600), fill=accent, anchor="rm")
        _bar(img, (x + 18, H - 62, x + card_w - 18, H - 52), (57, 57, 57))
        _bar(img, (x + 18, H - 62, x + 18 + (card_w - 36) * team["damage"] / max_damage, H - 52), accent)
    return png(img)


def render_tdm_weapons(teams: list[dict], weapon: str | None) -> bytes:
    """Every participant's most used weapon, from LogMatchEnd weapon statistics."""
    W = WIDTH
    H = max(520, 128 + 128 * max((len(team["players"]) for team in teams), default=0))
    img = _tdm_stage(H, weapon)
    d = ImageDraw.Draw(img)
    d.text((44, 38), "쓴 무기", font=font(22, 6), fill=TEXT, anchor="lm")
    d.text((W - 40, 38), "적에게 준 피해 순", font=font(14, 4), fill=MUTED, anchor="rm")
    margin, gap = 44, 20
    card_w = (W - margin * 2 - gap) / 2
    for team_index, team in enumerate(teams[:2]):
        x = margin + team_index * (card_w + gap)
        accent = YELLOW if team.get("rank") == 1 else SQUAD[1]
        _panel(img, (x + 18, 58, x + 24, 81), radius=3, alpha=255, color=accent)
        d.text((x + 38, 70), team["label"], font=teko(27, 600), fill=TEXT, anchor="lm")
        for row, player in enumerate(team["players"]):
            y = 96 + row * 128
            _panel(img, (x, y, x + card_w, y + 116), radius=12, alpha=170)
            d.text((x + 16, y + 19), player["name"], font=font(16, 6), fill=TEXT, anchor="lm")
            weapons = player.get("weapons") or []
            if not weapons:
                d.text((x + 16, y + 66), "무기 기록 없음", font=font(15, 4), fill=MUTED, anchor="lm")
                continue
            main = weapons[0]
            icon = _white(weapon_icon_url(main["weapon"]), 35)
            if icon:
                if icon.width > 198:
                    icon = icon.resize((198, round(icon.height * 198 / icon.width)), Image.LANCZOS)
                img.paste(icon, (round(x + 16), round(y + 49 - icon.height / 2)), icon)
            label = weapon_name(main["weapon"])
            name_font = next((teko(size, 500) for size in (25, 22, 19, 16)
                              if d.textlength(label, font=teko(size, 500)) <= 198), teko(16, 500))
            while d.textlength(label, font=name_font) > 198 and len(label) > 3:
                label = label[:-2].rstrip() + "…"
            d.text((x + 16, y + 84), label, font=name_font, fill=TEXT, anchor="ls")
            d.text((x + card_w - 16, y + 62), f"{main['damage']:.0f}", font=teko(44, 600),
                   fill=accent, anchor="rs")
            d.text((x + card_w - 16, y + 83), f"딜  ·  {main['kills']}처치", font=font(13, 4),
                   fill=MUTED, anchor="rm")
            if len(weapons) > 1:
                second = weapons[1]
                second_label = weapon_name(second["weapon"])
                d.text((x + 16, y + 104), f"#2  {second_label[:19]}  ·  {second['damage']:.0f}딜",
                       font=font(12, 4), fill=DIM, anchor="lm")
    return png(img)


# ── 2. 스쿼드 기록 ───────────────────────────────────────
def render_squad(cols: list[dict], rows: list[tuple]) -> bytes:
    """cols: {name, color, badge}. rows: (항목, [값], 표시 함수, 클수록 좋은가)."""
    label_w, top = 150, 116
    row_h = 44
    H = top + row_h * len(rows) + 22
    img = Image.new("RGB", (WIDTH, H), BG)
    d = ImageDraw.Draw(img)
    d.text((44, 38), "스쿼드 기록", font=font(22, 6), fill=TEXT, anchor="lm")
    col_w = (WIDTH - 44 - label_w - 24) / len(cols)
    x0 = 44 + label_w
    for i, c in enumerate(cols):
        cx = x0 + col_w * i + col_w / 2
        _panel(img, (x0 + col_w * i + 6, 60, x0 + col_w * (i + 1) - 6, H - 14), radius=14,
               alpha=34 if c["friend"] else 18, color=(255, 255, 255))
        _bar(img, (cx - 22, 66, cx + 22, 71), c["color"])
        name_f = font(19 if d.textlength(c["name"], font=font(19, 6)) < col_w - 24 else 15, 6)
        d.text((cx, 88), c["name"], font=name_f, fill=TEXT if c["friend"] else MUTED, anchor="mm")
        if c["badge"]:
            accent = YELLOW if c["badge"] == "MVP" else RED
            text = c["badge"]
            tw = d.textlength(text, font=font(13, 6))
            _panel(img, (cx - tw / 2 - 9, 104, cx + tw / 2 + 9, 124), radius=10, alpha=255, color=accent)
            d.text((cx, 114), text, font=font(13, 6), fill=BG, anchor="mm")
    for r, (label, values, fmt, higher) in enumerate(rows):
        y = top + 26 + row_h * r
        d.text((44, y), label, font=font(17, 4), fill=MUTED, anchor="lm")
        friends = [v for v, c in zip(values, cols) if c["friend"]]
        best = (max(friends) if higher else min(friends)) if friends else None
        for i, v in enumerate(values):
            cx = x0 + col_w * i + col_w / 2
            is_best = cols[i]["friend"] and v == best and len(friends) > 1 and v
            d.text((cx, y + 12), fmt(v), font=teko(34, 600 if is_best else 500),
                   fill=YELLOW if is_best else (TEXT if cols[i]["friend"] else MUTED), anchor="ms")
    return png(img)


# ── 3. 이동 경로 지도 ────────────────────────────────────
def _dashed(d: ImageDraw.ImageDraw, a: tuple, b: tuple, fill: tuple, width: int, dash: float, gap: float) -> None:
    (x0, y0), (x1, y1) = a, b
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if not length:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        d.line((x0 + ux * pos, y0 + uy * pos, x0 + ux * end, y0 + uy * end), fill=fill, width=width)
        pos = end + gap


def render_map(story: Story, colors: dict[str, tuple], map_ko: str) -> bytes:
    return png(_route_image(story, colors, map_ko))


def _route_image(story: Story, colors: dict[str, tuple], map_ko: str, elapsed: float | None = None) -> Image.Image:
    W, H = WIDTH, 560
    until = story.team_end if elapsed is None else max(0, min(elapsed, story.team_end))
    box = _crop_box(story, W, H, _route_focus(story))
    base = _map_crop(story, box, W, H)
    base = Image.blend(base, Image.new("RGB", (W, H), BG), 0.28)
    sx, sy = W * SS / (box[2] - box[0]), H * SS / (box[3] - box[1])
    P = lambda x, y: ((x - box[0]) * sx, (y - box[1]) * sy)

    # 우리 팀이 끝났을 때의 자기장: 파란 원 밖은 푸르게, 흰 원은 선으로
    zone = zone_at(story, until)
    if zone:
        (bx, by), br, (wx, wy), wr = zone
        tint = Image.new("L", (W * SS, H * SS), 46)
        if br > 0:
            cx, cy = P(bx, by)
            ImageDraw.Draw(tint).ellipse((cx - br * sx, cy - br * sy, cx + br * sx, cy + br * sy), fill=0)
        tint = tint.resize((W, H), Image.LANCZOS)
        base.paste(Image.new("RGB", (W, H), BLUE_ZONE), (0, 0), tint)
    layer, d = _layer((W, H))
    if zone and wr > 0:
        cx, cy = P(wx, wy)
        d.ellipse((cx - wr * sx, cy - wr * sy, cx + wr * sx, cy + wr * sy), outline=(255, 255, 255, 230), width=3 * SS)
    if story.plane:  # 비행기 길: 두 점을 지나는 직선을 화면 끝까지
        (ax, ay), (bx2, by2) = (P(*story.plane[0]), P(*story.plane[1]))
        dx, dy = bx2 - ax, by2 - ay
        n = (dx * dx + dy * dy) ** 0.5 or 1
        far = (W + H) * SS * 2
        _dashed(d, (ax - dx / n * far, ay - dy / n * far), (ax + dx / n * far, ay + dy / n * far),
                (255, 255, 255, 120), 2 * SS, 10 * SS, 8 * SS)
    for m in story.members:
        color = colors[m.name]
        end_t = m.death[0] if m.death else story.duration
        cutoff = min(until, end_t) if elapsed is not None else end_t
        path = [(t, x, y, car) for t, x, y, car in m.path if (not m.landing or t >= m.landing[0]) and t <= end_t]
        pts = [(t, *P(x, y), car) for t, x, y, car in path if t <= cutoff]
        if elapsed is not None:
            before = next((p for p in reversed(path) if p[0] <= cutoff), None)
            after = next((p for p in path if p[0] > cutoff), None)
            if before and after and before[0] < cutoff:
                fraction = (cutoff - before[0]) / (after[0] - before[0])
                x = before[1] + (after[1] - before[1]) * fraction
                y = before[2] + (after[2] - before[2]) * fraction
                pts.append((cutoff, *P(x, y), before[3]))
        if m.landing and m.landing[0] <= cutoff:
            pts.insert(0, (m.landing[0], *P(*m.landing[1:]), False))
        if m.death and m.death[0] <= cutoff:
            pts.append((m.death[0], *P(*m.death[1:]), False))
        if m.jump and m.landing and m.jump[0] <= cutoff:  # 낙하산: 점선
            fraction = min(1, (cutoff - m.jump[0]) / max(1, m.landing[0] - m.jump[0]))
            target = tuple(a + (b - a) * fraction for a, b in zip(m.jump[1:], m.landing[1:]))
            _dashed(d, P(*m.jump[1:]), P(*target), color + (200,), 2 * SS, 3 * SS, 5 * SS)
        for (_, x0, y0, car0), (_, x1, y1, car1) in zip(pts, pts[1:]):
            d.line((x0, y0, x1, y1), fill=(0, 0, 0, 120), width=7 * SS)
        for (_, x0, y0, car0), (_, x1, y1, car1) in zip(pts, pts[1:]):
            d.line((x0, y0, x1, y1), fill=color + ((150,) if car1 else (255,)), width=(3 if car1 else 4) * SS)
            d.ellipse((x1 - 2 * SS, y1 - 2 * SS, x1 + 2 * SS, y1 + 2 * SS), fill=color + (255,))
        if m.landing and m.landing[0] <= cutoff:
            lx, ly = P(*m.landing[1:])
            r = 8 * SS
            d.ellipse((lx - r, ly - r, lx + r, ly + r), fill=(0, 0, 0, 200), outline=color + (255,), width=3 * SS)
        if elapsed is not None and pts and (not m.death or cutoff < m.death[0]):
            _, x, y, _ = pts[-1]
            r = 8 * SS
            d.ellipse((x - r, y - r, x + r, y + r), fill=color + (255,), outline=(255, 255, 255, 255), width=2 * SS)
    _flatten(base, layer)

    # 처치·사망 표시는 킬피드 아이콘으로
    skull = _white(f"{KILLFEED}/Death.png", 18)
    for m in story.members:
        color = colors[m.name]
        for k in (k for k in m.kills if elapsed is None or k["t"] <= until):
            x, y = (v / SS for v in P(*k["xy"]))
            _icon_badge(base, skull, x, y, color)
        if m.death and m.death[0] <= until:
            x, y = (v / SS for v in P(*m.death[1:]))
            _cross(base, x, y, color)
    # 제목·범례는 경로가 적은 모서리에
    marks = [(x / SS, y / SS) for m in story.members
             for _, x, y, _ in [(0, *P(px, py), 0) for _, px, py, _ in m.path] + [(0, *P(*k["xy"]), 0) for k in m.kills]]
    marks += [(x / SS, y / SS) for m in story.members if m.death for x, y in [P(*m.death[1:])]]
    layout_zone = zone_at(story, story.team_end)
    if layout_zone and layout_zone[3] > 0:  # 최종 자기장으로 제목·범례 위치를 고정
        (wx, wy), wr = layout_zone[2], layout_zone[3]
        cx, cy = P(wx, wy)
        marks += [((cx + wr * sx * math.cos(a / 12 * math.pi)) / SS, (cy + wr * sy * math.sin(a / 12 * math.pi)) / SS)
                  for a in range(24)]
    busy = lambda box: sum(box[0] - 40 <= x <= box[2] + 40 and box[1] - 40 <= y <= box[3] + 40 for x, y in marks)
    title_w, legend_w = 210, _legend_width(base)
    title = min([(20, 20, 20 + title_w, 64), (W - 20 - title_w, 20, W - 20, 64)], key=busy)
    _panel(base, title, radius=12, alpha=170)
    d2 = ImageDraw.Draw(base)
    d2.text((title[0] + 18, 42), "이동 경로", font=font(20, 6), fill=TEXT, anchor="lm")
    d2.text((title[0] + 120, 42), map_ko, font=font(16, 4), fill=MUTED, anchor="lm")
    legend_x = min([20, W - 20 - legend_w], key=lambda x: busy((x, H - 46, x + legend_w, H - 16)))
    _legend(base, skull, legend_x)
    if elapsed is not None:  # GIF 장면: 제목 반대쪽 모서리에 경기 시각
        clock_x = W - 150 if title[0] == 20 else 20
        _clock(base, clock_x, until)
    return base


def _icon_badge(img: Image.Image, icon: Image.Image | None, x: float, y: float, ring: tuple) -> None:
    layer, d = _layer((40, 40))
    c, r = 20 * SS, 13 * SS
    d.ellipse((c - r, c - r, c + r, c + r), fill=(0, 0, 0, 220), outline=ring + (255,), width=3 * SS)
    _flatten(img, layer, (round(x) - 20, round(y) - 20))
    if icon:
        im = icon.resize((round(icon.width * 14 / icon.height), 14), Image.LANCZOS)
        img.paste(im, (round(x - im.width / 2), round(y - 7)), im)


def _cross(img: Image.Image, x: float, y: float, color: tuple) -> None:
    layer, d = _layer((40, 40))
    c, r = 20 * SS, 9 * SS
    for w, fill in ((9, (0, 0, 0, 220)), (5, color + (255,))):
        d.line((c - r, c - r, c + r, c + r), fill=fill, width=w * SS)
        d.line((c - r, c + r, c + r, c - r), fill=fill, width=w * SS)
    _flatten(img, layer, (round(x) - 20, round(y) - 20))


LEGEND = ["경로", "낙하", "착지", "처치", "사망", "마지막 자기장"]


def _legend_width(img: Image.Image) -> float:
    d = ImageDraw.Draw(img)
    return sum(30 + d.textlength(t, font=font(14, 4)) + 18 for t in LEGEND) + 12


def _legend(img: Image.Image, skull: Image.Image | None, x: float) -> None:
    W, H = img.size
    items = LEGEND
    f = font(14, 4)
    d = ImageDraw.Draw(img)
    widths = [30 + d.textlength(t, font=f) + 18 for t in items]
    y = H - 46
    _panel(img, (x, y, x + sum(widths) + 12, y + 30), radius=10, alpha=170)
    x += 14
    cy = y + 15
    for text, w in zip(items, widths):
        layer, ld = _layer((26, 26))
        c = 13 * SS
        if text == "경로":
            ld.line((2 * SS, c, 24 * SS, c), fill=TEXT + (255,), width=4 * SS)
        elif text == "낙하":
            _dashed(ld, (2 * SS, c), (24 * SS, c), TEXT + (255,), 2 * SS, 3 * SS, 4 * SS)
        elif text == "착지":
            ld.ellipse((c - 7 * SS, c - 7 * SS, c + 7 * SS, c + 7 * SS), fill=(0, 0, 0, 255), outline=TEXT + (255,), width=3 * SS)
        elif text == "처치":
            ld.ellipse((c - 11 * SS, c - 11 * SS, c + 11 * SS, c + 11 * SS), fill=(0, 0, 0, 255), outline=TEXT + (255,), width=2 * SS)
        elif text == "사망":
            for wd, fill in ((7, (0, 0, 0, 255)), (4, TEXT + (255,))):
                ld.line((c - 7 * SS, c - 7 * SS, c + 7 * SS, c + 7 * SS), fill=fill, width=wd * SS)
                ld.line((c - 7 * SS, c + 7 * SS, c + 7 * SS, c - 7 * SS), fill=fill, width=wd * SS)
        else:
            ld.ellipse((c - 10 * SS, c - 10 * SS, c + 10 * SS, c + 10 * SS), outline=(255, 255, 255, 255), width=2 * SS)
        _flatten(img, layer, (round(x), round(cy - 13)))
        if text == "처치" and skull:
            im = skull.resize((round(skull.width * 11 / skull.height), 11), Image.LANCZOS)
            img.paste(im, (round(x + 13 - im.width / 2), round(cy - 5.5)), im)
        d.text((x + 30, cy), text, font=f, fill=TEXT, anchor="lm")
        x += w


def _clock(img: Image.Image, x: float, t: float) -> None:
    _panel(img, (x, 13, x + 130, 61), radius=12, alpha=255)
    ImageDraw.Draw(img).text((x + 65, 38), f"{int(t // 60):02d}:{int(t % 60):02d}", font=font(25, 6), fill=YELLOW, anchor="mm")


# ── 4. 생존 타임라인 ─────────────────────────────────────
def render_timeline(story: Story, colors: dict[str, tuple]) -> bytes:
    return png(_timeline_image(story, colors))


def _timeline_image(story: Story, colors: dict[str, tuple], elapsed: float | None = None) -> Image.Image:
    left, right, top, row_h = 210, 40, 104, 70
    H = top + row_h * len(story.members) + 50
    W = WIDTH
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((44, 38), "교전 흐름", font=font(22, 6), fill=TEXT, anchor="lm")
    end = min(story.duration, story.team_end + 40)
    until = end if elapsed is None else max(0, min(elapsed, end))
    X = lambda t: left + (W - left - right) * min(max(t, 0), end) / end
    y_top, y_bot = top - 14, top + row_h * len(story.members) - 6
    # 자기장이 줄어드는 구간은 푸르게
    for n, (a, b) in enumerate(story.phases, 1):
        if a >= until:
            break
        phase_end = min(b, until)
        _panel(img, (X(a), y_top, X(phase_end), y_bot), radius=6, alpha=16, color=(255, 255, 255))
        if X(phase_end) - X(a) > 26:
            d.text(((X(a) + X(phase_end)) / 2, y_top - 12), f"자기장 {n}", font=font(12, 4), fill=MUTED, anchor="mm")
    # 범례
    legend = [("기절시킴", "Groggy.png"), ("처치", "Death.png")]
    lx = W - right
    for label, file in reversed(legend):
        ic = _white(f"{KILLFEED}/{file}", 16)
        lx -= d.textlength(label, font=font(14, 4))
        d.text((lx, 38), label, font=font(14, 4), fill=MUTED, anchor="lm")
        if ic:
            lx -= ic.width + 6
            img.paste(ic, (round(lx), 30), ic)
        lx -= 22
    groggy = _white(f"{KILLFEED}/Groggy.png", 12)
    skull = _white(f"{KILLFEED}/Death.png", 16)
    for i, m in enumerate(story.members):
        color = colors[m.name]
        cy = top + row_h * i + row_h / 2 - 6
        _panel(img, (44, cy - 11, 50, cy + 11), radius=3, alpha=255, color=color)
        size = 17 if d.textlength(m.name, font=font(17, 6)) < left - 80 else 14
        d.text((60, cy), m.name, font=font(size, 6), fill=TEXT if m.friend else MUTED, anchor="lm")
        land = m.landing[0] if m.landing else 0
        gone = m.death[0] if m.death else story.duration
        if until > 0:
            _bar(img, (X(0), cy - 2, X(min(land, until)), cy + 2), DIM)  # 비행기·낙하산
        if until > land:
            _bar(img, (X(land), cy - 5, X(min(gone, until)), cy + 5), color)
        # 기절해 있던 동안은 빨갛게
        for t_knock, *_ in m.knocked:  # 살려준 기록이 빠질 때가 있어 기절 시간은 최대 60초로 본다
            if t_knock > until:
                continue
            back = min([t for t in m.revived if t > t_knock] + [gone, t_knock + 60])
            _bar(img, (X(t_knock), cy - 5, X(min(back, until)), cy + 5), RED)
        for events, ic, dy in (([k["t"] for k in m.kills], skull, -24), ([k["t"] for k in m.knocks], groggy, 18)):
            last_x = -99
            for t in sorted(events):
                if not ic or t > until:
                    continue
                x = max(X(t), last_x + ic.width + 2)
                img.paste(ic, (round(x - ic.width / 2), round(cy + dy - ic.height / 2)), ic)
                last_x = x
        if m.death and m.death[0] <= until:
            _cross(img, X(gone), cy, color)
    # 분 눈금
    step = 60 if end < 8 * 60 else 120 if end < 15 * 60 else 300
    for t in range(0, int(end) + 1, step):
        d.text((X(t), H - 26), f"{t // 60}분", font=font(13, 4), fill=DIM, anchor="mm")
    if elapsed is not None:  # GIF 장면: 재생 위치 막대와 경기 시각
        x = X(until)
        d.line((x, y_top - 6, x, y_bot + 6), fill=(0, 0, 0), width=5)
        d.line((x, y_top - 6, x, y_bot + 6), fill=YELLOW, width=2)
        _clock(img, 166, until)
    return img


# ── 경로·교전 흐름 GIF ───────────────────────────────────
GIF_STEPS, GIF_EVENT_GAP = 32, 6  # 고르게 나눈 장면 수, 이보다 가까운 교전 장면은 합친다 (초)


def _frame_times(story: Story) -> list[float]:
    """GIF 장면 시각: 고르게 나눈 시각 + 착지·기절·처치·사망 순간 (짧은 교전도 장면에 잡히게)."""
    end = story.team_end
    times = [end * i / (GIF_STEPS - 1) for i in range(GIF_STEPS)]
    events = []
    for m in story.members:
        events += [m.landing[0]] if m.landing else []
        events += [k["t"] for k in m.kills + m.knocks] + [t for t, *_ in m.knocked]
        events += [m.death[0]] if m.death else []
    for t in sorted(t for t in events if 0 < t < end):
        if min(abs(t - x) for x in times) >= GIF_EVENT_GAP:
            times.append(t)
    return sorted(times)


def render_route_gifs(story: Story, colors: dict[str, tuple], map_ko: str) -> tuple[bytes, bytes]:
    """이동 경로·교전 흐름 GIF. 두 GIF는 같은 장면 시각·같은 재생 시간표."""
    times = _frame_times(story)
    route = gif([_route_image(story, colors, map_ko, t) for t in times])
    timeline = gif([_timeline_image(story, colors, t) for t in times])
    return route, timeline


# ── 5. 무기 ──────────────────────────────────────────────
def render_weapons(story: Story, colors: dict[str, tuple]) -> bytes:
    members = [m for m in story.members if m.friend]
    row_h, top = 100, 76
    H = top + row_h * len(members) + 16
    img = Image.new("RGB", (WIDTH, H), BG)
    d = ImageDraw.Draw(img)
    d.text((44, 38), "쓴 무기", font=font(22, 6), fill=TEXT, anchor="lm")
    d.text((WIDTH - 40, 38), "적에게 준 피해 순", font=font(14, 4), fill=MUTED, anchor="rm")
    card_x0, gap = 230, 12
    card_w = (WIDTH - 40 - card_x0 - gap * 2) / 3
    for i, m in enumerate(members):
        y0 = top + row_h * i
        cy = y0 + row_h / 2 - 4
        _panel(img, (44, cy - 12, 50, cy + 12), radius=3, alpha=255, color=colors[m.name])
        size = 18 if d.textlength(m.name, font=font(18, 6)) < 160 else 14
        d.text((60, cy), m.name, font=font(size, 6), fill=TEXT, anchor="lm")
        weapons = sorted(((w, v) for w, v in m.weapon_damage.items() if v >= 1), key=lambda x: -x[1])[:3]
        if not weapons:
            d.text((card_x0 + 4, cy), "적에게 피해를 주지 못했어요", font=font(16, 4), fill=DIM, anchor="lm")
            continue
        for j, (causer, dmg) in enumerate(weapons):
            x0 = card_x0 + (card_w + gap) * j
            _panel(img, (x0, y0 + 6, x0 + card_w, y0 + row_h - 8), radius=12, alpha=26, color=(255, 255, 255))
            ic = _white(weapon_icon_url(causer), 28)
            if ic and ic.width > card_w - 120:
                ic = ic.resize((round(card_w - 120), round(28 * (card_w - 120) / ic.width)), Image.LANCZOS)
            if ic:
                img.paste(ic, (round(x0 + 16), round(y0 + 34 - ic.height / 2)), ic)
            name = weapon_name(causer)
            name_width = max(48, card_w - 132)
            name_font = next((teko(size, 500) for size in (26, 23, 20, 17)
                              if d.textlength(name, font=teko(size, 500)) <= name_width), teko(17, 500))
            while d.textlength(name, font=name_font) > name_width and len(name) > 3:
                name = name[:-2].rstrip() + "…"
            d.text((x0 + 16, y0 + 80), name, font=name_font, fill=TEXT, anchor="ls")
            d.text((x0 + card_w - 16, y0 + 52), f"{dmg:.0f}", font=teko(44, 600), fill=YELLOW, anchor="rs")
            tags = []
            if m.weapon_knocks.get(causer):
                tags.append(f"기절 {m.weapon_knocks[causer]}")
            if m.weapon_kills.get(causer):
                tags.append(f"처치 {m.weapon_kills[causer]}")
            d.text((x0 + card_w - 16, y0 + 74), " · ".join(tags) or "딜", font=font(13, 4), fill=MUTED, anchor="rm")
    return png(img)


# ── 리포트 ───────────────────────────────────────────────
def build_report(platform: str, match: dict[str, Any], telemetry: list[dict[str, Any]], registered: list[PubgPlayer],
                 gemini_api_key: str | None, gemini_model: str) -> tuple[dict, list[tuple]]:
    attrs = match["data"]["attributes"]
    if str(attrs.get("gameMode", "")).casefold() == "tdm":
        return _build_tdm_report(platform, match, telemetry, registered, gemini_api_key, gemini_model)

    analysis = analyze_pubg_match(platform, match, telemetry, registered)
    scores = analysis["scores"]  # 친구 이름 → {pubg_name, rating, tags, ...}
    by_nick = {v["pubg_name"].lower(): name for name, v in scores.items()}
    friends = {p.name: p for p in registered}
    attrs = match["data"]["attributes"]
    participants = {x["attributes"]["stats"]["name"].lower(): x["attributes"]["stats"]
                    for x in match["included"] if x["type"] == "participant"}
    teams = sum(1 for x in match["included"] if x["type"] == "roster")
    story = build_story(match, telemetry, {v["pubg_name"] for v in scores.values()})

    ranked = sorted(scores, key=lambda n: (-scores[n]["rating"], -participants[scores[n]["pubg_name"].lower()]["damageDealt"]))
    mvp = ranked[0] if ranked else None
    culprit = analysis.get("culprit") if len(ranked) > 1 else None
    if culprit == mvp:
        culprit = None

    if story:
        members = story.members
        squad_names = [m.name for m in members]
    else:
        squad_names = [v["pubg_name"] for v in scores.values()]
        members = []
    ranked_pubg_names = [scores[name]["pubg_name"] for name in ranked]
    ranked_keys = {name.casefold() for name in ranked_pubg_names}
    squad_names = ranked_pubg_names + [name for name in squad_names if name.casefold() not in ranked_keys]
    if story:
        rank_by_nick = {name.casefold(): rank for rank, name in enumerate(squad_names)}
        story.members.sort(key=lambda member: rank_by_nick.get(member.name.casefold(), len(rank_by_nick)))
    colors = {n: SQUAD[i % len(SQUAD)] for i, n in enumerate(squad_names)}
    stats = [participants.get(n.lower(), {}) for n in squad_names]
    place = int(stats[0].get("winPlace") or 0) if stats else 0
    map_ko = _pubg_map_name(attrs.get("mapName", ""))
    mode = MODES.get(attrs.get("gameMode", ""), attrs.get("gameMode", ""))
    if attrs.get("matchType") == "competitive":
        mode = f"랭크 {mode}"

    def badge(nick: str) -> str | None:
        friend = by_nick.get(nick.lower())
        return "MVP" if friend and friend == mvp else "범인" if friend and friend == culprit else None

    totals = [("팀 킬", str(sum(int(s.get("kills") or 0) for s in stats))),
              ("기절", str(sum(int(s.get("DBNOs") or 0) for s in stats))),
              ("총 딜", f"{sum(float(s.get('damageDealt') or 0) for s in stats):.0f}")]
    files = [("result.png", render_result(story, place, teams, map_ko, mode, attrs["duration"] / 60,
                                          [(n, colors[n]) for n in squad_names], totals))]
    cols = [{"name": n, "color": colors[n], "friend": n.lower() in by_nick, "badge": badge(n)} for n in squad_names]
    distance = lambda s: float(s.get("walkDistance") or 0) + float(s.get("rideDistance") or 0) + float(s.get("swimDistance") or 0)
    rows = [("킬", [int(s.get("kills") or 0) for s in stats], str, True),
            ("기절시킴", [int(s.get("DBNOs") or 0) for s in stats], str, True),
            ("딜량", [round(float(s.get("damageDealt") or 0)) for s in stats], str, True),
            ("어시스트", [int(s.get("assists") or 0) for s in stats], str, True),
            ("부활", [int(s.get("revives") or 0) for s in stats], str, True),
            ("헤드샷 킬", [int(s.get("headshotKills") or 0) for s in stats], str, True),
            ("최장 킬", [round(float(s.get("longestKill") or 0)) for s in stats], lambda v: f"{v}m" if v else "-", True),
            ("생존", [round(float(s.get("timeSurvived") or 0)) for s in stats], lambda v: f"{v // 60}:{v % 60:02d}", True),
            ("이동", [round(distance(s)) for s in stats], lambda v: f"{v / 1000:.1f}km", True)]
    files.append(("squad.png", render_squad(cols, rows)))
    if story and any(m.path for m in story.members):
        route, timeline = render_route_gifs(story, colors, map_ko)
        if len(route) + len(timeline) <= GIF_MAX_BYTES:
            files += [("route.gif", route, "image/gif"), ("timeline.gif", timeline, "image/gif")]
        else:
            files += [("route.png", render_map(story, colors, map_ko)), ("timeline.png", render_timeline(story, colors))]
        if any(m.weapon_damage for m in story.members if m.friend):
            files.append(("weapons.png", render_weapons(story, colors)))

    order = sorted((m.death[0] if m.death else 1e9, m.name) for m in (story.members if story else []))
    facts = {"결과": f"{teams}팀 중 {place}등", "맵": map_ko, "모드": mode,
             "팀 생존(분)": round(story.team_end / 60, 1) if story else None,
             "죽은 순서(먼저→나중)": [by_nick.get(n.lower(), "친구 아닌 팀원") for t, n in order if t < 1e9],
             "선수": [_fact(n, scores[n], participants, story, "MVP" if n == mvp else "범인" if n == culprit else "")
                    for n in ranked]}
    names = [n for n in (mvp, culprit) if n]
    ai = _evaluate(facts, names, gemini_api_key, gemini_model)
    return _payload([friends[n] for n in ranked if n in friends], place, files, ai), files


def _tdm_rounds(duration: float, telemetry: list[dict], winner_id: int | None,
                team_ids: set[int]) -> list[dict]:
    """Infer 10-minute rounds only when kill gaps and the final roster result agree."""
    round_count = round(duration / 600)
    if winner_id is None or round_count not in (2, 3) or abs(duration - round_count * 600) > 35:
        return []
    start = next((event for event in telemetry if event.get("_T") == "LogMatchStart"), None)
    if not start:
        return []
    t0 = datetime.fromisoformat(start["_D"].replace("Z", "+00:00"))
    kind = "LogPlayerKillV2" if any(e.get("_T") == "LogPlayerKillV2" for e in telemetry) else "LogPlayerKill"
    kills = []
    for event in telemetry:
        if event.get("_T") != kind:
            continue
        killer = event.get("killer") or event.get("finisher") or {}
        victim = event.get("victim") or {}
        team_id = killer.get("teamId")
        if team_id not in team_ids or team_id == victim.get("teamId"):
            continue
        seconds = (datetime.fromisoformat(event["_D"].replace("Z", "+00:00")) - t0).total_seconds()
        kills.append((seconds, team_id))
    kills.sort()
    if len(kills) < 4:
        return []
    boundaries = []
    for i in range(1, round_count):
        expected = i * 600
        candidates = [(b[0] - a[0], (a[0] + b[0]) / 2) for a, b in zip(kills, kills[1:])
                      if expected - 90 <= (a[0] + b[0]) / 2 <= expected + 90]
        if not candidates:
            return []
        gap, boundary = max(candidates)
        if gap < 18:
            return []
        boundaries.append(boundary)
    rounds = [{"winner": None, "kills": {team_id: 0 for team_id in team_ids}} for _ in range(round_count)]
    for seconds, team_id in kills:
        index = min(sum(seconds >= boundary for boundary in boundaries), round_count - 1)
        rounds[index]["kills"][team_id] += 1
    for item in rounds:
        ordered = sorted(item["kills"], key=lambda team_id: item["kills"][team_id], reverse=True)
        if len(ordered) != 2 or item["kills"][ordered[0]] == item["kills"][ordered[1]]:
            return []
        item["winner"] = ordered[0]
    return rounds if sum(item["winner"] == winner_id for item in rounds) == 2 else []


def _tdm_weapon_stats(telemetry: list[dict]) -> dict[str, list[dict]]:
    """Weapon damage/kills from the match-end summary, with event fallback."""
    end = next((event for event in telemetry if event.get("_T") == "LogMatchEnd"), None)
    by_account: dict[str, list[dict]] = {}
    for entry in (end or {}).get("allWeaponStats", []):
        account_id = entry.get("accountId")
        weapons = []
        for stat in entry.get("stats", []):
            damage = float(stat.get("damage") or 0)
            kills = sum(int(hit.get("kills") or 0) for hit in stat.get("hitDetails", []))
            if stat.get("weapon") and (damage > 0 or kills > 0):
                weapons.append({"weapon": stat["weapon"], "damage": damage, "kills": kills})
        if account_id:
            by_account[account_id] = sorted(weapons, key=lambda item: (-item["damage"], -item["kills"]))
    gathered: dict[str, dict[str, dict]] = {}
    for event in telemetry:
        kind = event.get("_T")
        if kind == "LogPlayerTakeDamage":
            attacker, victim = event.get("attacker") or {}, event.get("victim") or {}
            weapon = event.get("damageCauserName")
            if not attacker.get("accountId") or not weapon or attacker.get("teamId") == victim.get("teamId"):
                continue
            stat = gathered.setdefault(attacker["accountId"], {}).setdefault(
                weapon, {"weapon": weapon, "damage": 0.0, "kills": 0})
            stat["damage"] += max(0, float(event.get("damage") or 0))
        elif kind == "LogPlayerKillV2":
            killer = event.get("killer") or event.get("finisher") or {}
            info = event.get("killerDamageInfo") or event.get("finishDamageInfo") or {}
            weapon = info.get("damageCauserName")
            if killer.get("accountId") and weapon:
                stat = gathered.setdefault(killer["accountId"], {}).setdefault(
                    weapon, {"weapon": weapon, "damage": 0.0, "kills": 0})
                stat["kills"] += 1
    fallback = {account: sorted(items.values(), key=lambda item: (-item["damage"], -item["kills"]))
                for account, items in gathered.items()}
    return fallback | by_account


def _build_tdm_report(platform: str, match: dict[str, Any], telemetry: list[dict[str, Any]],
                      registered: list[PubgPlayer], gemini_api_key: str | None,
                      gemini_model: str) -> tuple[dict, list[tuple]]:
    """TDM uses roster sides and round stats; battle royale scoring/story does not apply."""
    attrs = match["data"]["attributes"]
    included = match.get("included", [])
    participant_items = [item for item in included if item.get("type") == "participant"]
    participants_by_id = {item.get("id"): item.get("attributes", {}).get("stats", {})
                          for item in participant_items}
    friend_by_nick = {p.nickname.casefold(): p for p in registered if p.platform == platform}
    roster_by_id: dict[int, dict] = {}
    participant_team: dict[str, int] = {}
    for roster in (item for item in included if item.get("type") == "roster"):
        roster_attrs = roster.get("attributes", {})
        roster_stats = roster_attrs.get("stats") or {}
        raw_team_id = roster_stats.get("teamId")
        team_id = int(raw_team_id) if raw_team_id is not None else len(roster_by_id) + 1
        raw_rank = roster_stats.get("rank")
        rank = int(raw_rank) if raw_rank is not None else None
        team = roster_by_id.setdefault(team_id, {"id": team_id, "rank": rank, "players": []})
        if team["rank"] is None:
            team["rank"] = rank
        refs = roster.get("relationships", {}).get("participants", {}).get("data", [])
        for ref in refs:
            participant_id = ref.get("id")
            if participant_id in participants_by_id:
                participant_team[participant_id] = team_id

    # Some custom match payloads omit or underfill roster participant relations.
    # LogMatchStart still gives the TDM team for each account.
    participant_by_player_id = {
        str(stats.get("playerId")): participant_id
        for participant_id, stats in participants_by_id.items() if stats.get("playerId")
    }
    weapons_by_account = _tdm_weapon_stats(telemetry)
    telemetry_team: dict[str, int] = {}
    deaths_by_name: dict[str, int] = {}
    has_kill_v2 = any(event.get("_T") == "LogPlayerKillV2" for event in telemetry)
    for event in telemetry:
        if event.get("_T") == "LogMatchStart":
            for wrapper in event.get("characters", []):
                char = wrapper.get("character") or {}
                account_id = char.get("accountId")
                team_id = char.get("teamId")
                if account_id is None or team_id is None:
                    continue
                participant_id = participant_by_player_id.get(str(account_id))
                if participant_id:
                    telemetry_team[participant_id] = int(team_id)
        elif event.get("_T") == ("LogPlayerKillV2" if has_kill_v2 else "LogPlayerKill"):
            victim = event.get("victim") or {}
            victim_name = str(victim.get("name") or "").casefold()
            if victim_name:
                deaths_by_name[victim_name] = deaths_by_name.get(victim_name, 0) + 1

    for participant_id, team_id in telemetry_team.items():
        participant_team.setdefault(participant_id, team_id)

    for participant_id, stats in participants_by_id.items():
        team_id = participant_team.get(participant_id)
        if team_id is None:
            raw_place = stats.get("winPlace")
            if raw_place is not None:
                team_id = int(raw_place)
                roster_by_id.setdefault(team_id, {"id": team_id, "rank": team_id, "players": []})
                participant_team[participant_id] = team_id
        if team_id is None:
            continue
        team = roster_by_id.setdefault(team_id, {"id": team_id, "rank": None, "players": []})
        name = str(stats.get("name") or "Unknown")
        friend = friend_by_nick.get(name.casefold())
        player = {
            "name": name,
            "friend": friend is not None,
            "friend_name": friend.name if friend else None,
            "kills": int(stats.get("kills") or 0),
            "damage": float(stats.get("damageDealt") or 0),
            "headshots": int(stats.get("headshotKills") or 0),
            "assists": int(stats.get("assists") or 0),
            "deaths": int(stats.get("deaths") or deaths_by_name.get(name.casefold(), 0)),
            "weapons": weapons_by_account.get(str(stats.get("playerId")), []),
        }
        team["players"].append(player)
    teams = sorted(roster_by_id.values(), key=lambda team: (team["rank"] or 999, team["id"]))
    for team in teams:
        team["players"].sort(key=lambda player: (-player["kills"], -player["damage"], player["name"].casefold()))
        team["kills"] = sum(player["kills"] for player in team["players"])
        team["damage"] = sum(player["damage"] for player in team["players"])
    if len(teams) < 2:
        raise ValueError("TDM match is missing its two roster teams; refusing to build a misleading report")

    friend_players = [player for team in teams for player in team["players"] if player["friend"]]
    # Kills lead the TDM ranking; damage, headshots, assists, and fewer deaths break ties.
    order = sorted(friend_players, key=lambda player: (
        -player["kills"], -player["damage"], -player["headshots"], -player["assists"], player["deaths"],
        player["friend_name"].casefold()))
    winner = next((team for team in teams if team["rank"] == 1), None)
    loser = next((team for team in teams if team is not winner), None) if winner else None
    winner_friends = [player for player in order if winner and player in winner["players"]]
    loser_friends = [player for player in order if loser and player in loser["players"]]
    mvp_player = winner_friends[0] if winner_friends else None
    ace_player = loser_friends[0] if loser_friends else None
    featured = {id(player) for player in (mvp_player, ace_player) if player}
    culprit_player = next((player for player in reversed(order) if id(player) not in featured), None)
    mvp = mvp_player["friend_name"] if mvp_player else None
    ace = ace_player["friend_name"] if ace_player else None
    culprit = culprit_player["friend_name"] if culprit_player else None
    # 팀 이름은 그 팀 MVP(진 팀은 ACE) 닉네임으로: TEAM 01 → TEAM TLBMiss. 친구가 없는 팀은 그 팀 1등
    for team, lead in ((winner, mvp_player), (loser, ace_player)):
        if team and team["players"]:
            team["label"] = f"TEAM {(lead or team['players'][0])['name']}"
    for team in teams:
        team.setdefault("label", f"TEAM {team['id']:02d}")
    rounds = _tdm_rounds(float(attrs.get("duration") or 0), telemetry, winner["id"] if winner else None,
                         {team["id"] for team in teams})
    featured_weapon = next((player["weapons"][0]["weapon"] for player in (mvp_player, ace_player)
                            if player and player["weapons"]), None)
    map_name = "Pillar Compound" if attrs.get("mapName") == "PillarCompound_Main" else _pubg_map_name(attrs.get("mapName", ""))
    files = [
        ("result.png", render_tdm_result(map_name, float(attrs.get("duration") or 0) / 60,
                                         teams, rounds, featured_weapon)),
        ("teams.png", render_tdm_comparison(teams, mvp, ace, culprit, featured_weapon)),
    ]
    if any(player["weapons"] for team in teams for player in team["players"]):
        files.append(("weapons.png", render_tdm_weapons(teams, featured_weapon)))
    facts = {
        "모드": "팀 데스매치",
        "맵": map_name,
        "승리 팀": winner["label"] if winner else "확인 불가",
        "라운드 승자": [next(t["label"] for t in teams if t["id"] == item["winner"]) for item in rounds] if rounds else "확인 불가",
        "팀": [{"이름": team["label"], "순위": team["rank"], "팀 킬": team["kills"],
                "팀 피해량": round(team["damage"])} for team in teams],
        "선수": [{"이름": player["friend_name"], "역할": "MVP" if player is mvp_player else
                 "ACE" if player is ace_player else "범인" if player is culprit_player else "팀원",
                 "킬": player["kills"],
                 "딜량": round(player["damage"]), "헤드샷 킬": player["headshots"],
                 "사망": player["deaths"], "어시스트": player["assists"],
                 "주무기": weapon_name(player["weapons"][0]["weapon"]) if player["weapons"] else None}
                for player in order],
    }
    role_names = [name for name in (mvp, ace, culprit) if name]
    ai = _evaluate(facts, role_names, gemini_api_key, gemini_model, mode="tdm")
    players = list(dict.fromkeys(friend_by_nick[p["name"].casefold()] for p in friend_players))
    return _payload(players, 1 if winner else 0, files, ai), files


def _fact(name: str, score: dict, participants: dict, story: Story | None, role: str) -> dict:
    s = participants.get(score["pubg_name"].lower(), {})
    fact = {"이름": name, "역할": role or "팀원", "킬": s.get("kills"), "기절시킴": s.get("DBNOs"),
            "딜량": round(float(s.get("damageDealt") or 0)), "부활": s.get("revives"),
            "생존(분)": round(float(s.get("timeSurvived") or 0) / 60, 1), "감점·가점 이유": score.get("reasons", [])}
    member = next((m for m in (story.members if story else []) if m.name.lower() == score["pubg_name"].lower()), None)
    if member:
        weapons = sorted(member.weapon_damage.items(), key=lambda x: -x[1])[:2]
        fact["주무기"] = [weapon_name(w) for w, _ in weapons]
        if member.knocked:
            fact["기절당한 횟수"] = len(member.knocked)
    return fact


def _payload(players: list[PubgPlayer], place: int, files: list[tuple], ai: dict) -> dict:
    ids = list(dict.fromkeys(p.discord_user_id for p in players if p.discord_user_id))
    components: list[dict] = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, *_ in files]
    words = []
    if ai.get("summary"):
        words.append(f"**총평**\n{ai['summary']}")
    if ai.get("lines"):
        words.append("**평가**\n" + "\n".join(ai["lines"]))
    if words:
        components.append({"type": 10, "content": "\n\n".join(words)})
    return {
        "flags": 32768,  # Components V2 (@silent는 전송할 때 붙는다)
        "allowed_mentions": {"users": ids},
        "components": [{"type": 10, "content": " ".join(f"<@{uid}>" for uid in ids)},
                       {"type": 17, "accent_color": ACCENT_WIN if place == 1 else ACCENT_LOSE, "components": components}],
    }


def _evaluate(facts: dict, names: list[str], api_key: str | None, model: str, mode: str = "squad") -> dict:
    """Gemini: 판 흐름 한 문장 + MVP·범인 한 문장씩. 실패하면 빈 값."""
    if not api_key or not names:
        return {}
    keys = ["summary"] + names
    intro = ("배틀그라운드 팀 데스매치 경기 기록이야. 양 팀 모두 친구들이라 제3자 중계처럼 써. "
             "승리 팀을 정확히 말하고 '우리/상대'라는 표현은 쓰지 마. "
             "자기장, 생존 시간, 이동 경로 같은 배틀로얄 상황은 언급하지 마.\n" if mode == "tdm" else
             "배틀그라운드 스쿼드 경기 기록이야.\n")
    metric_guidance = ("판단은 기록(킬, 피해량, 헤드샷, 사망, 어시스트)에 근거하고, 숫자는 꼭 필요할 때 하나만.\n"
                       if mode == "tdm" else
                       "판단은 기록(킬, 기절, 딜량, 생존, 부활, 주무기, 감점 이유)에 근거하고, 숫자는 꼭 필요할 때 하나만.\n")
    result_guidance = ("팀 결과와 개인 교전 기여를 혼동하지 말고, 팀 승리를 개인 성과로 단정하지 마.\n"
                       if mode == "tdm" else
                       "일찍 전멸한 판(팀 생존이 짧음)에는 캐리·이끌었다 같은 말 대신 '그나마 ~는 버텼어요'처럼. "
                       "'일찍 끊겼다'는 죽은 순서와 생존 시간이 실제로 가장 짧을 때만.\n")
    ace_guidance = ("ACE는 패배 팀에서 가장 잘한 선수야. 패배 사실을 인정하면서 개인 활약을 짧게 칭찬해.\n"
                    if mode == "tdm" else "")
    placement_guidance = ("" if mode == "tdm" else
                          "1등이 아닌 판의 MVP에게 '치킨을 먹었다'처럼 이긴 듯한 말은 쓰지 말고 '아쉽게 놓쳤지만 ~는 빛났어요'처럼. ")
    examples = (["좋은 예: 'TEAM ○○가 두 라운드를 가져가며 승리했어요.'\n",
                 "좋은 예: '○○는 패배 팀에서도 교전 기여가 돋보였어요.'\n"] if mode == "tdm" else
                ["좋은 예 (형식만 참고, 내용은 이 경기 기록으로): '○○는 교전마다 먼저 눕혀줬네요. 총 감각이 살아 있었어요.'\n",
                 "좋은 예: '○○는 너무 일찍 혼자 끊겼어요. 다음 판엔 팀 옆에 붙어서 움직여보세요.'\n"])
    prompt = "".join([
        intro, "친구끼리 보는 디스코드 리포트에 넣을 한마디를 써줘. ",
        "스탯은 이미 이미지로 보여주니까 숫자를 읊지 말고, 해설자나 코치처럼 느낌과 조언을 말해줘.\n",
        "summary: 이 판을 한 문장으로 (50자 이내, 넘으면 안 됨). 무엇이 순위를 갈랐는지, 아쉬운 점이나 다음 판에 해볼 것. ",
        "기록에 없는 장면(차량 추격, 건물 싸움 등)을 지어내지 마.\n",
        "각 선수 이름: 그 친구에게 하는 한마디 (45자 이내로 짧게, 넘으면 안 됨, 1~2문장, 이름으로 시작, 은/는 받침에 맞게). ",
        "MVP는 무엇이 좋았는지 칭찬 (예: 기가 막히네요, 든든했어요), ",
        "범인은 무엇이 아쉬웠는지와 다음에 해볼 것 (예: 다음 판엔 ~해보세요, ~에 신경 써보세요). ",
        ace_guidance, metric_guidance,
        *examples,
        "나쁜 예: '○○는 딜량 478과 6킬을 기록했습니다.' (숫자 나열), 예시 문장이나 표현을 그대로 베끼기\n",
        placement_guidance,
        result_guidance, "이모지·줄표 금지, 존댓말(~요, ~습니다)로 친근하게.\n\n",
        json.dumps(facts, ensure_ascii=False),
    ])
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        best, best_over = None, None
        for _ in range(MAX_TRIES):
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
    except Exception as exc:
        print(f"[pubg] gemini evaluation failed: {exc}")
        return {}
