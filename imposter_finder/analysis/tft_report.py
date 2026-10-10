"""TFT 친구 로비 리포트: 요약 · 친구 덱 한 줄씩 · 탈락 흐름(GIF) + 총평/평가 · lolchess.gg 버튼. 롤과 같은 디자인.

TFT는 개인전이라 범인 없이 보여 주기만 한다. 친구가 1등을 못 한 판은 요약 카드에 그 판 1등도 작게.
아이콘·한글 이름은 CommunityDragon(ko_kr), 티어는 TFT 리그 API(지금 값).
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw

from imposter_finder.analysis.cards import (BG, DEFEAT, HIGHLIGHT, LABEL, PAD, VICTORY, WHITE, WIDTH, _download, ambient_bg,
                                            canvas, font, gif, glass, icon, medallion, png)

CDRAGON = "https://raw.communitydragon.org/latest"
GAME_DATA = f"{CDRAGON}/plugins/rcp-be-lol-game-data/global/default"
EMBLEM = f"{CDRAGON}/plugins/rcp-fe-lol-shared-components/global/default"
QUEUES = {1090: "일반", 1100: "랭크", 1130: "초고속 모드", 1160: "더블업", 6000: "튜토리얼"}
RANKED_QUEUES = {1100}
COST = {1: (128, 128, 128), 2: (17, 178, 136), 3: (32, 122, 199), 4: (196, 64, 218), 5: (255, 185, 59),
        6: (255, 185, 59), 7: (255, 185, 59)}  # 게임 안 코스트 테두리 색
STYLE = {1: (156, 104, 72), 2: (148, 162, 170), 3: (226, 184, 72), 4: (180, 220, 240)}  # 브론즈·실버·골드·프리즘
TIERS = {"IRON": "아이언", "BRONZE": "브론즈", "SILVER": "실버", "GOLD": "골드", "PLATINUM": "플래티넘",
         "EMERALD": "에메랄드", "DIAMOND": "다이아몬드", "MASTER": "마스터", "GRANDMASTER": "그랜드마스터",
         "CHALLENGER": "챌린저"}
DIM = (110, 110, 110)
GOLD_LINE = (200, 155, 60)  # 지나간 시간


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads(_download(f"{CDRAGON}/cdragon/tft/ko_kr.json"))


@lru_cache(maxsize=1)
def _companions() -> dict[int, dict]:
    raw = json.loads(_download(f"{CDRAGON}/plugins/rcp-be-lol-game-data/global/ko_kr/v1/companions.json"))
    return {c["itemId"]: c for c in raw}


def _png(path: str | None) -> str | None:
    return f"{CDRAGON}/game/{path.lower().replace('.tex', '.png')}" if path else None


def companion_icon(p: dict) -> str | None:
    """꼬마 전설 아이콘 (아이템 ID → companions.json)."""
    try:
        c = _companions().get((p.get("companion") or {}).get("item_ID"))
    except Exception:
        return None
    path = (c or {}).get("loadoutsIcon", "")
    return f"{GAME_DATA}/{path.removeprefix('/lol-game-data/assets/').lower()}" if path else None


@lru_cache(maxsize=None)
def _lookup(set_number: int) -> tuple[dict, dict, dict]:
    data = _data()
    sets = data["sets"].get(str(set_number), {})
    units = {c["apiName"]: c for c in sets.get("champions", [])}
    traits = {t["apiName"]: t for t in sets.get("traits", [])}
    items = {i["apiName"]: i for i in data["items"]}
    return units, traits, items


def stage(last_round: int) -> str:
    """마지막 라운드 번호 → 게임 표기 (1스테이지는 4라운드, 그다음은 7라운드씩)."""
    if last_round <= 4:
        return f"1-{last_round}"
    return f"{(last_round - 5) // 7 + 2}-{(last_round - 5) % 7 + 1}"


def core_trait(p: dict) -> dict | None:
    """덱 이름이 되는 핵심 시너지: 켜진 것 중 유닛 수 → 단계 순. 1명짜리 고유 시너지는 뺀다."""
    active = [t for t in p["traits"] if t.get("style", 0) > 0 and t.get("tier_total", 0) > 1]
    return max(active, key=lambda t: (t["num_units"], t["style"]), default=None)


def place_color(place: int) -> tuple:
    return HIGHLIGHT if place == 1 else VICTORY if place <= 4 else LABEL


def _fit(dr: ImageDraw.ImageDraw, text: str, width: float, sizes: tuple, weight: int = 6) -> int:
    return next((n for n in sizes if dr.textlength(text, font=font(n, weight)) <= width), sizes[-1])


def carry_unit(p: dict, set_number: int) -> dict | None:
    """핵심 챔피언: 별 → 아이템 수 → 코스트 순."""
    units_db, _, _ = _lookup(set_number)
    return max(p["units"], key=lambda u: (u.get("tier", 1), len(u.get("itemNames", [])),
                                          units_db.get(u["character_id"], {}).get("cost", 0)), default=None)


def _trait_badge(img: Image.Image, dr: ImageDraw.ImageDraw, x: float, cy: float, trait: dict, set_number: int,
                 size: int = 24) -> None:
    """시너지 단계 색 육각형 + 검게 뺀 아이콘."""
    _, traits_db, _ = _lookup(set_number)
    dr.regular_polygon((x + size / 2, cy, size / 2 + 1), 6, rotation=30, fill=STYLE[min(trait["style"], 4)])
    ic = icon(_png(traits_db.get(trait["name"], {}).get("icon")), round(size * 0.68), radius=0)
    if ic:
        dark = Image.new("RGBA", ic.size, BG + (255,))
        dark.putalpha(ic.getchannel("A"))
        img.paste(dark, (round(x + (size - ic.width) / 2), round(cy - ic.height / 2)), dark)


def _deck_name(img: Image.Image, dr: ImageDraw.ImageDraw, x: float, cy: float, p: dict, set_number: int,
               size: int = 15, color: tuple = WHITE) -> None:
    """덱 이름: 핵심 시너지 로고 + "5 지옥불"."""
    core = core_trait(p)
    if not core:
        return
    _, traits_db, _ = _lookup(set_number)
    badge = size + 7
    _trait_badge(img, dr, x, cy, core, set_number, badge)
    dr.text((x + badge + 6, cy), f"{core['num_units']} {traits_db.get(core['name'], {}).get('name', '')}",
            font=font(size, 6), fill=color, anchor="lm")


def _unit_tile(img: Image.Image, dr: ImageDraw.ImageDraw, x: float, y: float, u: dict, set_number: int,
               tile: int = 54, items: bool = True) -> None:
    """코스트 색 테두리 챔피언 + 위에 ★, 아래 아이템."""
    units_db, _, items_db = _lookup(set_number)
    meta = units_db.get(u["character_id"], {})
    cost = meta.get("cost") or (u.get("rarity", 0) + 1)
    dr.rounded_rectangle((x - 2, y - 2, x + tile + 2, y + tile + 2), radius=9, fill=COST.get(cost, LABEL))
    ic = icon(_png(meta.get("tileIcon")), tile, radius=8) or icon(_png(meta.get("squareIcon")), tile, radius=8)
    if ic:
        img.paste(ic, (round(x), round(y)), ic)
    dr.text((x + tile / 2, y - 1), "★" * u.get("tier", 1), font=font(12, 6),
            fill=HIGHLIGHT if u.get("tier", 1) == 3 else WHITE, anchor="ms", stroke_width=2, stroke_fill=BG)
    if items:
        for k, item in enumerate(u.get("itemNames", [])[:3]):
            iic = icon(_png(items_db.get(item, {}).get("icon")), 17, radius=4)
            if iic:
                img.paste(iic, (round(x + 1 + k * 18), round(y + tile - 8)), iic)


# ── 1. 요약 ─────────────────────────────────────────────
def _summary_block(img: Image.Image, dr: ImageDraw.ImageDraw, x: float, y: float, p: dict, set_number: int,
                   big: bool) -> None:
    """핵심 챔피언 · 꼬마 전설 + 닉네임 · 덱 이름."""
    tile = 64 if big else 50
    carry = carry_unit(p, set_number)
    if carry:
        _unit_tile(img, dr, x, y + 8, carry, set_number, tile, items=False)
    tx = x + tile + 16
    pet = icon(companion_icon(p), 26 if big else 22, radius=13)
    if pet:
        img.paste(pet, (round(tx), round(y + 10)), pet)
    name = p.get("riotIdGameName") or ""
    dr.text((tx + (32 if big else 27), y + 23), name, font=font(19 if big else 15, 6),
            fill=place_color(p["placement"]) if big else WHITE, anchor="lm")
    _deck_name(img, dr, tx, y + (56 if big else 48), p, set_number, 15 if big else 13, WHITE if big else LABEL)


def render_header(match: dict, friends: set[str], background: str | None) -> bytes:
    """친구 중 최고 등수(꼬마 전설·핵심 챔피언·덱 이름). 친구가 1등이 아니면 오른쪽에 1등도 작게."""
    info = match["info"]
    set_number = info.get("tft_set_number", 0)
    img, dr = canvas(160)
    ambient_bg(img, background, dim=0.8, focus_y=0.25)
    best = min((p for p in info["participants"] if p["puuid"] in friends), key=lambda p: p["placement"])
    mode = QUEUES.get(info.get("queue_id"), "일반")
    dr.text((PAD, 24), f"전략적 팀 전투 · {mode} · 시즌 {set_number} · {info['game_length'] / 60:.0f}분",
            font=font(16), fill=LABEL)
    dr.text((PAD, 52), f"#{best['placement']}", font=font(60, 6), fill=place_color(best["placement"]))
    w = dr.textlength(f"#{best['placement']}", font=font(60, 6))
    _summary_block(img, dr, PAD + w + 22, 52, best, set_number, big=True)
    winner = next(p for p in info["participants"] if p["placement"] == 1)
    if winner["puuid"] not in friends:
        bx0 = WIDTH - PAD - 300
        glass(img, (bx0, 48, WIDTH - PAD, 140), radius=14, alpha=22)
        dr.text((bx0 + 16, 64), "1등", font=font(14, 6), fill=HIGHLIGHT, anchor="lm")
        _summary_block(img, dr, bx0 + 50, 54, winner, set_number, big=False)
    return png(img)


# ── 2. 덱 ───────────────────────────────────────────────
def _deck_row(img: Image.Image, dr: ImageDraw.ImageDraw, p: dict, y: int, row_h: int, set_number: int,
              tier: dict | None, partner: dict | None = None) -> None:
    """왼쪽: 등수 · 꼬마 전설+닉네임 · 덱 이름 · 레벨·탈락 · 티어(LP 변동) / 오른쪽: 나머지 시너지, 챔피언.
    더블업이면 등수 옆에 팀원 닉네임."""
    left_w = 210
    glass(img, (PAD - 8, y, WIDTH - PAD + 8, y + row_h - 10), radius=14, alpha=20 if p["placement"] <= 4 else 10)
    dr.text((PAD + 6, y + 8), f"#{p['placement']}", font=font(34, 6), fill=place_color(p["placement"]))
    if partner:
        pw = dr.textlength(f"#{p['placement']}", font=font(34, 6))
        mate = f"팀 {partner.get('riotIdGameName') or ''}"
        dr.text((PAD + 14 + pw, y + 30), mate, font=font(_fit(dr, mate, left_w - pw - 24, (13, 12, 11), 4)), fill=LABEL,
                anchor="lm")
    pet = icon(companion_icon(p), 24, radius=12)
    nx = PAD + 6
    if pet:
        img.paste(pet, (nx, y + 52), pet)
        nx += 30
    name = p.get("riotIdGameName") or ""
    dr.text((nx, y + 64), name, font=font(_fit(dr, name, PAD + left_w - nx - 10, (16, 15, 14, 13, 12)), 6),
            fill=WHITE, anchor="lm")
    _deck_name(img, dr, PAD + 6, y + 90, p, set_number, 13)
    info = f"Lv.{p['level']} · {stage(p['last_round'])} 탈락" if p["placement"] > 1 else f"Lv.{p['level']} · 우승"
    dr.text((PAD + 6, y + 113), info, font=font(12), fill=LABEL, anchor="lm")
    if tier:
        crest = icon(f"{EMBLEM}/{tier['tier'].lower()}.png", 22, radius=0)
        tx = PAD + 92
        if crest:
            img.paste(crest, (tx, y + 102), crest)
            tx += 22
        text = (f"{TIERS[tier['tier']]} · {tier['leaguePoints']}LP" if tier["tier"] in {"MASTER", "GRANDMASTER", "CHALLENGER"}
                else f"{TIERS.get(tier['tier'], tier['tier'])} {tier['rank']} · {tier['leaguePoints']}LP")
        dr.text((tx, y + 113), text, font=font(12, 6), fill=WHITE, anchor="lm")
        if tier.get("delta"):  # 지난 수집 때와 비교한 LP 변동
            tx += dr.textlength(text, font=font(12, 6)) + 5
            dr.text((tx, y + 113), f"{tier['delta']:+d}", font=font(12, 6),
                    fill=VICTORY if tier["delta"] > 0 else DEFEAT, anchor="lm")
    # 오른쪽 위: 핵심을 뺀 나머지 켜진 시너지 (높은 단계 먼저)
    core = core_trait(p)
    x = PAD + left_w
    for t in sorted((t for t in p["traits"] if t.get("style", 0) > 0 and t is not core),
                    key=lambda t: (-t["style"], -t["num_units"])):
        _trait_badge(img, dr, x, y + 22, t, set_number, 24)
        count = f"{t['num_units']}"
        dr.text((x + 27, y + 22), count, font=font(13, 6), fill=WHITE, anchor="lm")
        x += 27 + dr.textlength(count, font=font(13, 6)) + 11
        if x > WIDTH - PAD - 40:
            break
    # 오른쪽 아래: 챔피언
    x = PAD + left_w
    for u in p["units"]:
        _unit_tile(img, dr, x, y + 52, u, set_number)
        x += 64


def render_decks(match: dict, friends: set[str], tiers: dict[str, dict], background: str | None) -> bytes:
    """친구만 등수순 한 줄씩."""
    info = match["info"]
    rows = sorted((p for p in info["participants"] if p["puuid"] in friends), key=lambda p: p["placement"])
    ranked = info.get("queue_id") in RANKED_QUEUES
    top, row_h = 64, 140
    img, dr = canvas(top + row_h * len(rows) + 12)
    ambient_bg(img, background, dim=0.86, focus_y=0.6)
    dr.text((PAD, 20), "친구 덱", font=font(21, 6), fill=WHITE)
    dr.text((WIDTH - PAD, 24), "시너지 · ★ 별 · 테두리 코스트 · 아래 아이템", font=font(14), fill=LABEL, anchor="ra")
    groups = {}
    for q in info["participants"]:
        if q.get("partner_group_id"):
            groups.setdefault(q["partner_group_id"], []).append(q)
    for r, p in enumerate(rows):
        partner = next((q for q in groups.get(p.get("partner_group_id"), []) if q is not p), None)
        _deck_row(img, dr, p, top + r * row_h, row_h, info.get("tft_set_number", 0),
                  tiers.get(p["puuid"]) if ranked else None, partner)
    return png(img)


# ── 3. 탈락 흐름 (GIF) ──────────────────────────────────
ELIM_STEPS = 24  # 고르게 나눈 장면 수, 여기에 탈락 순간을 더한다
ELIM_X0, ELIM_X1 = 70, WIDTH - 90


def _elim_axis(players: list[dict], game_length: float) -> tuple[float, float]:
    """시간축 범위: 앞쪽은 아무도 안 떨어지니 첫 탈락 2분 전(5분 단위)부터."""
    end = max(p["time_eliminated"] for p in players) or game_length
    first = min(p["time_eliminated"] for p in players if p["placement"] > 1)
    return max(0, (first - 120) // 300 * 300), end


def _elim_label(p: dict, friends: set[str]) -> str:
    """친구는 등수·닉네임, 나머지는 등수만 (자리를 아끼려고)."""
    head = "우승" if p["placement"] == 1 else f"{p['placement']}등"
    return f"{head} {p.get('riotIdGameName') or ''}" if p["puuid"] in friends else head


def _elimination_frame(match: dict, friends: set[str], until: float, slots: dict[str, tuple]) -> Image.Image:
    """시간축 한 줄. until까지 탈락한 사람은 그 시각 자리에 꼬마 전설 + 등수·닉네임."""
    info = match["info"]
    players = info["participants"]
    start, end = _elim_axis(players, info["game_length"])
    img, dr = canvas(262)
    dr.text((PAD, 20), "탈락 흐름", font=font(21, 6), fill=WHITE)
    alive = sum(p["time_eliminated"] > until or p["placement"] == 1 for p in players)
    dr.text((WIDTH - PAD, 24), f"{int(until // 60):02d}:{int(until % 60):02d} · 남은 {alive}명", font=font(16, 6),
            fill=HIGHLIGHT, anchor="ra")
    x0, x1, line_y = ELIM_X0, ELIM_X1, 135
    X = lambda t: x0 + (x1 - x0) * (min(max(t, start), end) - start) / (end - start)
    dr.line((x0, line_y, x1, line_y), fill=(50, 56, 62), width=4)
    dr.line((x0, line_y, X(until), line_y), fill=GOLD_LINE, width=4)
    for minute in range(int(start // 60), int(end // 60) + 1, 5):
        dr.text((X(minute * 60), line_y + 92), f"{minute}분", font=font(12), fill=LABEL, anchor="ma")
    for p in sorted(players, key=lambda p: -p["placement"]):
        shown = p["time_eliminated"] <= until and (p["placement"] > 1 or until >= end)
        if not shown:
            continue
        friend = p["puuid"] in friends
        color = place_color(p["placement"]) if friend else DIM
        x, lane_y, right = slots[p["puuid"]]
        dr.line((x, line_y, x, lane_y), fill=color, width=2 if friend else 1)
        dr.ellipse((x - 4, line_y - 4, x + 4, line_y + 4), fill=color)
        medallion(img, x, lane_y, 30 if friend else 24, companion_icon(p), color, crop=0.9)
        label = _elim_label(p, friends)
        f = font(13 if friend else 12, 6 if friend else 4)
        tx = x + 20 if right else x - 20
        dr.text((tx, lane_y), label, font=f, fill=WHITE if friend else DIM, anchor="lm" if right else "rm")
    return img


def _elimination_slots(match: dict, friends: set[str]) -> dict[str, tuple]:
    """이름표 자리: 위아래 네 줄에 번갈아, 앞 이름표와 겹치지 않는 줄로. 반환: puuid → (x, y, 오른쪽에 글자?)."""
    info = match["info"]
    players = info["participants"]
    start, end = _elim_axis(players, info["game_length"])
    x0, x1 = ELIM_X0, ELIM_X1
    lanes = [95, 175, 60, 210]  # 선(135) 위·아래
    last = {lane: -999.0 for lane in lanes}
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    slots = {}
    for p in sorted(players, key=lambda p: (p["time_eliminated"], -p["placement"])):
        x = x0 + (x1 - x0) * (p["time_eliminated"] - start) / (end - start)
        right = x < WIDTH - 220  # 오른쪽 끝 근처는 글자를 왼쪽으로
        width = 40 + probe.textlength(_elim_label(p, friends), font=font(13, 6))
        edge = x - 16 if right else x - width  # 이름표 왼쪽 끝
        lane = next((ln for ln in lanes if last[ln] < edge - 8), min(lanes, key=lambda ln: last[ln]))
        last[lane] = edge + width
        slots[p["puuid"]] = (x, lane, right)
    return slots


def render_eliminations(match: dict, friends: set[str]) -> bytes:
    """GIF: 시간이 흐르며 탈락한 사람이 그 자리에 떨어진다. 마지막 장면은 전체."""
    players = match["info"]["participants"]
    start, end = _elim_axis(players, match["info"]["game_length"])
    times = {start + (end - start) * i / (ELIM_STEPS - 1) for i in range(ELIM_STEPS)} | {p["time_eliminated"] for p in players}
    slots = _elimination_slots(match, friends)
    return gif([_elimination_frame(match, friends, t, slots) for t in sorted(times)])


MAX_LINE_CHARS, MAX_TRIES = 52, 3  # 롤과 같게: 판독 한 줄 길이
ACCENT_WIN, ACCENT_TOP4, ACCENT_REST = 0xFABE0A, 0x0ACBE6, 0x5B5A56  # 카드 왼쪽 색: 친구 최고 등수 1등·4등 안·그 밖


@dataclass(frozen=True)
class Friend:
    name: str
    discord_user_id: str | None


def build_report(match: dict, friends: dict[str, Friend], tiers: dict[str, dict] | None = None,
                 gemini_api_key: str | None = None, gemini_model: str = "") -> tuple[dict, list[tuple]]:
    """friends: 친구 puuid → Friend. tiers: puuid → 리그 API 랭크 항목(+ "delta" LP 변동). 반환: (payload, 파일들)."""
    info = match["info"]
    set_number = info.get("tft_set_number", 0)
    units_db, traits_db, _ = _lookup(set_number)
    puuids = set(friends)
    mine = sorted((p for p in info["participants"] if p["puuid"] in puuids), key=lambda p: p["placement"])
    best = mine[0]
    carry = carry_unit(best, set_number)
    background = _png(units_db.get(carry["character_id"], {}).get("icon")) if carry else None
    files = [("result.png", render_header(match, puuids, background)),
             ("decks.png", render_decks(match, puuids, tiers or {}, background)),
             ("eliminations.gif", render_eliminations(match, puuids), "image/gif")]

    def fact(p: dict) -> dict:
        core = core_trait(p)
        c = carry_unit(p, set_number)
        return {"이름": friends[p["puuid"]].name if p["puuid"] in friends else "친구 아님", "등수": p["placement"],
                "레벨": p["level"], "탈락 라운드": "우승" if p["placement"] == 1 else stage(p["last_round"]),
                "덱": f"{core['num_units']} {traits_db.get(core['name'], {}).get('name', '')}" if core else "없음",
                "핵심 챔피언": units_db.get(c["character_id"], {}).get("name") if c else None}
    winner = next(p for p in info["participants"] if p["placement"] == 1)
    facts = {"모드": QUEUES.get(info.get("queue_id"), "일반"), "친구": [fact(p) for p in mine],
             "1등": "친구" if winner["puuid"] in puuids else fact(winner) | {"이름": "다른 플레이어"}}
    ai = _evaluate(facts, [friends[best["puuid"]].name], gemini_api_key, gemini_model)
    name, tag = best.get("riotIdGameName") or "", best.get("riotIdTagline") or ""
    link = f"https://lolchess.gg/profile/kr/{urllib.parse.quote(name)}-{urllib.parse.quote(tag)}" if name and tag else None
    return _payload(mine, friends, best["placement"], files, ai, link), files


def _payload(mine: list, friends: dict[str, Friend], best: int, files: list[tuple], ai: dict, link: str | None) -> dict:
    ids = list(dict.fromkeys(friends[p["puuid"]].discord_user_id for p in mine if friends[p["puuid"]].discord_user_id))
    components: list[dict] = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, *_ in files]
    words = []
    if ai.get("summary"):
        words.append(f"**총평**\n{ai['summary']}")
    if ai.get("lines"):
        words.append("**평가**\n" + "\n".join(ai["lines"]))
    if words:
        components.append({"type": 10, "content": "\n\n".join(words)})
    if link:  # 맨 아래 lolchess.gg 버튼: 시너지·챔피언 설명은 거기서 (이미지는 호버가 안 된다)
        components.append({"type": 1, "components": [{"type": 2, "style": 5, "label": "lolchess.gg에서 보기", "url": link}]})
    accent = ACCENT_WIN if best == 1 else ACCENT_TOP4 if best <= 4 else ACCENT_REST
    return {
        "flags": 32768,  # Components V2 (@silent는 전송할 때 붙는다)
        "allowed_mentions": {"users": ids},
        "components": [{"type": 10, "content": " ".join(f"<@{uid}>" for uid in ids)},
                       {"type": 17, "accent_color": accent, "components": components}],
    }


def _evaluate(facts: dict, names: list[str], api_key: str | None, model: str) -> dict:
    """Gemini: 로비 흐름 한 문장 + 친구 중 최고 등수 한 문장. 개인전이라 범인은 없다. 실패하면 빈 값."""
    if not api_key:
        return {}
    keys = ["summary"] + names
    prompt = ("전략적 팀 전투(TFT) 경기 기록이야. 친구 여럿이 같은 로비에서 한 판이야. 디스코드 리포트에 넣을 한마디를 써줘. "
              "덱과 등수는 이미 이미지로 보여주니까 숫자를 읊지 말고, 해설자처럼 느낌과 한 줄 조언을 말해줘.\n"
              "summary: 이 로비를 한 문장으로 (50자 이내, 넘으면 안 됨). 친구들끼리 누가 앞섰는지, 어떤 덱이 통했는지.\n"
              "각 이름: 친구 중 최고 등수에게 하는 한마디 (45자 이내, 이름으로 시작, 은/는 받침에 맞게). 덱 선택이나 마무리를 칭찬해.\n"
              "개인전이라 범인이나 탓하는 말은 쓰지 마. 1등이 친구가 아니면 '아쉽게 우승은 놓쳤지만'처럼.\n"
              "기록에 없는 장면(증강, 리롤 타이밍 등)을 지어내지 마. 이모지·줄표 금지, 존댓말(~요, ~습니다)로 친근하게.\n\n"
              + json.dumps(facts, ensure_ascii=False))
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
    except Exception as exc:
        print(f"[tft] gemini evaluation failed: {exc}")
        return {}
