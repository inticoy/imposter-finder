"""롤 친구 경기 리포트: 결과 배너 · 선수 · 오브젝트 · 골드 차이 이미지 + Gemini 요약·평가."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from io import BytesIO

from PIL import Image

from imposter_finder.analysis.cards import (BG, BLUE, GOLD, GREEN, LABEL, PAD, RED, TRACK, WHITE, WIDTH,
                                            _download, backdrop, canvas, font, icon, pill, png, shade, split_bar,
                                            white_icon)
from imposter_finder.games.lol import ARENA_QUEUES, CDRAGON_ICONS, QUEUE_NAMES, DDragon

ACCENT_WIN, ACCENT_LOSE = 0x2ECC71, 0xED4245
OBJECTIVES = [("킬", "champion", "kills"), ("포탑", "tower", "tower-100"), ("억제기", "inhibitor", "inhibitor-100"),
              ("드래곤", "dragon", "dragon-100"), ("바론", "baron", "baron-100"), ("전령", "riftHerald", "herald-100"),
              ("공허 유충", "horde", "right_icons_grub")]


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
    color = GREEN if won else RED
    cx = WIDTH // 2
    dr.rectangle((0, 0, WIDTH, 5), fill=color)

    # 가운데: 승패 · 모드
    word = "승리" if won else "패배"
    dr.text((cx, 38), word, font=font(64, 6), fill=color, anchor="ma")
    dr.text((cx, 120), f"{mode} · {minutes}분", font=font(22, 4), fill=WHITE, anchor="ma")

    def side(info: dict, align: str) -> None:
        sign = -1 if align == "left" else 1
        edge = PAD if align == "left" else WIDTH - PAD
        anchor = "la" if align == "left" else "ra"
        dr.text((edge, 20), info["label"], font=font(18), fill=GREEN if align == "left" else RED, anchor=anchor)
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
                dr.rounded_rectangle((x - 2, 118, x + size + 1, 120 + size + 1), radius=10, outline=GREEN, width=2)

    side(left, "left")
    if right:
        side(right, "right")
    return png(img)


def render_players(columns: list[dict], rows: list[tuple]) -> bytes:
    head_h, row_h, label_w = 196, 52, 130
    height = head_h + row_h * len(rows) + 10
    img, dr = canvas(height)
    # 칸은 라벨 뒤부터 그림 오른쪽 끝까지 나눈다 (오른쪽 여백 없음)
    left = PAD + label_w
    col_w = (WIDTH - left) / len(columns)
    for c, col in enumerate(columns):  # 칸마다 챔피언 로딩 원화를 어둡게
        x0 = round(left + c * col_w)
        x1 = WIDTH if c == len(columns) - 1 else round(left + (c + 1) * col_w) - 4
        backdrop(img, col.get("art_url"), (x0, 0, x1, height), dim=0.8, focus_y=0.1)
    for c, col in enumerate(columns):
        cx = int(left + c * col_w + col_w / 2)
        color = GOLD if col["badge"] == "MVP" else RED if col["badge"] == "범인" else WHITE
        ic = icon(col["icon_url"], 56)
        if ic:
            img.paste(ic, (cx - 28, 16), ic)
        if col["badge"]:
            dr.rounded_rectangle((cx - 31, 13, cx + 31, 75), radius=14, outline=color, width=3)
            pill(dr, cx, 114, col["badge"], color)
        dr.text((cx, 84), col["name"], font=font(24, 6), fill=color, anchor="ma")
        dr.text((cx, 146), col["champion"], font=font(18), fill=LABEL, anchor="ma")
        dr.text((cx, 168), col["kda"], font=font(20, 6), fill=WHITE, anchor="ma")
    for r, (label, values, fmt) in enumerate(rows):
        y = head_h + r * row_h
        dr.text((PAD, y + 6), label, font=font(20), fill=LABEL)
        hi = max(values) or 1
        for c, v in enumerate(values):
            x = int(left + c * col_w) + 12
            bw = int(col_w) - 26
            dr.rounded_rectangle((x, y + 30, x + bw, y + 39), radius=4, fill=TRACK)
            dr.rounded_rectangle((x, y + 30, x + max(int(bw * v / hi), 6), y + 39), radius=4,
                                 fill=GOLD if v == hi else BLUE)
            dr.text((x, y + 4), fmt(v), font=font(20, 6), fill=WHITE if v == hi else LABEL)
    return png(img)


def render_objectives(objectives: list[tuple]) -> bytes:
    label_w = 130
    img, dr = canvas(150)
    dr.text((PAD, 64), "오브젝트", font=font(20), fill=LABEL)
    rw = dr.textlength("상대", font=font(16))
    dr.text((WIDTH - PAD, 12), "상대", font=font(16), fill=RED, anchor="ra")
    dr.text((WIDTH - PAD - rw - 6, 12), ":", font=font(16), fill=LABEL, anchor="ra")
    dr.text((WIDTH - PAD - rw - 18, 12), "우리 팀", font=font(16), fill=GREEN, anchor="ra")
    cell = (WIDTH - PAD * 2 - label_w) // len(objectives)
    for n, (label, ours, theirs, icon_url) in enumerate(objectives):
        cx = PAD + label_w + n * cell + cell // 2
        ic = white_icon(icon_url, 34)
        if ic:
            img.paste(ic, (cx - 17, 34), ic)
        dr.text((cx, 74), label, font=font(15), fill=LABEL, anchor="ma")
        split_bar(dr, cx - cell // 2 + 14, cx + cell // 2 - 14, 100, ours, theirs, height=8)
        dr.text((cx - 6, 114), str(ours), font=font(22, 6), fill=GREEN if ours > theirs else LABEL, anchor="ra")
        dr.text((cx, 114), ":", font=font(22, 6), fill=LABEL, anchor="ma")
        dr.text((cx + 6, 114), str(theirs), font=font(22, 6), fill=RED if theirs > ours else LABEL, anchor="la")
    return png(img)


def render_gold(diffs: list[int]) -> bytes:
    height = 340
    img, dr = canvas(height)
    dr.text((PAD, 14), "골드 차이", font=font(20), fill=LABEL)
    dr.text((WIDTH - PAD, 16), "▲ 우리 팀 우세   ▼ 상대 우세", font=font(16), fill=LABEL, anchor="ra")
    gx0, gx1, gy0, gy1 = PAD + 70, WIDTH - PAD - 16, 56, height - 56
    mid, half = (gy0 + gy1) // 2, (gy1 - gy0) // 2
    span = max(max(abs(d) for d in diffs), 1000)
    xy = lambda i, d: (gx0 + (gx1 - gx0) * i / max(len(diffs) - 1, 1), mid - half * d / span)
    for frac in (0.5, 1.0):
        for sign in (1, -1):
            dr.line((gx0, mid - sign * half * frac, gx1, mid - sign * half * frac), fill=(52, 54, 60), width=1)
    dr.line((gx0, mid, gx1, mid), fill=TRACK, width=2)
    for i in range(len(diffs) - 1):
        (x1, y1), (x2, y2) = xy(i, diffs[i]), xy(i + 1, diffs[i + 1])
        color = GREEN if diffs[i] + diffs[i + 1] >= 0 else RED
        dr.polygon([(x1, mid), (x1, y1), (x2, y2), (x2, mid)], fill=shade(color))
        dr.line((x1, y1, x2, y2), fill=color, width=3)
    dr.text((gx0 - 10, mid - half), f"+{span / 1000:.1f}k", font=font(16), fill=GREEN, anchor="rm")
    dr.text((gx0 - 10, mid), "0", font=font(16), fill=LABEL, anchor="rm")
    dr.text((gx0 - 10, mid + half), f"-{span / 1000:.1f}k", font=font(16), fill=RED, anchor="rm")
    for minute in range(0, len(diffs), 5):
        dr.text((xy(minute, 0)[0], gy1 + 12), f"{minute}분", font=font(16), fill=LABEL, anchor="ma")
    return png(img)


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
    stats = _team_scores(team, minutes)
    mvp = max(mine, key=lambda p: stats[p["puuid"]]["score"])
    culprit = min(mine, key=lambda p: stats[p["puuid"]]["score"]) if len(mine) > 1 else None
    name = lambda p: friends[p["puuid"]].name

    cols = sorted(mine, key=lambda p: -stats[p["puuid"]]["score"])
    columns = [{"name": name(p), "champion": ddragon.champion_name(p["championName"]),
                "icon_url": ddragon.champion_icon(p["championName"]),
                "art_url": f"https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{p['championName']}_0.jpg",
                "kda": f"{p['kills']}/{p['deaths']}/{p['assists']}",
                "badge": "MVP" if p is mvp else "범인" if p is culprit else None} for p in cols]
    rows = [("딜량", [p["totalDamageDealtToChampions"] for p in cols], lambda v: f"{v / 1000:.1f}k"),
            ("받은 피해", [p["totalDamageTaken"] for p in cols], lambda v: f"{v / 1000:.1f}k"),
            ("KDA", [stats[p["puuid"]]["kda"] for p in cols], lambda v: f"{v:.1f}"),
            ("킬 관여", [stats[p["puuid"]]["kp"] * 100 for p in cols], lambda v: f"{v:.0f}%"),
            ("분당 CS", [stats[p["puuid"]]["cs"] for p in cols], lambda v: f"{v:.1f}"),
            ("시야 점수", [p["visionScore"] for p in cols], lambda v: f"{v}")]
    objectives_by_team = {t["teamId"]: t["objectives"] for t in info["teams"]}
    us, them = objectives_by_team[team_id], objectives_by_team[300 - team_id]
    objectives = [(label, us.get(key, {}).get("kills", 0), them.get(key, {}).get("kills", 0), f"{CDRAGON_ICONS}/{ic}.png")
                  for label, key, ic in OBJECTIVES]

    queue = QUEUE_NAMES.get(info["queueId"], info.get("gameMode", ""))
    enemy = [p for p in info["participants"] if p["teamId"] != team_id]
    friend_ids = {p["puuid"] for p in mine}
    side_info = lambda label, members: {
        "label": label, "kills": sum(p["kills"] for p in members), "gold": sum(p["goldEarned"] for p in members),
        "icons": [(ddragon.champion_icon(p["championName"]), p["puuid"] in friend_ids) for p in members]}
    splash = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{mvp['championName']}_0.jpg"
    files = [("result.png", render_banner(won, queue, int(minutes), splash,
                                          side_info(f"우리 팀 · 친구 {len(mine)}명", team), side_info("상대 팀", enemy))),
             ("players.png", render_players(columns, rows)),
             ("objectives.png", render_objectives(objectives))]
    diffs = _gold_diffs(info, timeline, team_id) if timeline else []
    if diffs:
        files.append(("gold.png", render_gold(diffs)))

    facts = {"결과": "승리" if won else "패배", "오브젝트(우리:상대)": {o[0]: f"{o[1]}:{o[2]}" for o in objectives},
             "골드 흐름(우리 팀 기준)": _gold_story(diffs) if diffs else "없음",
             "선수": [_player_fact(p, name(p), "MVP" if p is mvp else "범인", stats, ddragon) for p in (mvp, culprit) if p]}
    ai = _evaluate(facts, [name(p) for p in (mvp, culprit) if p], gemini_api_key, gemini_model)
    return _payload(mine, friends, won, files, ai), files


def _player_fact(p: dict, name: str, role: str, stats: dict, ddragon: DDragon) -> dict:
    return {"이름": name, "역할": role, "챔피언": ddragon.champion_name(p["championName"]),
            "KDA": f"{p['kills']}/{p['deaths']}/{p['assists']}", "딜량": p["totalDamageDealtToChampions"],
            "킬 관여": f"{stats[p['puuid']]['kp'] * 100:.0f}%", "시야 점수": p["visionScore"]}


def _build_arena(match: dict, friends: dict[str, Friend], ddragon: DDragon, key: str | None, model: str):
    """아레나: 2인 1조 순위전이라 오브젝트·골드 없이 순위와 딜 위주."""
    info = match["info"]
    mine = [p for p in info["participants"] if p["puuid"] in friends]
    place = lambda p: p.get("placement") or p.get("subteamPlacement") or 8
    ranked = sorted(mine, key=lambda p: (place(p), -p["totalDamageDealtToChampions"]))
    mvp, culprit = ranked[0], (ranked[-1] if len(ranked) > 1 else None)
    name = lambda p: friends[p["puuid"]].name
    columns = [{"name": name(p), "champion": ddragon.champion_name(p["championName"]),
                "icon_url": ddragon.champion_icon(p["championName"]),
                "kda": f"{p['kills']}/{p['deaths']}/{p['assists']}",
                "badge": "MVP" if p is mvp else "범인" if p is culprit else None} for p in ranked]
    rows = [("순위", [9 - place(p) for p in ranked], lambda v: f"{9 - v}등"),
            ("딜량", [p["totalDamageDealtToChampions"] for p in ranked], lambda v: f"{v / 1000:.1f}k"),
            ("받은 피해", [p["totalDamageTaken"] for p in ranked], lambda v: f"{v / 1000:.1f}k"),
            ("킬", [p["kills"] for p in ranked], lambda v: f"{v}")]
    best = place(mvp)
    splash = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{mvp['championName']}_0.jpg"
    files = [("result.png", render_banner(best <= 4, f"아레나 최고 {best}등", int(info["gameDuration"] / 60), splash,
                                          {"label": f"친구 {len(mine)}명", "kills": None,
                                           "icons": [(c["icon_url"], True) for c in columns]}, None)),
             ("players.png", render_players(columns, rows))]
    facts = {"모드": "아레나 (2인 1조, 순위전)", "선수": [{"이름": name(p), "역할": "MVP" if p is mvp else "범인",
                                                     "순위": place(p), "딜량": p["totalDamageDealtToChampions"],
                                                     "KDA": f"{p['kills']}/{p['deaths']}/{p['assists']}"}
                                                    for p in (mvp, culprit) if p]}
    ai = _evaluate(facts, [name(p) for p in (mvp, culprit) if p], key, model)
    return _payload(mine, friends, best <= 4, files, ai), files


def _payload(mine: list, friends: dict, won: bool, files: list[tuple], ai: dict) -> dict:
    ids = list(dict.fromkeys(friends[p["puuid"]].discord_user_id for p in mine if friends[p["puuid"]].discord_user_id))
    components: list[dict] = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, _ in files]
    # 총평과 평가는 이미지 아래에 모은다
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
                       {"type": 17, "accent_color": ACCENT_WIN if won else ACCENT_LOSE, "components": components}],
    }


def _evaluate(facts: dict, names: list[str], api_key: str | None, model: str) -> dict:
    """Gemini: 경기 흐름 한 문장 + MVP·범인 한 문장씩. 실패하면 빈 값."""
    if not api_key:
        return {}
    keys = ["summary"] + names
    prompt = ("리그 오브 레전드 경기 기록이야. 친구끼리 보는 디스코드 리포트에 넣을 평가를 써줘.\n"
              "summary: 경기 흐름 한 문장, 50자 이내. 골드 흐름 문장과 오브젝트를 그대로 근거로, 기록에 없는 흐름을 지어내지 마.\n"
              "각 선수 이름: '{이름}은/는 ...했습니다.' 45자 이내 한 문장, 결정적인 수치 하나와 그 의미.\n"
              "수치는 기록 값 그대로, 조언·이모지·줄표 금지, 조사 은/는을 이름 받침에 맞게.\n\n"
              + json.dumps(facts, ensure_ascii=False))
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)  # 변수로 잡아둬야 호출 도중 연결이 닫히지 않는다
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema={
                "type": "object", "properties": {k: {"type": "string"} for k in keys}, "required": keys}))
        result = json.loads(resp.text)
        return {"summary": result.get("summary", "").strip(), "lines": [result[n].strip() for n in names if result.get(n)]}
    except Exception as exc:
        print(f"[lol] gemini evaluation failed: {exc}")
        return {}
