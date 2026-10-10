"""롤 친구 경기 리포트: 결과 배너 · 선수 · 오브젝트 · 골드 차이 이미지 + Gemini 요약·평가."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from io import BytesIO

from PIL import Image, ImageChops, ImageDraw

from imposter_finder.analysis.cards import (ALLY, BG, DEFEAT, ENEMY, GIF_MAX_BYTES, GOLD, HIGHLIGHT, LABEL, PAD,
                                            SLATE, SS, TRACK, VICTORY, WHITE, WIDTH, _download, ambient_bg, backdrop,
                                            bar, canvas, edge_shadow, font, gif, glass, header, icon, medallion, pill, png,
                                            shade, split_bar)
from imposter_finder.games.lol import ARENA_QUEUES, QUEUE_NAMES, DDragon, opgg_link

ACCENT_WIN, ACCENT_LOSE = 0x0ACBE6, 0xFF2345  # 카드 왼쪽 색: 롤 클라이언트 승리·패배
MAX_LINE_CHARS, MAX_TRIES = 52, 3  # 총평·평가 한 줄 길이 (넘으면 디스코드에서 줄이 바뀐다)
AMBIENT_DIM = 0.85  # 흐린 원화 바탕을 얼마나 어둡게 (1이면 원화 없음)
GOLD_PANEL = False  # 골드 그래프 뒤 반투명 판
MINIMAP_ICONS = "https://raw.communitydragon.org/latest/game/assets/ux/minimap/icons"
# 게임 안 미니맵 컬러 아이콘 (64px)
OBJECTIVES = [("킬", "champion", "champion_dead"), ("포탑", "tower", "tower"), ("억제기", "inhibitor", "inhibitor"),
              ("드래곤", "dragon", "dragon"), ("바론", "baron", "baron"), ("전령", "riftHerald", "riftherald"),
              ("공허 유충", "horde", "grub")]
MONSTER_ICONS = {"BARON_NASHOR": "baron", "RIFTHERALD": "riftherald", "ATAKHAN": "atakhan_v"}
DRAGON_ICONS = {"FIRE_DRAGON": "dragon_infernal", "WATER_DRAGON": "dragon_ocean", "EARTH_DRAGON": "dragon_mountain",
                "AIR_DRAGON": "dragon_cloud", "HEXTECH_DRAGON": "dragon_hextech", "CHEMTECH_DRAGON": "dragon_chemtech",
                "ELDER_DRAGON": "dragon_elder"}


@dataclass(frozen=True)
class Friend:
    name: str
    discord_user_id: str | None


# ── 점수와 MVP·범인 ─────────────────────────────────────
def _team_scores(team: list[dict], minutes: float) -> dict[str, dict]:
    """팀 5명 안에서 항목별 순위를 매겨 합산한다 (딜 25, KDA 20, 킬 관여 15, 받은 피해·CS·시야·데스 각 10)."""
    kills = sum(p["kills"] for p in team) or 1
    stats = {p["puuid"]: {"kda": (p["kills"] + p["assists"]) / max(p["deaths"], 1),
                          "kp": (p["kills"] + p["assists"]) / kills,
                          "dmg": p["totalDamageDealtToChampions"],
                          "taken": p["totalDamageTaken"] + p.get("damageSelfMitigated", 0) * 0.5,
                          "cs": (p["totalMinionsKilled"] + p["neutralMinionsKilled"]) / minutes,
                          "vision": p["visionScore"] / minutes,
                          "deaths": -p["deaths"]} for p in team}
    weights = {"kda": 0.2, "kp": 0.15, "dmg": 0.25, "taken": 0.1, "cs": 0.1, "vision": 0.1, "deaths": 0.1}
    for pid in stats:
        stats[pid]["score"] = 0.0
    for key, weight in weights.items():
        for rank, pid in enumerate(sorted(stats, key=lambda pid: stats[pid][key])):
            stats[pid]["score"] += weight * rank / max(len(stats) - 1, 1)
    for rank, pid in enumerate(sorted(stats, key=lambda pid: -stats[pid]["score"]), 1):
        stats[pid]["rank"] = f"{rank}/{len(stats)}"
    return stats


# ── 이미지 ──────────────────────────────────────────────
def render_banner(won: bool, mode: str, minutes: int, background: str | None,
                  left: dict, right: dict | None) -> bytes:
    """스코어보드형 결과 배너. 가운데 승패, 왼쪽 우리 팀, 오른쪽 상대 팀.

    left/right: {"label", "kills", "gold", "icons": [(url, is_friend)]}. right가 None이면 왼쪽만 (아레나).
    """
    height = 190
    img, dr = canvas(height)
    if background:  # MVP 챔피언 원화를 어둡게 깔아 중계 화면처럼
        try:
            art = Image.open(BytesIO(_download(background))).convert("RGB")
            art = art.resize((WIDTH, int(art.height * WIDTH / art.width)))
            top = int(art.height * 0.12)
            art = art.crop((0, top, WIDTH, top + height))
            img.paste(Image.blend(art, Image.new("RGB", art.size, BG), 0.72))
        except Exception:
            pass
    color = VICTORY if won else DEFEAT
    cx = WIDTH // 2

    # 가운데: 승패 · 모드
    word = "승리" if won else "패배"
    dr.text((cx, 38), word, font=font(64, 6), fill=color, anchor="ma")
    dr.text((cx, 120), f"{mode} · {minutes}분", font=font(22, 4), fill=WHITE, anchor="ma")

    def side(info: dict, align: str) -> None:
        sign = -1 if align == "left" else 1
        edge = PAD if align == "left" else WIDTH - PAD
        anchor = "la" if align == "left" else "ra"
        dr.text((edge, 20), info["label"], font=font(18), fill=ALLY if align == "left" else ENEMY, anchor=anchor)
        if info.get("kills") is not None:
            dr.text((edge, 44), str(info["kills"]), font=font(46, 6), fill=WHITE, anchor=anchor)
            kills_w = dr.textlength(str(info["kills"]), font=font(46, 6))
            gx = edge + (kills_w + 16) * (-sign)
            dr.text((gx, 52), "킬", font=font(16), fill=LABEL, anchor=anchor)
            if info.get("gold"):
                dr.text((gx, 74), f"{info['gold'] / 1000:.1f}k 골드", font=font(16), fill=GOLD, anchor=anchor)
        size, gap = 42, 6
        for n, (url, is_friend) in enumerate(info["icons"]):
            x = edge + n * (size + gap) if align == "left" else edge - size - n * (size + gap)
            champ = icon(url, size, radius=8)
            if champ:
                img.paste(champ, (int(x), 120), champ)
            if is_friend:
                dr.rounded_rectangle((x - 2, 118, x + size + 1, 120 + size + 1), radius=10, outline=HIGHLIGHT, width=2)

    side(left, "left")
    if right:
        side(right, "right")
    return png(img)


def render_players(columns: list[dict], rows: list[tuple], background: str | None) -> bytes:
    """라벨 칸 + 친구 칸을 같은 폭으로. 친구 칸마다 챔피언 로딩 원화."""
    head_h, row_h = 200, 54
    height = head_h + row_h * len(rows) + 12
    img, dr = canvas(height)
    col_w = WIDTH / (len(columns) + 1)
    ambient_bg(img, background, (0, 0, round(col_w), height), dim=min(AMBIENT_DIM + 0.05, 1))  # 라벨 칸: MVP 원화 색감
    dr.text((24, 96), "선수 비교", font=font(22, 6), fill=WHITE)
    dr.text((24, 128), "점수 높은 순", font=font(15), fill=LABEL)
    for c, col in enumerate(columns):
        x0, x1 = round((c + 1) * col_w), round((c + 2) * col_w)  # 칸 사이 틈 없이
        backdrop(img, col.get("art_url"), (x0, 0, x1, height), dim=0.66, focus_y=0.1, fade=0.75)
    for c in range(len(columns)):  # 칸 경계는 선 대신 그늘
        edge_shadow(img, round((c + 1) * col_w), height)
    for c, col in enumerate(columns):
        cx = int((c + 1.5) * col_w)
        color = HIGHLIGHT if col["badge"] in {"MVP", "ACE"} else DEFEAT if col["badge"] == "범인" else WHITE
        ic = icon(col["icon_url"], 56)
        if ic:
            img.paste(ic, (cx - 28, 16), ic)
        if col["badge"]:
            dr.rounded_rectangle((cx - 31, 13, cx + 31, 75), radius=14, outline=color, width=3)
            pill(dr, cx, 114, col["badge"], color)
        dr.text((cx, 84), col["name"], font=font(24, 6), fill=color, anchor="ma")
        dr.text((cx, 146), col["champion"], font=font(17), fill=LABEL, anchor="ma")
        dr.text((cx, 170), col["kda"], font=font(20, 6), fill=WHITE, anchor="ma")
    for r, (label, values, fmt) in enumerate(rows):
        y = head_h + r * row_h
        dr.text((24, y + 14), label, font=font(19), fill=LABEL)
        hi = max(values) or 1
        for c, v in enumerate(values):
            x0, x1 = round((c + 1) * col_w) + 16, round((c + 2) * col_w) - 16
            top = v == hi
            dr.text((x0, y + 4), fmt(v), font=font(20, 6), fill=WHITE if top else LABEL)
            bar(img, (x0, y + 33, x1, y + 39), TRACK, dim_side=None)
            bar(img, (x0, y + 33, x0 + (x1 - x0) * v / hi, y + 39), GOLD if top else SLATE)
    return png(img)


def render_objectives(objectives: list[tuple], dragons: list[tuple[str, bool]], background: str | None) -> bytes:
    """objectives: [(이름, 우리, 상대, 아이콘)], dragons: [(아이콘 URL, 우리 팀이 가져갔나)] 시간 순서."""
    height = 222 + (88 if dragons else 0)
    img, dr = canvas(height)
    ambient_bg(img, background, dim=AMBIENT_DIM, focus_y=0.45)
    top = header(dr, "오브젝트", [(ALLY, "우리 팀"), (ENEMY, "상대 팀")])
    cell = (WIDTH - PAD * 2) / len(objectives)
    for n, (label, ours, theirs, icon_url) in enumerate(objectives):
        cx = int(PAD + n * cell + cell / 2)
        glass(img, (PAD + n * cell + 4, top - 14, PAD + (n + 1) * cell - 4, top + 128))
        ic = icon(icon_url, 38, radius=0)
        if ic:
            img.paste(ic, (cx - 19, top), ic)
        dr.text((cx, top + 46), label, font=font(15), fill=LABEL, anchor="ma")
        dr.text((cx - 8, top + 70), str(ours), font=font(26, 6), fill=ALLY if ours > theirs else WHITE, anchor="ra")
        dr.text((cx, top + 70), ":", font=font(22, 4), fill=LABEL, anchor="ma")
        dr.text((cx + 8, top + 70), str(theirs), font=font(26, 6), fill=ENEMY if theirs > ours else WHITE, anchor="la")
        split_bar(img, cx - cell / 2 + 18, cx + cell / 2 - 18, top + 112, ours, theirs)
    if dragons:  # 드래곤 순서: 누가 어떤 드래곤을 먹었나
        y = 236
        glass(img, (PAD + 4, y - 12, WIDTH - PAD - 4, y + 54))
        dr.text((PAD + 22, y + 11), "드래곤 순서", font=font(18), fill=LABEL)
        for n, (url, ours) in enumerate(dragons):
            medallion(img, PAD + 170 + n * 56, y + 21, 46, url, ALLY if ours else ENEMY, ring_w=3)
    return png(img)


def render_gold(diffs: list[int], events: list[tuple[float, str, bool]], background: str | None) -> bytes:
    """우리 팀 기준 골드 차이 곡선. events: [(분, 아이콘 URL, 우리 팀이 가져갔나)] 바론·드래곤 등."""
    return png(_gold_image(diffs, events, background))


GOLD_EVENT_GAP = 0.3  # 1분 장면과 이보다 가까운 오브젝트 장면은 합친다 (분)


def render_gold_gif(diffs: list[int], events: list[tuple[float, str, bool]], background: str | None) -> bytes:
    """골드 차이 GIF: 1분마다 + 오브젝트 순간 장면. 축은 경기 전체 기준이라 재생 중에 흔들리지 않는다."""
    times = [float(m) for m in range(len(diffs))]
    for minute, *_ in sorted(events):
        if 0 < minute < times[-1] and min(abs(minute - t) for t in times) >= GOLD_EVENT_GAP:
            times.append(minute)
    return gif([_gold_image(diffs, events, background, t) for t in sorted(times)], dither=True)


def _gold_image(diffs: list[int], events: list[tuple[float, str, bool]], background: str | None,
                until: float | None = None) -> Image.Image:
    height = 390
    img, dr = canvas(height)
    ambient_bg(img, background, dim=AMBIENT_DIM, focus_y=0.75)
    header(dr, "골드 차이", [(ALLY, "우리 팀 우세"), (ENEMY, "상대 우세")])
    gx0, gx1, gy0, gy1 = PAD + 62, WIDTH - PAD - 70, 124, height - 48
    mid, half = (gy0 + gy1) / 2, (gy1 - gy0) / 2
    span = max(max(abs(d) for d in diffs), 1000)
    last = max(len(diffs) - 1, 1)
    px = lambda minute: gx0 + (gx1 - gx0) * minute / last
    py = lambda d: mid - half * d / span
    if GOLD_PANEL:  # 아이콘·시간까지 감싸야 어색하지 않다
        glass(img, (PAD - 8, 70, WIDTH - PAD + 8, height - 8), alpha=10)
    for minute in range(0, len(diffs), 5):
        dr.text((px(minute), gy1 + 12), f"{minute}분", font=font(15), fill=LABEL, anchor="ma")
    dr.text((gx0 - 12, py(span)), f"+{span / 1000:.1f}k", font=font(15), fill=ALLY, anchor="rm")
    dr.text((gx0 - 12, mid), "0", font=font(15), fill=LABEL, anchor="rm")
    dr.text((gx0 - 12, py(-span)), f"-{span / 1000:.1f}k", font=font(15), fill=ENEMY, anchor="rm")

    # 오브젝트 시점: 위쪽 띠에 아이콘, 그래프에 옅은 세로선 (GIF 장면에선 그 시각이 지난 것만)
    slot = gx0 - 99.0
    for minute, url, ours in sorted(events):
        x = px(minute)
        ix = max(x, slot + 30)  # 겹치면 오른쪽으로 민다
        slot = ix
        if until is not None and minute > until:
            continue
        color = ALLY if ours else ENEMY
        for y in range(gy0 - 6, gy1, 6):  # 점선
            dr.line((x, y, x, y + 2), fill=shade(color, 0.45), width=1)
        medallion(img, ix, 94, 30, url, color)

    if until is not None:  # GIF 장면: until분까지, 끝은 다음 분과 이어 그린다
        whole = min(int(until), len(diffs) - 1)
        shown = diffs[:whole + 1]
        if whole < len(diffs) - 1 and until > whole:
            shown.append(round(diffs[whole] + (diffs[whole + 1] - diffs[whole]) * (until - whole)))
        px_full = px
        px = lambda i: px_full(i if i <= whole else until)
        diffs = shown

    # 곡선과 면: 크게 그려 줄인다
    layer = Image.new("RGBA", (WIDTH * SS, height * SS), (0, 0, 0, 0))
    pts = [(px(i) * SS, py(d) * SS) for i, d in enumerate(diffs)]
    area = Image.new("L", layer.size, 0)
    ImageDraw.Draw(area).polygon([(pts[0][0], mid * SS)] + pts + [(pts[-1][0], mid * SS)], fill=255)
    # 0선에서 멀수록 진하게
    ramp = Image.new("L", (1, layer.height), 0)
    for y in range(layer.height):
        ramp.putpixel((0, y), int(40 + 125 * min(abs(y / SS - mid) / half, 1)))
    area = ImageChops.multiply(area, ramp.resize(layer.size))
    for color, box in ((ALLY, (0, int(mid * SS), layer.width, layer.height)), (ENEMY, (0, 0, layer.width, int(mid * SS)))):
        alpha = area.copy()
        alpha.paste(0, box)
        fill = Image.new("RGBA", layer.size, color + (0,))
        fill.putalpha(alpha)
        layer = Image.alpha_composite(layer, fill)
    ld = ImageDraw.Draw(layer)
    side = lambda d: ALLY if d >= 0 else ENEMY
    for i in range(len(diffs) - 1):
        (x1, y1), (x2, y2), d1, d2 = pts[i], pts[i + 1], diffs[i], diffs[i + 1]
        if d1 * d2 < 0:  # 0선을 지나는 구간은 색을 나눈다
            xm = x1 + (x2 - x1) * d1 / (d1 - d2)
            ld.line((x1, y1, xm, mid * SS), fill=side(d1), width=3 * SS)
            ld.line((xm, mid * SS, x2, y2), fill=side(d2), width=3 * SS)
        else:
            ld.line((x1, y1, x2, y2), fill=side(d1 if d1 else d2), width=3 * SS)
        ld.ellipse((x2 - 1.5 * SS, y2 - 1.5 * SS, x2 + 1.5 * SS, y2 + 1.5 * SS), fill=side(d2))  # 꺾이는 곳 매끄럽게
    ex, ey = pts[-1]
    ld.ellipse((ex - 6 * SS, ey - 6 * SS, ex + 6 * SS, ey + 6 * SS), fill=side(diffs[-1]), outline=BG, width=2 * SS)
    layer = layer.resize((WIDTH, height), Image.LANCZOS)
    img.paste(layer, (0, 0), layer)
    for x in range(gx0, gx1, 8):  # 0선은 점선
        dr.line((x, mid, x + 3, mid), fill=LABEL, width=1)
    dr.text((ex / SS + 14 if until is not None else gx1 + 14, ey / SS), f"{diffs[-1] / 1000:+.1f}k",
            font=font(17, 6), fill=side(diffs[-1]), anchor="lm")
    return img


# ── 리포트 ──────────────────────────────────────────────
def _gold_diffs(info: dict, timeline: dict, team_id: int) -> list[int]:
    team_of = {p["participantId"]: p["teamId"] for p in info["participants"]}
    diffs = []
    for frame in timeline["info"]["frames"]:
        gold = {100: 0, 200: 0}
        for pid, pf in frame["participantFrames"].items():
            gold[team_of[int(pid)]] += pf["totalGold"]
        diffs.append(gold[team_id] - gold[300 - team_id])
    return diffs


def _monster_events(timeline: dict | None, team_id: int) -> list[tuple[float, str, bool, bool]]:
    """드래곤·바론·전령·아타칸 처치: [(분, 아이콘 URL, 우리 팀이 가져갔나, 드래곤인가)] 시간 순서."""
    if not timeline:
        return []
    events = []
    for frame in timeline["info"]["frames"]:
        for event in frame["events"]:
            if event.get("type") != "ELITE_MONSTER_KILL":
                continue
            kind = event.get("monsterType")
            is_dragon = kind == "DRAGON"
            name = DRAGON_ICONS.get(event.get("monsterSubType", ""), "dragon") if is_dragon else MONSTER_ICONS.get(kind)
            if name:
                events.append((event["timestamp"] / 60000, f"{MINIMAP_ICONS}/{name}.png",
                               event.get("killerTeamId") == team_id, is_dragon))
    return events


def _gold_story(d: list[int]) -> str:
    """골드 흐름을 모델이 잘못 읽지 않게 사실 문장으로 정리한다."""
    at15 = min(15, len(d) - 1)
    worst, best = min(range(len(d)), key=lambda i: d[i]), max(range(len(d)), key=lambda i: d[i])
    return (f"15분 {d[at15]:+,}골드, 최대 열세 {worst}분 {d[worst]:+,}, 최대 우세 {best}분 {d[best]:+,}, "
            f"종료 시 {d[-1]:+,}")


def build_report(match: dict[str, Any], timeline: dict[str, Any] | None, friends: dict[str, Friend],
                 ddragon: DDragon, gemini_api_key: str | None, gemini_model: str) -> tuple[dict, list[tuple]]:
    """friends는 puuid → Friend. 반환: (Components V2 payload, [(filename, png)])."""
    info = match["info"]
    if info["queueId"] in ARENA_QUEUES:
        return _build_arena(match, friends, ddragon, gemini_api_key, gemini_model)

    mine = [p for p in info["participants"] if p["puuid"] in friends]
    team_id = mine[0]["teamId"]
    team = [p for p in info["participants"] if p["teamId"] == team_id]
    minutes = info["gameDuration"] / 60
    won = mine[0]["win"]
    best_badge = "MVP" if won else "ACE"
    stats = _team_scores(team, minutes)
    mvp = max(mine, key=lambda p: stats[p["puuid"]]["score"])
    culprit = min(mine, key=lambda p: stats[p["puuid"]]["score"]) if len(mine) > 1 else None
    name = lambda p: friends[p["puuid"]].name

    cols = sorted(mine, key=lambda p: -stats[p["puuid"]]["score"])
    columns = [{"name": name(p), "champion": ddragon.champion_name(p["championName"]),
                "icon_url": ddragon.champion_icon(p["championName"]),
                "art_url": f"https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{p['championName']}_0.jpg",
                "kda": f"{p['kills']}/{p['deaths']}/{p['assists']}",
                "badge": best_badge if p is mvp else "범인" if p is culprit else None} for p in cols]
    rows = [("딜량", [p["totalDamageDealtToChampions"] for p in cols], lambda v: f"{v / 1000:.1f}k"),
            ("받은 피해", [p["totalDamageTaken"] for p in cols], lambda v: f"{v / 1000:.1f}k"),
            ("KDA", [stats[p["puuid"]]["kda"] for p in cols], lambda v: f"{v:.1f}"),
            ("킬 관여", [stats[p["puuid"]]["kp"] * 100 for p in cols], lambda v: f"{v:.0f}%"),
            ("분당 CS", [stats[p["puuid"]]["cs"] for p in cols], lambda v: f"{v:.1f}"),
            ("시야 점수", [p["visionScore"] for p in cols], lambda v: f"{v}")]
    objectives_by_team = {t["teamId"]: t["objectives"] for t in info["teams"]}
    us, them = objectives_by_team[team_id], objectives_by_team[300 - team_id]
    objectives = [(label, us.get(key, {}).get("kills", 0), them.get(key, {}).get("kills", 0), f"{MINIMAP_ICONS}/{ic}.png")
                  for label, key, ic in OBJECTIVES]

    queue = QUEUE_NAMES.get(info["queueId"], info.get("gameMode", ""))
    enemy = [p for p in info["participants"] if p["teamId"] != team_id]
    friend_ids = {p["puuid"] for p in mine}
    side_info = lambda label, members: {
        "label": label, "kills": sum(p["kills"] for p in members), "gold": sum(p["goldEarned"] for p in members),
        "icons": [(ddragon.champion_icon(p["championName"]), p["puuid"] in friend_ids) for p in members]}
    splash = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{mvp['championName']}_0.jpg"
    monsters = _monster_events(timeline, team_id)
    files = [("result.png", render_banner(won, queue, int(minutes), splash,
                                          side_info(f"우리 팀 · 친구 {len(mine)}명", team), side_info("상대 팀", enemy))),
             ("players.png", render_players(columns, rows, splash)),
             ("objectives.png", render_objectives(objectives, [(url, ours) for _, url, ours, dragon in monsters if dragon],
                                                    splash))]
    diffs = _gold_diffs(info, timeline, team_id) if timeline else []
    if diffs:
        events = [(minute, url, ours) for minute, url, ours, _ in monsters]
        animated = render_gold_gif(diffs, events, splash)
        files.append(("gold.gif", animated, "image/gif") if len(animated) <= GIF_MAX_BYTES
                     else ("gold.png", render_gold(diffs, events, splash)))

    facts = {"결과": "승리" if won else "패배", "오브젝트(우리:상대)": {o[0]: f"{o[1]}:{o[2]}" for o in objectives},
             "골드 흐름(우리 팀 기준)": _gold_story(diffs) if diffs else "없음",
             "선수": [_player_fact(p, name(p), best_badge if p is mvp else "범인", stats, ddragon)
                    for p in (mvp, culprit) if p]}
    ai = _evaluate(facts, [name(p) for p in (mvp, culprit) if p], gemini_api_key, gemini_model)
    return _payload(mine, friends, won, files, ai, opgg_link([mvp] + [p for p in mine if p is not mvp], info)), files


POSITIONS = {"TOP": "탑", "JUNGLE": "정글", "MIDDLE": "미드", "BOTTOM": "원딜", "UTILITY": "서포터"}


def _player_fact(p: dict, name: str, role: str, stats: dict, ddragon: DDragon) -> dict:
    st = stats[p["puuid"]]
    return {"이름": name, "역할": role, "챔피언": ddragon.champion_name(p["championName"]),
            "포지션": POSITIONS.get(p.get("teamPosition", ""), "알 수 없음"),
            "KDA": f"{p['kills']}/{p['deaths']}/{p['assists']}", "딜량": p["totalDamageDealtToChampions"],
            "받은 피해": p["totalDamageTaken"], "킬 관여": f"{st['kp'] * 100:.0f}%", "분당 CS": round(st["cs"], 1),
            "시야 점수": p["visionScore"], "제어 와드": p.get("visionWardsBoughtInGame", 0),
            "포탑 피해": p.get("damageDealtToBuildings", 0), "팀 안 종합 순위": st.get("rank")}


def _build_arena(match: dict, friends: dict[str, Friend], ddragon: DDragon, key: str | None, model: str):
    """아레나: 2인 1조 순위전이라 오브젝트·골드 없이 순위와 딜 위주."""
    info = match["info"]
    mine = [p for p in info["participants"] if p["puuid"] in friends]
    place = lambda p: p.get("placement") or p.get("subteamPlacement") or 8
    ranked = sorted(mine, key=lambda p: (place(p), -p["totalDamageDealtToChampions"]))
    mvp, culprit = ranked[0], (ranked[-1] if len(ranked) > 1 else None)
    best_badge = "MVP" if place(mvp) <= 4 else "ACE"
    name = lambda p: friends[p["puuid"]].name
    columns = [{"name": name(p), "champion": ddragon.champion_name(p["championName"]),
                "icon_url": ddragon.champion_icon(p["championName"]),
                "kda": f"{p['kills']}/{p['deaths']}/{p['assists']}",
                "badge": best_badge if p is mvp else "범인" if p is culprit else None} for p in ranked]
    rows = [("순위", [9 - place(p) for p in ranked], lambda v: f"{9 - v}등"),
            ("딜량", [p["totalDamageDealtToChampions"] for p in ranked], lambda v: f"{v / 1000:.1f}k"),
            ("받은 피해", [p["totalDamageTaken"] for p in ranked], lambda v: f"{v / 1000:.1f}k"),
            ("킬", [p["kills"] for p in ranked], lambda v: f"{v}")]
    best = place(mvp)
    splash = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{mvp['championName']}_0.jpg"
    files = [("result.png", render_banner(best <= 4, f"아레나 최고 {best}등", int(info["gameDuration"] / 60), splash,
                                          {"label": f"친구 {len(mine)}명", "kills": None,
                                           "icons": [(c["icon_url"], True) for c in columns]}, None)),
             ("players.png", render_players(columns, rows, splash))]
    facts = {"모드": "아레나 (2인 1조, 순위전)", "선수": [{"이름": name(p), "역할": best_badge if p is mvp else "범인",
                                                     "순위": place(p), "딜량": p["totalDamageDealtToChampions"],
                                                     "KDA": f"{p['kills']}/{p['deaths']}/{p['assists']}"}
                                                    for p in (mvp, culprit) if p]}
    ai = _evaluate(facts, [name(p) for p in (mvp, culprit) if p], key, model)
    return _payload(mine, friends, best <= 4, files, ai, opgg_link([mvp] + [p for p in mine if p is not mvp], info)), files


def _payload(mine: list, friends: dict, won: bool, files: list[tuple], ai: dict, link: str | None = None) -> dict:
    ids = list(dict.fromkeys(friends[p["puuid"]].discord_user_id for p in mine if friends[p["puuid"]].discord_user_id))
    components: list[dict] = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, *_ in files]
    # 총평과 평가는 이미지 아래에 모은다
    words = []
    if ai.get("summary"):
        words.append(f"**총평**\n{ai['summary']}")
    if ai.get("lines"):
        words.append("**평가**\n" + "\n".join(ai["lines"]))
    if words:
        components.append({"type": 10, "content": "\n\n".join(words)})
    if link:  # 맨 아래 OP.GG 버튼
        label = "OP.GG 경기 전적 보기" if "/matches/" in link else "OP.GG 전적 보기"
        components.append({"type": 1, "components": [{"type": 2, "style": 5, "label": label, "url": link}]})
    return {
        "flags": 32768,  # Components V2 (@silent는 전송할 때 붙는다)
        "allowed_mentions": {"users": ids},
        "components": [{"type": 10, "content": " ".join(f"<@{uid}>" for uid in ids)},
                       {"type": 17, "accent_color": ACCENT_WIN if won else ACCENT_LOSE, "components": components}],
    }


def _evaluate(facts: dict, names: list[str], api_key: str | None, model: str) -> dict:
    """Gemini: 경기 흐름 한 문장 + MVP·범인 한 문장씩. 실패하면 빈 값."""
    if not api_key:
        return {}
    keys = ["summary"] + names
    prompt = ("리그 오브 레전드 경기 기록이야. 친구끼리 보는 디스코드 리포트에 넣을 한마디를 써줘. "
              "스탯은 이미 이미지로 보여주니까 숫자를 읊지 말고, 해설자나 코치처럼 느낌과 조언을 말해줘.\n"
              "summary: 이 판을 한 문장으로 (50자 이내, 넘으면 안 됨). 무엇이 승부를 갈랐는지, 아쉬운 점이나 다음 판에 해볼 것. "
              "골드 흐름과 오브젝트 기록이 근거지만 숫자는 쓰지 마. 기록에 없는 장면(한타, 갱킹 등)을 지어내지 마.\n"
              "각 선수 이름: 그 친구에게 하는 한마디 (45자 이내로 짧게, 넘으면 안 됨, 1~2문장, 이름으로 시작, 은/는 받침에 맞게). "
              "MVP는 이긴 팀의 활약을 칭찬하고, ACE는 진 팀에서도 돋보인 플레이를 칭찬해. "
              "범인은 무엇이 아쉬웠는지와 다음에 해볼 것 (예: 다음 판엔 ~해보세요, ~에 신경 써보세요). "
              "판단은 기록(포지션, 데스, 시야, 딜량, 킬 관여 등)에 근거하고, 숫자는 꼭 필요할 때 하나만.\n"
              "좋은 예 (형식만 참고, 내용은 이 경기 기록으로): '○○는 앞라인과 딜을 다 챙겼네요. 오늘 판은 ○○가 끌고 갔습니다.'\n"
              "좋은 예: '○○는 너무 자주 끊겼어요. 다음 판엔 합류 전에 시야부터 잡아보세요.'\n"
              "나쁜 예: '○○는 딜량 44,621과 킬 관여 75%를 기록했습니다.' (숫자 나열), 예시 문장이나 표현을 그대로 베끼기\n"
              "ACE에게 '이겼다'처럼 승리한 듯한 말은 쓰지 말고 '졌지만 ~는 빛났어요'처럼. "
              "한타·갱킹·라인전 같은 장면 묘사는 기록에 없으니 쓰지 마.\n"
              "이모지·줄표 금지, 존댓말(~요, ~습니다)로 친근하게.\n\n"
              + json.dumps(facts, ensure_ascii=False))
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)  # 변수로 잡아둬야 호출 도중 연결이 닫히지 않는다
        best, best_over = None, None
        for _ in range(MAX_TRIES):  # 글자 수를 잘 못 지키니 길면 다시 받는다 (디스코드에서 한 줄로 보이게)
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
        print(f"[lol] gemini evaluation failed: {exc}")
        return {}
