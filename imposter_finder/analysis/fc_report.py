"""FC 온라인 친구전 리포트: 스코어·맞대결·MVP 카드, 스탯 그래프, Gemini 평가."""
from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from imposter_finder.games.fconline import FcMeta, player_image

FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
BG, TRACK = (43, 45, 49), (64, 66, 73)
GREEN, BLUE, GOLD = (87, 242, 135), (88, 101, 242), (240, 178, 50)
LABEL, WHITE = (181, 186, 193), (255, 255, 255)
ACCENT = 0x2ECC71


@dataclass(frozen=True)
class Friend:
    name: str
    discord_user_id: str | None


FORFEIT_WIN, FORFEIT_LOSS = 1, 2  # matchEndType: 0 정상, 1 몰수승, 2 몰수패(중도 이탈)


def goals(side: dict[str, Any]) -> int:
    # 몰수패 쪽은 기록이 비어 있어 표시용 점수를 쓴다
    return side["shoot"].get("goalTotal") or side["shoot"].get("goalTotalDisplay") or 0


def is_forfeit(match: dict[str, Any]) -> bool:
    return any(side["matchDetail"].get("matchEndType") for side in match["matchInfo"])


def build_forfeit_report(match: dict[str, Any], head_to_head: list[dict[str, Any]],
                         friends: dict[str, Friend]) -> dict[str, Any]:
    """중도 이탈 경기는 스탯이 없어 한 줄로만 알린다."""
    # 중도 이탈한 쪽(몰수패)을 오른쪽에 둔다
    left, right = sorted(match["matchInfo"], key=lambda side: side["matchDetail"].get("matchEndType") == FORFEIT_LOSS)
    lf, rf = friends[left["ouid"]], friends[right["ouid"]]
    text = (f"## 👑 {left['nickname']} 몰수승\n"
            f"**{rf.name}** ({right['nickname']}) 중도 이탈\n"
            + _head_to_head_line(head_to_head, left["ouid"], right["ouid"], lf, rf))
    mention_ids = [f.discord_user_id for f in (lf, rf) if f.discord_user_id]
    return {
        "flags": 32768 | 4096,
        "allowed_mentions": {"users": mention_ids},
        "components": [
            {"type": 10, "content": " ".join(f"<@{uid}>" for uid in mention_ids)},
            {"type": 17, "accent_color": 0x99AAB5, "components": [{"type": 10, "content": text}]},
        ],
    }


def build_report(match: dict[str, Any], head_to_head: list[dict[str, Any]], friends: dict[str, Friend],
                 meta: FcMeta, gemini_api_key: str | None, gemini_model: str) -> tuple[dict[str, Any], bytes]:
    """Return (Discord Components V2 payload, stats PNG). friends is keyed by ouid."""
    # 이긴 쪽을 왼쪽에 둔다. 무승부면 받은 순서대로
    left, right = sorted(match["matchInfo"], key=lambda side: side["matchDetail"]["matchResult"] != "승")
    left_won = left["matchDetail"]["matchResult"] == "승"
    lf, rf = friends[left["ouid"]], friends[right["ouid"]]

    score = (f"## {'👑 ' if left_won else ''}{left['nickname']}  "
             f"{goals(left)} : {goals(right)}  {right['nickname']}")
    lines = [score, _head_to_head_line(head_to_head, left["ouid"], right["ouid"], lf, rf), "",
             _mvp_line(match, friends, meta), _culprit_line(match, friends, meta)]

    evaluation = _evaluate(left, right, lf, rf, meta, gemini_api_key, gemini_model)
    mvp_spid = _mvp(match)[1]["spId"]
    header = {"type": 10, "content": "\n".join(lines)}
    image = player_image(mvp_spid)
    top = {"type": 9, "components": [header], "accessory": {"type": 11, "media": {"url": image}}} if image else header
    components = [top, {"type": 12, "items": [{"media": {"url": "attachment://stats.png"}}]}]
    if evaluation:
        components.append({"type": 10, "content": "**평가**\n" + "\n".join(evaluation)})

    mention_ids = [f.discord_user_id for f in (lf, rf) if f.discord_user_id]
    payload = {
        "flags": 32768 | 4096,  # Components V2 + @silent (알림 없이 태그만)
        "allowed_mentions": {"users": mention_ids},
        "components": [
            {"type": 10, "content": " ".join(f"<@{uid}>" for uid in mention_ids)},
            {"type": 17, "accent_color": ACCENT, "components": components},
        ],
        "attachments": [{"id": 0, "filename": "stats.png"}],
    }
    chart = render_stats_chart(left["nickname"], _chart_rows(left), right["nickname"], _chart_rows(right),
                               winner="left" if left_won else "right" if right["matchDetail"]["matchResult"] == "승" else None)
    return payload, chart


def _head_to_head_line(games: list[dict[str, Any]], ouid_l: str, ouid_r: str, lf: Friend, rf: Friend) -> str:
    def record(ouid: str) -> tuple[int, int, int, int, int]:
        w = d = l = gf = ga = 0
        for game in games:
            me = next(side for side in game["matchInfo"] if side["ouid"] == ouid)
            op = next(side for side in game["matchInfo"] if side["ouid"] != ouid)
            result = me["matchDetail"]["matchResult"]
            w, d, l = w + (result == "승"), d + (result == "무"), l + (result == "패")
            gf, ga = gf + goals(me), ga + goals(op)
        return w, d, l, gf, ga

    rec_l, rec_r = record(ouid_l), record(ouid_r)
    # 맞대결에서 앞서는 사람 기준으로 보여준다
    name, (w, d, l, gf, ga) = ((lf.name, rec_l) if (rec_l[0], rec_l[3] - rec_l[4]) >= (rec_r[0], rec_r[3] - rec_r[4])
                               else (rf.name, rec_r))
    return f"최근 {len(games)}경기 맞대결  **{name} {w}승 {d}무 {l}패** · {gf}득 {ga}실"


def _mvp(match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    played = [(side, p) for side in match["matchInfo"] for p in side["player"] if p["status"].get("spRating", 0) > 0]
    return max(played, key=lambda item: item[1]["status"]["spRating"])


SUB_POSITION = 28  # spposition.json: SUB


def _culprit(match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """선발 중 평점이 가장 낮은 선수. 교체로 잠깐 뛴 선수는 평점이 낮게 나오기 쉬워 뺀다."""
    starters = [(side, p) for side in match["matchInfo"] for p in side["player"]
                if p["spPosition"] != SUB_POSITION and p["status"].get("spRating", 0) > 0]
    return min(starters, key=lambda item: item[1]["status"]["spRating"])


def grade_badge(grade: int) -> str:
    icon = "💎" if grade >= 11 else "🥇" if grade >= 8 else "🥈" if grade >= 5 else "🥉" if grade >= 2 else ""
    return f"{icon}+{grade}"


def _player_line(title: str, side: dict[str, Any], player: dict[str, Any], friends: dict[str, Friend],
                 meta: FcMeta) -> str:
    st = player["status"]
    marks = ("⚽" * st.get("goal", 0) + "🅰️" * st.get("assist", 0)
             + "🟨" * st.get("yellowCards", 0) + "🟥" * st.get("redCards", 0))
    name = f"{meta.player_name(player['spId'])} {meta.season_name(player['spId'])} {grade_badge(player['spGrade'])}"
    rating = f"평점 {st['spRating']:.1f}"
    return f"**{title}** {name} ({friends[side['ouid']].name})\n{marks + ' · ' if marks else ''}{rating}"


def _mvp_line(match: dict[str, Any], friends: dict[str, Friend], meta: FcMeta) -> str:
    return _player_line("MVP", *_mvp(match), friends, meta)


def _culprit_line(match: dict[str, Any], friends: dict[str, Friend], meta: FcMeta) -> str:
    return _player_line("범인", *_culprit(match), friends, meta)


def _pct(success: int, attempt: int) -> str:
    return f"{round(success / attempt * 100)}%" if attempt else "-"


def _chart_rows(side: dict[str, Any]) -> list[tuple[str, float, str]]:
    md, sh, ps, df = side["matchDetail"], side["shoot"], side["pass"], side["defence"]
    return [
        ("점유율", md["possession"], f"{md['possession']}%"),
        ("슈팅", sh["shootTotal"], str(sh["shootTotal"])),
        ("유효슈팅", sh["effectiveShootTotal"], str(sh["effectiveShootTotal"])),
        ("패스 성공률", ps["passSuccess"] / max(ps["passTry"], 1) * 100, _pct(ps["passSuccess"], ps["passTry"])),
        ("스루패스 성공", ps["throughPassSuccess"], str(ps["throughPassSuccess"])),
        ("드리블", md["dribble"], str(md["dribble"])),
        ("태클 성공", df["tackleSuccess"], str(df["tackleSuccess"])),
        ("평균 평점", md["averageRating"], f"{md['averageRating']:.1f}"),
    ]


def render_stats_chart(left_name: str, left: list, right_name: str, right: list, winner: str | None) -> bytes:
    """좌우 대칭: 한 줄 막대를 두 사람 비율로 나누고, 숫자는 양 끝 고정 열에 둔다."""
    width, top, row_h, pad = 760, 84, 58, 32
    img = Image.new("RGB", (width, top + row_h * len(left) + 12), BG)
    draw = ImageDraw.Draw(img)
    f_name, f_label, f_value = (ImageFont.truetype(FONT, 26, index=6), ImageFont.truetype(FONT, 22, index=4),
                                ImageFont.truetype(FONT, 22, index=6))

    left_color, right_color = (GOLD if winner == "left" else GREEN), (GOLD if winner == "right" else BLUE)
    draw.text((pad, 26), left_name, font=f_name, fill=left_color)
    draw.text((width - pad, 26), right_name, font=f_name, fill=right_color, anchor="ra")
    if winner == "left":
        draw.text((pad + draw.textlength(left_name, font=f_name) + 10, 30), "승", font=f_label, fill=GOLD)
    elif winner == "right":
        draw.text((width - pad - draw.textlength(right_name, font=f_name) - 10, 30), "승", font=f_label,
                  fill=GOLD, anchor="ra")

    bar_l, bar_r = pad, width - pad
    for i, ((label, lv, lt), (_, rv, rt)) in enumerate(zip(left, right)):
        y = top + i * row_h
        draw.text((bar_l, y), lt, font=f_value, fill=WHITE if lv >= rv else LABEL)
        draw.text((width // 2, y + 2), label, font=f_label, fill=LABEL, anchor="ma")
        draw.text((bar_r, y), rt, font=f_value, fill=WHITE if rv >= lv else LABEL, anchor="ra")
        draw.rounded_rectangle((bar_l, y + 32, bar_r, y + 42), radius=5, fill=TRACK)
        if lv + rv:
            split = bar_l + int((bar_r - bar_l) * lv / (lv + rv))
            if lv:
                draw.rounded_rectangle((bar_l, y + 32, max(split - 2, bar_l + 6), y + 42), radius=5, fill=GREEN)
            if rv:
                draw.rounded_rectangle((min(split + 2, bar_r - 6), y + 32, bar_r, y + 42), radius=5, fill=BLUE)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _evaluate(left: dict, right: dict, lf: Friend, rf: Friend, meta: FcMeta,
              api_key: str | None, model: str) -> list[str]:
    """Gemini 한 줄 평가. 키가 없거나 실패하면 평가 없이 보낸다."""
    if not api_key:
        return []

    def summary(side: dict, friend: Friend) -> dict:
        top = sorted((p for p in side["player"] if p["status"].get("spRating", 0) > 0),
                     key=lambda p: -p["status"]["spRating"])[:3]
        rows = {label: text for label, _, text in _chart_rows(side)}
        return {"이름": friend.name, "결과": side["matchDetail"]["matchResult"], "골": side["shoot"]["goalTotal"], **rows,
                "롱패스": f"{side['pass']['longPassSuccess']}/{side['pass']['longPassTry']}",
                "헤딩슛": side["shoot"]["shootHeading"], "박스 밖 슛": side["shoot"]["shootOutPenalty"],
                "상위 선수": [f"{meta.player_name(p['spId'])} 평점 {p['status']['spRating']:.1f} {p['status']['goal']}골"
                          for p in top]}

    names = [lf.name, rf.name]
    prompt = ("FC 온라인 1:1 친선 경기 기록이야. 친구끼리 보는 디스코드 리포트에 넣을 한 줄 평가를 써줘.\n"
              "두 사람 각각 '{이름}은/는 ...했습니다.' 형식의 40자 이내 한 문장으로, 이번 경기에서 있었던 일만 써. "
              "조언이나 '~해보세요' 같은 제안은 쓰지 마. 가장 결정적인 수치 하나만 근거로 하고, "
              "수치는 기록에 있는 값을 그대로 쓰고 '모두', '전부'처럼 기록과 다른 해석을 붙이지 마. "
              "조사 은/는을 이름 받침에 맞게, 이모지와 줄표(—, -)는 쓰지 마.\n\n"
              + json.dumps([summary(left, lf), summary(right, rf)], ensure_ascii=False))
    schema = {"type": "object", "properties": {n: {"type": "string"} for n in names}, "required": names}
    try:
        from google import genai
        from google.genai import types

        # 임시 객체로 부르면 호출 도중 연결이 닫혀서 변수로 잡아둔다
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=schema))
        result = json.loads(resp.text)
        return [result[n].strip() for n in names if result.get(n)]
    except Exception as exc:  # 한도 초과 등: 평가만 빼고 리포트는 보낸다
        print(f"[fc] gemini evaluation failed: {exc}")
        return []
