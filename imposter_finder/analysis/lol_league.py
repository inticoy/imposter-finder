"""자유랭크 친구전 5경기 리포트와 통산 기록."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from io import BytesIO
from itertools import combinations

from PIL import Image, ImageChops, ImageDraw, ImageOps

from imposter_finder.analysis.cards import WIDTH, _download, font, icon, png, split_bar
from imposter_finder.analysis.lol_report import (
    ACCENT_WIN, REPORT_CORAL, REPORT_GOLD, REPORT_TEAM_BLUE, REPORT_VICTORY, Friend,
)
from imposter_finder.games.lol import DDragon, DDRAGON

FLEX_QUEUE, LEAGUE_EVERY, BEST_MIN_GAMES = 440, 5, 5
SURFACE, TEXT, MUTED, DIVIDER = "#111a27", "#f3f6fb", "#acbbcc", "#2b3b4e"
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Player:
    name: str
    champion: str
    kills: int
    deaths: int
    assists: int
    role: str
    vision: int
    multikill: int


@dataclass(frozen=True)
class Game:
    id: str
    created: int
    duration: int
    won: bool
    kills: int
    enemy_kills: int
    players: tuple[Player, ...]

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.created / 1000, KST)


def friend_game(match: dict, friends: dict[str, Friend]) -> Game | None:
    info = match["info"]
    duration = info.get("gameDuration", 0)
    if info["queueId"] != FLEX_QUEUE or duration < 300:
        return None
    participants = info["participants"]
    if any(p.get("gameEndedInEarlySurrender") for p in participants):
        return None
    teams = defaultdict(list)
    for p in participants:
        if p["puuid"] in friends:
            teams[p["teamId"]].append(p)
    qualified = [team for team, ps in teams.items() if len({friends[p["puuid"]].name for p in ps}) >= 2]
    if len(qualified) != 1:
        return None
    team = qualified[0]
    ps = teams[team]
    if len(ps) != len({friends[p["puuid"]].name for p in ps}):
        return None
    kills = Counter()
    for p in participants:
        kills[p["teamId"]] += p["kills"]
    roster = tuple(Player(friends[p["puuid"]].name, p["championName"], p["kills"], p["deaths"], p["assists"],
                          p.get("teamPosition", ""), p.get("visionScore", 0),
                          max((n for key, n in (("tripleKills", 3), ("quadraKills", 4), ("pentaKills", 5))
                               if p.get(key)), default=0)) for p in ps)
    return Game(match["metadata"]["matchId"], info["gameCreation"], duration, ps[0]["win"],
                kills[team], sum(kills.values()) - kills[team], roster)


def _win_rate(games: list[Game]) -> Fraction:
    return Fraction(sum(g.won for g in games), len(games))


def _participation(games: list[Game]) -> dict[str, dict[str, list[Game]]]:
    picks = defaultdict(lambda: defaultdict(list))
    for game in games:
        for player in game.players:
            picks[player.name][player.champion].append(game)
    return picks


def _duos(games: list[Game]) -> dict[tuple[str, str], list[tuple[Game, tuple[str, str]]]]:
    pairs = defaultdict(list)
    for game in games:
        for a, b in combinations(sorted(game.players, key=lambda p: p.name), 2):
            pairs[a.name, b.name].append((game, (a.champion, b.champion)))
    return pairs


@dataclass(frozen=True)
class Record:
    label: str
    value: int | Fraction
    display: str
    game: Game
    player: Player | None = None


def _duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02}"


def _records(history: list[Game]) -> list[Record]:
    longest = max(history, key=lambda g: g.duration)
    records = [Record("최장 경기", longest.duration, _duration(longest.duration), longest)]
    wins = [g for g in history if g.won and g.kills > g.enemy_kills]
    if wins:
        margin = max(wins, key=lambda g: g.kills - g.enemy_kills)
        delta = margin.kills - margin.enemy_kills
        records.append(Record("최대 킬 격차 승리", delta, f"{delta:+}킬", margin))
    players = [(g, p) for g in history for p in g.players]
    g, p = max(players, key=lambda gp: gp[1].kills)
    records.append(Record("한 경기 최다 킬", p.kills, f"{p.kills}킬", g, p))
    g, p = max(players, key=lambda gp: gp[1].assists)
    records.append(Record("한 경기 최다 어시스트", p.assists, f"{p.assists}회", g, p))
    supports = [(g, p) for g, p in players if p.role == "UTILITY" and g.duration >= 900]
    if supports:
        g, p = max(supports, key=lambda gp: Fraction(gp[1].vision * 60, gp[0].duration))
        rate = Fraction(p.vision * 60, g.duration)
        records.append(Record("서포터 최고 분당 시야", rate, f"{float(rate):.1f}", g, p))
    return records


def _record_book(history: list[Game], window: list[Game]) -> list[tuple[Record, str]]:
    current = {r.label: r for r in _records(window)}
    ids = {g.id for g in window}
    previous = [g for g in history if g.id not in ids]
    book = {r.label: (r, "") for r in _records(previous)} if previous else {}
    updated = False
    for label, record in current.items():
        prior = book.get(label)
        if prior and record.value < prior[0].value:
            continue
        badge = "타이기록" if prior and record.value == prior[0].value else "신기록"
        book[label] = record, badge
        updated = True
    order = ("최장 경기", "최대 킬 격차 승리", "한 경기 최다 킬", "한 경기 최다 어시스트", "서포터 최고 분당 시야")
    return [book[key] for key in order if key in book] if updated else []


class Card:
    def __init__(self, title: str, subtitle: str, height: int, dd: DDragon, *, art: str = "",
                 accent=REPORT_VICTORY) -> None:
        self.image = Image.new("RGB", (WIDTH, height), SURFACE)
        self.draw = ImageDraw.Draw(self.image)
        self.dd = dd
        if art:
            try:
                asset = _download(f"{DDRAGON}/cdn/img/champion/splash/{art}_0.jpg")
                splash = ImageOps.fit(Image.open(BytesIO(asset)).convert("RGB"), (560, 208), centering=(0.5, 0.2))
            except (OSError, ValueError):
                splash = None
            if splash:
                ramp = Image.linear_gradient("L")
                horizontal = ramp.rotate(90).resize(splash.size)
                vertical = ImageOps.invert(ramp).resize(splash.size)
                fade = ImageChops.multiply(horizontal, vertical).point(lambda value: round(value * 0.5))
                self.image.paste(splash, (WIDTH - splash.width, 0), fade)
        self.draw.rounded_rectangle((1, 1, WIDTH - 2, height - 2), radius=17, outline=DIVIDER, width=2)
        self.draw.rounded_rectangle((2, 14, 6, height - 15), radius=2, fill=accent)
        self.text(29, 21, title, 32)
        self.text(WIDTH - 28, 31, subtitle, 18, MUTED, anchor="rt", max_width=425)

    def text(self, x, y, value, size=21, color=TEXT, *, weight=7, anchor="lt", max_width=None):
        face = font(size, weight)
        value = str(value)
        if max_width and face.getlength(value) > max_width:
            while value and face.getlength(value + "…") > max_width:
                value = value[:-1]
            value += "…"
        self.draw.text((x, y), value, font=face, fill=color, anchor=anchor)

    def divider(self, y):
        self.draw.line((28, y, WIDTH - 28, y), fill=DIVIDER)

    def tag(self, x, y, label, color=REPORT_GOLD):
        face = font(15, 7)
        width = round(face.getlength(label)) + 20
        self.draw.rounded_rectangle((x, y, x + width, y + 23), radius=12, fill=color)
        self.text(x + width / 2, y + 12, label, 15, SURFACE, anchor="mm")
        return width

    def champions(self, x, y, champions, size=35):
        for champion in champions:
            portrait = icon(self.dd.champion_icon(champion), size, radius=6)
            if portrait:
                self.image.paste(portrait, (x, y), portrait)
            else:
                self.draw.rounded_rectangle((x, y, x + size - 1, y + size - 1), radius=6, fill=DIVIDER)
                self.text(x + size / 2, y + size / 2, self.dd.champion_name(champion)[:1], 17, anchor="mm")
            x += size + 5

    def results(self, x, y, games, size=20):
        wins = sum(g.won for g in games)
        left, right = f"{wins}승", f"{len(games) - wins}패"
        self.text(x, y, left, size, REPORT_VICTORY)
        self.text(x + round(font(size, 7).getlength(left)) + 9, y, right, size, REPORT_CORAL)

    def bar(self, left, right, y, games):
        wins = sum(g.won for g in games)
        split_bar(self.image, left, right, y, wins, len(games) - wins,
                  left_color=REPORT_VICTORY, right_color=REPORT_CORAL, tick=False)


def _match_card(window: list[Game], dd: DDragon) -> bytes:
    g, highlight = max(((g, p) for g in window for p in g.players),
                       key=lambda gp: (gp[1].multikill, gp[0].won, gp[1].kills))
    card = Card("5경기 전적", f"자유랭크 · {window[0].date:%m.%d}~{window[-1].date:%m.%d}", 470, dd,
                art=highlight.champion)
    card.results(29, 73, window, 38)
    card.bar(211, 443, 89, window)
    for i, game in enumerate(window):
        y = 127 + i * 53
        card.divider(y - 9)
        card.champions(29, y, (p.champion for p in game.players))
        card.text(244, y + 9, " · ".join(p.name for p in game.players), 19, max_width=365)
        color = REPORT_VICTORY if game.won else REPORT_CORAL
        card.text(729, y + 7, f"{game.kills} : {game.enemy_kills}", 23, color, anchor="rt")
        card.tag(742, y + 5, "승리" if game.won else "패배", color)
        card.text(970, y + 1, f"{game.date:%m.%d %H:%M}", 17, anchor="rt")
        card.text(970, y + 23, _duration(game.duration), 15, MUTED, anchor="rt")
    y = 399
    card.divider(y - 10)
    card.champions(29, y, [highlight.champion], 48)
    card.text(88, y, f"하이라이트 · {g.date:%m.%d %H:%M}", 16, MUTED)
    card.text(88, y + 23, f"{highlight.name} · {dd.champion_name(highlight.champion)}", 22, max_width=490)
    card.text(864, y + 14, f"{highlight.kills}/{highlight.deaths}/{highlight.assists}", 22, anchor="rt")
    card.tag(879, y + 10, {3: "트리플킬", 4: "쿼드라킬", 5: "펜타킬"}.get(highlight.multikill, "최다 킬"))
    return png(card.image)


def _pick(card: Card, x: int, y: int, champion: str, games: list[Game], *, best=False, same=False,
          show_rate=False) -> None:
    badge_x = x
    if not best:
        badge_x += card.tag(x, y, "모스트") + 5
    if best or same:
        card.tag(badge_x, y, "최고 승률", REPORT_TEAM_BLUE)
    card.champions(x, y + 31, [champion], 41)
    name_x = x + 51
    card.text(name_x, y + 31, card.dd.champion_name(champion), 20, max_width=174)
    card.results(name_x, y + 55, games, 17)
    if best or same or show_rate:
        rate = _win_rate(games)
        color = REPORT_VICTORY if rate > Fraction(1, 2) else REPORT_CORAL if rate < Fraction(1, 2) else MUTED
        card.text(x + 321, y + 37, f"{float(rate):.0%}", 27, color, anchor="rt")
    else:
        card.text(x + 321, y + 37, f"{len(games)}판", 24, REPORT_GOLD, anchor="rt")


def _picks_card(history: list[Game], window: list[Game], dd: DDragon) -> bytes:
    recent = Counter(p.name for g in window for p in g.players)
    picks = _participation(history)
    owners = sorted(recent, key=lambda name: (-recent[name], name))
    featured = min(picks[owners[0]], key=lambda c: (-len(picks[owners[0]][c]), dd.champion_name(c)))
    card = Card("친구별 모스트", "통산 · 최고 승률 5판 이상", 81 + 94 * len(owners), dd,
                art=featured, accent=REPORT_GOLD)
    for i, name in enumerate(owners):
        row = picks[name]
        most = min(row, key=lambda c: (-len(row[c]), dd.champion_name(c)))
        qualified = [c for c in row if len(row[c]) >= BEST_MIN_GAMES]
        best = min(qualified, key=lambda c: (-_win_rate(row[c]), -len(row[c]), dd.champion_name(c)), default=None)
        y = 76 + i * 94
        card.divider(y - 8)
        card.text(29, y + 23, name, 23, max_width=135)
        card.text(29, y + 52, f"최근 {recent[name]}판", 16, MUTED)
        same = best == most
        _pick(card, 180, y, most, row[most], same=same and len(qualified) > 1, show_rate=same)
        if same:
            card.text(559, y + 29, f"{len(row[most])}판", 19, MUTED)
            card.bar(559, 675, y + 56, row[most])
        elif best:
            _pick(card, 559, y, best, row[best], best=True)
        else:
            card.text(559, y + 24, "최고 승률", 17, MUTED)
            card.text(559, y + 47, "5판 이상 픽 없음", 17, MUTED, weight=5)
    return png(card.image)


def _duos_card(history: list[Game], window: list[Game], dd: DDragon) -> bytes:
    all_duos, recent = _duos(history), _duos(window)
    leaders = sorted(recent, key=lambda pair: (-len(recent[pair]), -len(all_duos[pair]), pair))[:4]
    combos = {
        pair: Counter(combo for _, combo in reversed(all_duos[pair])).most_common(1)[0]
        for pair in leaders
    }
    card = Card("친구 조합", "최근 동반 출전 TOP 4 · 누적 최다 픽 조합", 82 + 85 * len(leaders),
                dd, art=combos[leaders[0]][0][0], accent=REPORT_TEAM_BLUE)
    for i, pair in enumerate(leaders):
        y = 76 + i * 85
        card.divider(y - 8)
        card.text(29, y, " × ".join(pair), 24, max_width=430)
        combo, count = combos[pair]
        card.champions(29, y + 33, combo)
        card.text(113, y + 35, " + ".join(dd.champion_name(c) for c in combo), 18, max_width=303)
        card.text(113, y + 57, f"{count}판" if count > 1 else "재등장 없음", 15, MUTED)
        played, total = [g for g, _ in recent[pair]], [g for g, _ in all_duos[pair]]
        card.text(532, y + 8, "최근", 17, MUTED)
        card.results(532, y + 34, played, 24)
        card.text(748, y + 8, f"통산 {len(total)}판", 17, MUTED)
        card.results(748, y + 34, total, 24)
        card.bar(748, 896, y + 64, total)
    return png(card.image)


def _records_card(book: list[tuple[Record, str]], count: int, dd: DDragon) -> bytes:
    card = Card("기록실", f"통산 {count}판", 144 + 66 * len(book), dd, accent=REPORT_GOLD)
    y = 72
    for label, single in (("팀 기록", False), ("개인 기록", True)):
        card.text(29, y, label, 18, MUTED)
        y += 32
        for record, badge in book:
            player, game = record.player, record.game
            if (player is not None) != single:
                continue
            card.divider(y - 5)
            portraits = [player.champion] if player else [p.champion for p in game.players]
            card.champions(29, y + 6, portraits, 38 if player else 30)
            x = 83 if player else 215
            card.text(x, y + 3, record.label, 22)
            detail = f"{player.name} · {dd.champion_name(player.champion)}" if player else f"{game.kills} : {game.enemy_kills}"
            card.text(x, y + 32, f"{detail} · {game.date:%m.%d}", 16, MUTED, max_width=460)
            if badge:
                card.tag(759, y + 11, badge)
            card.text(968, y + 8, record.display, 26, REPORT_GOLD, anchor="rt")
            y += 66
    return png(card.image)


def build_league_report(history: list[Game], window: list[Game], dd: DDragon) -> tuple[dict, list[tuple]]:
    files = [("lol-match-summary.png", _match_card(window, dd)),
             ("lol-most-played.png", _picks_card(history, window, dd)),
             ("lol-friend-duos.png", _duos_card(history, window, dd))]
    records = _record_book(history, window)
    if records:
        files.append(("lol-records.png", _records_card(records, len(history), dd)))
    components = [{"type": 12, "items": [{"media": {"url": f"attachment://{name}"}}]} for name, _ in files]
    return {"flags": 32768, "allowed_mentions": {"users": []},
            "components": [{"type": 17, "accent_color": ACCENT_WIN, "components": components}]}, files
