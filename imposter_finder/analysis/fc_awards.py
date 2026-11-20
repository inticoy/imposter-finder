"""FC 친구전 선수 시상식과 통산 기록실."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction

from PIL import Image, ImageDraw

from imposter_finder.analysis.cards import PAD, WIDTH, font, icon, png
from imposter_finder.analysis.fc_report import BG, LABEL, SHOT_GOAL, WHITE, Friend, _face, goals
from imposter_finder.games.fconline import KST, FcMeta, face_image

GOLD = (239, 194, 101)


@dataclass
class Player:
    spid: int
    owner: str
    games: int = 0
    goals: int = 0
    assists: int = 0
    ratings: list[float] = field(default_factory=list)
    keeper_ratings: list[float] = field(default_factory=list)


def player_totals(matches: list[dict], friends: dict[str, Friend]) -> list[Player]:
    """경기별 실출전 기록을 구단주와 시즌 카드 단위로 합친다."""
    totals: dict[tuple[str, int], Player] = {}
    for match in matches:
        for side in match["matchInfo"]:
            ouid = side["ouid"]
            owner = friends[ouid].name if ouid in friends else side.get("nickname", "")
            for p in side["player"]:
                status = p.get("status") or {}
                rating = status.get("spRating") or 0
                scored, assisted = status.get("goal") or 0, status.get("assist") or 0
                if rating <= 0 and not scored and not assisted:
                    continue
                entry = totals.setdefault((ouid, p["spId"]), Player(p["spId"], owner))
                entry.games += 1
                entry.goals += scored
                entry.assists += assisted
                if rating > 0:
                    entry.ratings.append(rating)
                    if p.get("spPosition") == 0:
                        entry.keeper_ratings.append(rating)
    return list(totals.values())


def top_three(scored: list[tuple[int | float | Fraction, Player]]) -> list[tuple[int, int | float | Fraction, Player]]:
    """동점은 공동 순위. 표시할 세 자리 뒤의 동률도 반환한다."""
    rows = []
    previous = None
    rank = 0
    for index, (value, p) in enumerate(sorted(scored, key=lambda item: (-item[0], item[1].owner, item[1].spid)), 1):
        if value != previous:
            rank = index
            previous = value
        if rank > 3:
            break
        rows.append((rank, value, p))
    return rows


def _award_groups(players: list[Player], recent: bool) -> list[tuple[str, str, str, list]]:
    out = []
    if recent:
        for title, attr in (("평점 BEST", "ratings"), ("황금장갑 · GK 평점", "keeper_ratings")):
            scored = [(round(sum(ratings) / len(ratings), 2), p) for p in players
                      if len(ratings := getattr(p, attr)) >= 2]
            out.append((title, attr, "", top_three(scored)))
    for title, attr, unit in (("득점", "goals", "골"), ("도움", "assists", "도움")):
        scored = []
        for p in players:
            count = getattr(p, attr)
            if count and (not recent or p.games >= 2):
                scored.append((Fraction(count, p.games) if recent else count, p))
        out.append((f"{title} 효율" if recent else f"통산 {title}왕", attr, unit, top_three(scored)))
    return out


def _fit(draw: ImageDraw.ImageDraw, text: str, face, width: int) -> str:
    while len(text) > 1 and draw.textlength(text, font=face) > width:
        text = text[:-2] + "…"
    return text


def _faces(spids: set[int], size: int) -> dict[int, Image.Image | None]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        ordered = sorted(spids)
        return dict(zip(ordered, pool.map(lambda sid: _face(face_image(sid), size, None), ordered)))


def _award_card(groups: list[tuple[str, str, str, list]], meta: FcMeta, faces: dict, *,
                title: str, subtitle: str, recent: bool) -> bytes:
    row_height = 302
    img = Image.new("RGB", (WIDTH, 103 + ((len(groups) + 1) // 2) * row_height), BG)
    d = ImageDraw.Draw(img)
    d.text((PAD, 24), title, font=font(33, 7), fill=WHITE)
    d.text((WIDTH - PAD, 50), subtitle, font=font(19, 6), fill=LABEL, anchor="ra")
    for j, (label, metric, unit, entries) in enumerate(groups):
        x, y = PAD + j % 2 * 491, 95 + j // 2 * row_height
        end = x + 444
        d.text((x, y), label, font=font(25, 7), fill=WHITE)
        if not entries:
            d.text((x, y + 49), "집계할 기록 없음", font=font(19, 6), fill=LABEL)
            continue
        tied = Counter(rank for rank, _, _ in entries)
        for i, (rank, value, player) in enumerate(entries[:3]):
            yy = y + 41 + i * 74
            strong = GOLD if rank == 1 else WHITE
            d.text((x + 12, yy + 26), str(rank), font=font(25, 7), fill=strong, anchor="mm")
            if tied[rank] > 1:
                d.text((x + 12, yy + 48), "공동", font=font(12, 6), fill=LABEL, anchor="mm")
            face = faces[player.spid]
            if face:
                img.paste(face, (x + 33, yy), face)
            title_font = font(24, 7)
            name = _fit(d, meta.player_name(player.spid), title_font, 230)
            d.text((x + 105, yy + 1), name, font=title_font, fill=WHITE)
            sx = x + 105
            season = meta.season_name(player.spid) or str(player.spid // 1_000_000)
            logo = icon(meta.season_image(player.spid), 22, radius=2)
            if logo:
                img.paste(logo, (sx, yy + 34), logo)
                sx += 24
            detail_font = font(16, 6)
            owner = _fit(d, f"{season}  ·  {player.owner}", detail_font, end - sx - 101)
            d.text((sx, yy + 36), owner, font=detail_font, fill=LABEL)
            score = f"{float(value):.2f}" if recent else f"{value}{unit}"
            count = f"{player.games}경기" if unit else f"{len(getattr(player, metric))}경기"
            if recent and unit:
                count = f"{getattr(player, metric)}{unit} / {count}"
            d.text((end, yy + 19), score, font=font(28, 7), fill=strong, anchor="rm")
            d.text((end, yy + 45), count, font=detail_font, fill=LABEL, anchor="rm")
        if len(entries) > 3:
            d.text((x + 105, y + 265), f"외 {len(entries) - 3}명 공동 {entries[2][0]}위", font=font(15, 6), fill=LABEL)
    return png(img)


def render_awards(history: list[dict], window: list[dict], friends: dict[str, Friend], meta: FcMeta) -> list[tuple[str, bytes]]:
    recent = _award_groups(player_totals(window, friends), recent=True)
    career = _award_groups(player_totals(history, friends), recent=False)
    faces = _faces({p.spid for group in recent + career for _, _, p in group[3][:3]}, 56)
    return [
        ("awards.png", _award_card(recent, meta, faces, title=f"이번 {len(window)}경기",
                                  subtitle="2경기 이상 · 평균 평점 및 경기당 기록", recent=True)),
        ("career.png", _award_card(career, meta, faces, title="친구전 통산",
                                  subtitle=f"저장된 정상 경기 {len(history)}판 · 누적 기록", recent=False)),
    ]


@dataclass
class Record:
    value: int
    name: str
    detail: str
    date: str
    spid: int | None = None


RECORD_GROUPS = [
    ("경기", (("최다 득점 경기", "골"), ("최대 골 차 승리", "골 차"))),
    ("선수", (("선수 한 경기 최다 골", "골"), ("선수 한 경기 최다 도움", "도움"))),
    ("승부와 수비", (("최대 역전승", "골 차"), ("최장 무실점 연속", "경기"))),
]


def _match_date(match: dict) -> str:
    dt = datetime.fromisoformat(match["matchDate"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%y.%m.%d")


def _comeback(sides: list[dict], scores: list[int]) -> tuple[int, tuple[int, int]]:
    events = []
    for j, side in enumerate(sides):
        for shot in side.get("shootDetail", ()):
            t = shot.get("goalTime")
            if shot.get("result") == SHOT_GOAL and isinstance(t, int) and 0 <= t < 4 * (1 << 24):
                events.append((t, j))
    observed = Counter(j for _, j in events)
    if [observed[0], observed[1]] != scores or len({t for t, _ in events}) != len(events):
        return 0, (0, 0)
    winner = 0 if scores[0] > scores[1] else 1
    running = [0, 0]
    deficit, score_at = 0, (0, 0)
    for _, j in sorted(events):
        running[j] += 1
        gap = running[1 - winner] - running[winner]
        if gap > deficit:
            deficit, score_at = gap, (running[0], running[1])
    return deficit, score_at


def league_records(history: list[dict], friends: dict[str, Friend]) -> dict[str, tuple[Record, int]]:
    records: dict[str, tuple[Record, int]] = {}
    streaks: dict[str, tuple[int, str]] = {}

    def consider(key: str, record: Record) -> None:
        if key not in records or record.value > records[key][0].value:
            records[key] = (record, 1)
        elif record.value == records[key][0].value:
            records[key] = (records[key][0], records[key][1] + 1)

    for match in history:
        sides = match["matchInfo"]
        scores = [int(goals(s)) for s in sides]
        names = [friends[s["ouid"]].name if s["ouid"] in friends else s.get("nickname", "") for s in sides]
        date = _match_date(match)
        scoreline = f"{names[0]} {scores[0]}:{scores[1]} {names[1]}"
        consider("최다 득점 경기", Record(sum(scores), scoreline, "양 팀 득점 합계", date))
        if scores[0] != scores[1]:
            consider("최대 골 차 승리", Record(abs(scores[0] - scores[1]), scoreline, "최종 스코어 기준", date))
            deficit, low = _comeback(sides, scores)
            if deficit:
                consider("최대 역전승", Record(deficit, scoreline, f"{low[0]}:{low[1]} 열세에서 역전", date))
        for j, side in enumerate(sides):
            key = side["ouid"]
            if scores[1 - j] == 0:
                count, start = streaks.get(key, (0, date))
                streaks[key] = (count + 1, start)
                span = date if start == date else f"{start}–{date}"
                consider("최장 무실점 연속", Record(count + 1, names[j], "해당 감독의 연속 출전 경기", span))
            else:
                streaks.pop(key, None)
            for player in side["player"]:
                status = player.get("status") or {}
                for key, metric in (("선수 한 경기 최다 골", "goal"), ("선수 한 경기 최다 도움", "assist")):
                    value = status.get(metric) or 0
                    if value > 0:
                        consider(key, Record(value, names[j], scoreline, date, player["spId"]))
    return records


def render_records(history: list[dict], friends: dict[str, Friend], meta: FcMeta) -> bytes:
    records = league_records(history, friends)
    faces = _faces({r.spid for r, _ in records.values() if r.spid is not None}, 70)
    img = Image.new("RGB", (WIDTH, 752), BG)
    d = ImageDraw.Draw(img)
    d.text((PAD, 29), "친구 리그 기록실", font=font(41, 7), fill=WHITE)
    d.text((WIDTH - PAD, 60), f"통산 정상 경기 {len(history)}판", font=font(20, 6), fill=LABEL, anchor="ra")
    for k, (group, entries) in enumerate(RECORD_GROUPS):
        y = 111 + 205 * k
        d.text((PAD, y), group, font=font(26, 7), fill=((178, 192, 212), GOLD, (162, 197, 232))[k])
        for j, (title, unit) in enumerate(entries):
            x = PAD + j * 491
            d.text((x, y + 49), title, font=font(20, 7), fill=WHITE)
            if title not in records:
                d.text((x, y + 91), "기록 없음", font=font(18, 6), fill=LABEL)
                continue
            record, ties = records[title]
            d.text((x + 444, y + 89), f"{record.value}{unit}", font=font(33, 7), fill=GOLD, anchor="ra")
            tx = x
            name, detail = record.name, record.detail
            sid = record.spid
            if sid is not None:
                face = faces[sid]
                if face:
                    img.paste(face, (x, y + 80), face)
                    tx += 77
                name = meta.player_name(sid)
                detail = " · ".join(filter(None, (meta.season_name(sid) or str(sid // 1_000_000), record.name)))
            face = font(22, 7)
            d.text((tx, y + 88), _fit(d, name, face, x + 335 - tx), font=face, fill=WHITE)
            face = font(16, 6)
            d.text((tx, y + 121), _fit(d, detail, face, x + 420 - tx), font=face, fill=LABEL)
            more = f" · 동일 기록 {ties}건" if ties > 1 else ""
            d.text((tx, y + 148), record.date + more, font=font(15, 6), fill=LABEL)
    return png(img)
