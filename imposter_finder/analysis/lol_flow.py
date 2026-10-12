"""자동 롤 리포트의 경기 흐름 GIF: 미니맵 · 교전 · 오브젝트 · 골드 차이.

위치는 1분 스냅샷만으로는 뚝뚝 끊겨서, 킬·오브젝트·포탑 방패 좌표와 아이템 구매(우물)까지
경로점으로 모아 부드러운 곡선으로 잇는다. 실제 이동이 아니라 추정 경로다.
죽은 뒤 부활 시각은 레벨·경기 시간으로 계산한 대기 시간을 쓰고, 그보다 일찍 살아 있는
증거(스냅샷·킬 관여)가 있으면 그쪽에 맞춘다.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageOps

from imposter_finder.analysis.cards import (ALLY, BG, ENEMY, GOLD, HIGHLIGHT, LABEL, PAD, SS, TRACK, WHITE, WIDTH, GIF_MAX_BYTES,
                                            _download, ambient_bg, bar, font, glass, header, icon, shade)
from imposter_finder.analysis.lol_report import AMBIENT_DIM, DRAGON_ICONS, MINIMAP_ICONS, _scores, nickname
from imposter_finder.games.lol import DDragon

RIFT = "https://raw.communitydragon.org/latest/game/assets/maps/info/map11/2dlevelminimap_base_baron1.png"

# ── 배치 ────────────────────────────────────────────────
HEIGHT = 576
MAP_X, MAP_Y, MAP = PAD, 76, 468  # 왼쪽 정사각 미니맵
RX, RR = MAP_X + MAP + 32, WIDTH - PAD  # 오른쪽 칸
CHAMP_D, CHAMP_GAP = 26, 21  # 지도 위 챔피언 지름 · 이보다 가까우면 서로 민다

# ── 재생 속도 ────────────────────────────────────────────
# 용량은 거의 "움직인 챔피언 수 × 장면 수"라 장면 수가 곧 용량이다.
# 장면 수를 제한한 뒤 실제 인코딩 크기도 검사한다 (cards.GIF_MAX_BYTES 8MiB).
FRAME_MS = 100  # 10fps
SEC_PER_MIN = 1.85  # 경기 1분의 기본 재생 시간, 긴 경기는 자동으로 줄인다
MAX_FLOW_FRAMES = 700  # 렌더링 시간·메모리 제한, 최종 파일은 별도로 8MiB 이하 확인
SLOW = 0.5  # 킬 앞뒤는 이만큼 느리게
SLOW_BEFORE, SLOW_AFTER = 2500, 4000  # 느리게 보는 구간 (경기 ms)
FAST_UNTIL, FAST = 80_000, 3.0  # 첫 80초(우물 → 라인)는 빠르게
FIRST_MS, LAST_MS = 800, 3500

# ── 지도 좌표 ────────────────────────────────────────────
WORLD_MIN, WORLD_SPAN = -120, 14990  # 미니맵 그림이 덮는 월드 범위 (지형 여백 포함)
FOUNTAIN = {100: (554, 581), 200: (14302, 14387)}
TURRETS = {  # 팀 → 포탑 좌표 (외곽·내부·억제기·쌍둥이)
    100: [(981, 10441), (1512, 6699), (1169, 4287), (5846, 6396), (5048, 4812), (3651, 3696),
          (10504, 1029), (6919, 1483), (4281, 1253), (1748, 2270), (2177, 1807)],
    200: [(4318, 13875), (7943, 13411), (10481, 13650), (8955, 8510), (9767, 10113), (11134, 11207),
          (13866, 4505), (13327, 8226), (13624, 10572), (12611, 13084), (13052, 12612)],
}
INHIBITORS = {100: [(1171, 3571), (3203, 3208), (3452, 1236)], 200: [(11261, 13676), (11598, 11667), (13604, 11316)]}
INHIBITOR_RESPAWN = 300_000
PITS = [((9866, 4414), "dragon"), ((5007, 10471), "baron")]
VMAX = 0.9  # 월드 단위/ms. 이보다 빨라야 닿는 추정 경로점(먼 거리 어시스트 등)은 버린다
WALK = 0.42  # 부활 뒤 우물에서 걷는 속도 (월드 단위/ms)

# 부활 대기 (초): 레벨별 기본값 × (1 + 경기 시간 가중)
DEATH_BASE = [6, 6, 8, 8, 10, 12, 16, 21, 26, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]

MONSTERS = {"BARON_NASHOR": ("바론", "baron"), "RIFTHERALD": ("전령", "riftherald"), "HORDE": ("공허 유충", "grub"),
            "ATAKHAN": ("아타칸", "atakhan_v")}
DRAGON_NAMES = {"FIRE_DRAGON": "화염", "WATER_DRAGON": "바다", "EARTH_DRAGON": "대지", "AIR_DRAGON": "바람",
                "HEXTECH_DRAGON": "마법공학", "CHEMTECH_DRAGON": "화학공학", "ELDER_DRAGON": "장로"}
SPECIALS = {"KILL_FIRST_BLOOD": "선취점", "KILL_ACE": "에이스"}
MULTI = {2: "더블 킬", 3: "트리플 킬", 4: "쿼드라 킬", 5: "펜타 킬"}
TALLY = ["tower", "dragon", "baron", "riftherald", "grub", "atakhan", "inhibitor"]  # 팀 줄 오른쪽 집계 순서
TALLY_ICON = {"tower": "tower", "dragon": "dragon", "baron": "baron", "riftherald": "riftherald", "grub": "grub",
              "atakhan": "atakhan_v", "inhibitor": "inhibitor"}


def to_map(pos) -> tuple[float, float]:
    x, y = (pos["x"], pos["y"]) if isinstance(pos, dict) else pos
    return (MAP_X + (x - WORLD_MIN) / WORLD_SPAN * MAP, MAP_Y + (1 - (y - WORLD_MIN) / WORLD_SPAN) * MAP)


def clock(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60:02d}:{s % 60:02d}"


def death_seconds(level: int, ms: float) -> float:
    m = ms / 60000
    if m < 15:
        extra = 0
    elif m < 30:
        extra = math.ceil(2 * (m - 15)) * 0.00425
    elif m < 45:
        extra = 0.1275 + math.ceil(2 * (m - 30)) * 0.003
    else:
        extra = min(0.5, 0.2175 + math.ceil(2 * (m - 45)) * 0.0145)
    return DEATH_BASE[min(max(level, 1), 18) - 1] * (1 + extra)


# ── 그림 조각 (한 번 그려 두고 정수 좌표에 붙인다: 안 움직이면 픽셀이 같아 GIF가 작다) ──
def _image(url: str) -> Image.Image:
    return Image.open(BytesIO(_download(url))).convert("RGBA")


def disc(url: str, d: int, ring: tuple, crop: float = 0.86, dead: bool = False, dot: bool = False) -> Image.Image:
    """원형 아이콘 + 고리. dead면 흑백, dot이면 친구 표시 금색 점."""
    size = d * SS
    src = _image(url)
    w, h = src.size
    mx, my = w * (1 - crop) / 2, h * (1 - crop) / 2
    src = src.crop((round(mx), round(my), round(w - mx), round(h - my))).resize((size, size), Image.LANCZOS)
    if dead:
        src = ImageEnhance.Brightness(ImageOps.grayscale(src).convert("RGBA")).enhance(0.55)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    layer.paste(Image.new("RGBA", (size, size), BG + (255,)), (0, 0), mask)
    layer.paste(src, (0, 0), ImageChops.multiply(src.getchannel("A"), mask))
    dr = ImageDraw.Draw(layer)
    dr.ellipse((0, 0, size - 1, size - 1), outline=ring, width=round(2.5 * SS))
    if dead:  # 가운데 ×
        c, r = size / 2, size * 0.2
        dr.line((c - r, c - r, c + r, c + r), fill=(220, 220, 215), width=2 * SS)
        dr.line((c + r, c - r, c - r, c + r), fill=(220, 220, 215), width=2 * SS)
    if dot:
        r = 3.5 * SS
        dr.ellipse((size - 2 * r - SS, size - 2 * r - SS, size - SS, size - SS), fill=HIGHLIGHT, outline=BG, width=SS)
    return layer.resize((d, d), Image.LANCZOS)


def tinted(url: str, d: int, color: tuple, alpha: float = 1.0) -> Image.Image:
    """회색 미니맵 아이콘(포탑·억제기)을 팀 색으로. 검은 테두리는 그대로 남는다."""
    src = icon(url, d, radius=0)
    rgb = ImageChops.multiply(src.convert("RGB"), Image.new("RGB", src.size, tuple(min(255, c + 40) for c in color)))
    out = rgb.convert("RGBA")
    out.putalpha(src.getchannel("A").point(lambda v: int(v * alpha)))
    return out


def ring_sprite(radius: float, color: tuple, alpha: int) -> Image.Image:
    size = math.ceil(radius * 2 + 6)
    layer = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    c = size * SS / 2
    ImageDraw.Draw(layer).ellipse((c - radius * SS, c - radius * SS, c + radius * SS, c + radius * SS),
                                  outline=color + (alpha,), width=round(2.5 * SS))
    return layer.resize((size, size), Image.LANCZOS)


def shared_palette(parts: list[tuple[Image.Image, int]], text: list[tuple]) -> Image.Image:
    """영역마다 색 수를 정해 한 팔레트로 (한 번에 뽑으면 넓은 지도 색이 다른 색에 밀린다).
    글자 색은 바탕과 섞인 가장자리 색까지 넣어 글자가 매끈하다. 255색: 1색은 투명용으로 남긴다."""
    colors: list[tuple] = []
    for c in text:
        for k in (0.3, 0.55, 0.8, 1.0):
            colors.append(tuple(round(b * (1 - k) + v * k) for v, b in zip(c, BG)))
    for im, n in parts:
        q = im.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[:n * 3]
        colors += [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    unique = list(dict.fromkeys(colors))[:255]
    out = Image.new("P", (1, 1))
    out.putpalette([v for c in unique for v in c])
    return out


def put(img: Image.Image, sprite: Image.Image, cx: float, cy: float) -> None:
    img.paste(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)), sprite)


# ── 경로 ────────────────────────────────────────────────
class Path2D:
    """한 선수의 경로: 살아 있는 구간마다 경로점을 Hermite 곡선으로 잇는다."""

    def __init__(self) -> None:
        self.segments: list[list[tuple[float, float, float]]] = []  # [(t, x, y)]
        self.tangents: list[list[tuple[float, float]]] = []
        self.deaths: list[tuple[float, float, tuple]] = []  # (죽은 시각, 부활 시각, 좌표)

    def build(self) -> None:
        for seg in self.segments:
            tans = []
            for k, (t, x, y) in enumerate(seg):
                if k == 0 or k == len(seg) - 1:
                    tans.append((0.0, 0.0))  # 부활·죽음·시작은 멈춘 상태에서
                    continue
                (t0, x0, y0), (t2, x2, y2) = seg[k - 1], seg[k + 1]
                s1 = ((x - x0) / (t - t0), (y - y0) / (t - t0))
                s2 = ((x2 - x) / (t2 - t), (y2 - y) / (t2 - t))
                if s1[0] * s2[0] + s1[1] * s2[1] <= 0:  # 방향이 꺾이면 그 점에서 멈췄다 간다
                    tans.append((0.0, 0.0))
                    continue
                m = ((s1[0] + s2[0]) / 2, (s1[1] + s2[1]) / 2)
                cap = min(math.hypot(*s1), math.hypot(*s2))
                k_ = min(1.0, cap / max(math.hypot(*m), 1e-9))  # 지나치게 튀어나가지 않게
                tans.append((m[0] * k_, m[1] * k_))
            self.tangents.append(tans)

    def dead_at(self, t: float):
        for d0, d1, pos in self.deaths:
            if d0 <= t < d1:
                return d0, d1, pos
        return None

    def at(self, t: float) -> tuple[float, float] | None:
        for seg, tans in zip(self.segments, self.tangents):
            if not seg or t < seg[0][0] or t > seg[-1][0]:
                continue
            k = max(0, min(bisect_right([p[0] for p in seg], t) - 1, len(seg) - 2))
            if len(seg) == 1:
                return seg[0][1:]
            (t0, x0, y0), (t1, x1, y1) = seg[k], seg[k + 1]
            (mx0, my0), (mx1, my1) = tans[k], tans[k + 1]
            dt = t1 - t0
            u = (t - t0) / dt if dt else 1
            h00, h10, h01, h11 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u, -2 * u**3 + 3 * u**2, u**3 - u**2
            return (h00 * x0 + h10 * dt * mx0 + h01 * x1 + h11 * dt * mx1,
                    h00 * y0 + h10 * dt * my0 + h01 * y1 + h11 * dt * my1)
        return None


def build_paths(info: dict, timeline: dict) -> dict[int, Path2D]:
    ps = {p["participantId"]: p for p in info["participants"]}
    frames = timeline["info"]["frames"]
    events = sorted((e for f in frames for e in f["events"]), key=lambda e: e["timestamp"])
    end = info["gameDuration"] * 1000
    fountain = {pid: FOUNTAIN[p["teamId"]] for pid, p in ps.items()}

    levels = defaultdict(list)
    for e in events:
        if e["type"] == "LEVEL_UP":
            levels[e["participantId"]].append(e["timestamp"])
    level_at = lambda pid, t: 1 + bisect_right(levels[pid], t)

    # 살아 있다는 증거: 스냅샷(체력>0) · 킬 관여 · 오브젝트 처치
    alive = defaultdict(list)  # pid → [(t, (x, y))]
    for f in frames:
        for pid, pf in f["participantFrames"].items():
            if pf.get("championStats", {}).get("health", 1) > 0:
                alive[int(pid)].append((f["timestamp"], (pf["position"]["x"], pf["position"]["y"])))
    for e in events:
        if e["type"] in {"CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL"} and e.get("position"):
            for pid in [e.get("killerId"), *e.get("assistingParticipantIds", [])]:
                if pid in ps:
                    alive[pid].append((e["timestamp"], (e["position"]["x"], e["position"]["y"])))

    paths = {pid: Path2D() for pid in ps}
    kills = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("position")]
    for e in kills:
        pid, t = e["victimId"], e["timestamp"]
        pos = (e["position"]["x"], e["position"]["y"])
        respawn = t + death_seconds(level_at(pid, t), t) * 1000
        for at, where in alive[pid]:  # 계산보다 일찍 살아 있으면 그때 우물에서 걸어간 것으로
            if t + 3000 < at < respawn:
                respawn = min(respawn, max(t + 3000, at - math.dist(fountain[pid], where) / WALK))
        paths[pid].deaths.append((t, respawn, pos))
    for path in paths.values():
        path.deaths.sort()
        for n in range(len(path.deaths) - 1):  # 다음 죽음보다 늦게 부활할 순 없다
            d0, d1, pos = path.deaths[n]
            path.deaths[n] = (d0, min(d1, path.deaths[n + 1][0] - 1000), pos)

    # 경로점: (t, x, y, 확실한가)
    points = defaultdict(list)
    for f in frames:
        for pid, pf in f["participantFrames"].items():
            pid = int(pid)
            if not paths[pid].dead_at(f["timestamp"]) and pf.get("championStats", {}).get("health", 1) > 0:
                points[pid].append((f["timestamp"], pf["position"]["x"], pf["position"]["y"], True))
    for e in events:
        t, kind = e["timestamp"], e["type"]
        pos = e.get("position")
        if kind == "CHAMPION_KILL" and pos:
            points[e["victimId"]].append((t, pos["x"], pos["y"], True))
        if kind in {"CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL", "TURRET_PLATE_DESTROYED"} and pos:
            for pid in [e.get("killerId"), *e.get("assistingParticipantIds", [])]:
                if pid in ps:
                    points[pid].append((t, pos["x"], pos["y"], False))
        if kind in {"ITEM_PURCHASED", "ITEM_SOLD"} and e.get("participantId") in ps:
            pid = e["participantId"]
            if not paths[pid].dead_at(t) and t > 5000:
                points[pid].append((t, *fountain[pid], False))

    for pid, path in paths.items():
        bounds = [(0, path.deaths[0][0] if path.deaths else end, fountain[pid])]
        for n, (d0, d1, _) in enumerate(path.deaths):
            nxt = path.deaths[n + 1][0] if n + 1 < len(path.deaths) else end
            if d1 < end:
                bounds.append((d1, nxt, fountain[pid]))
        for start, stop, home in bounds:
            raw = sorted((p for p in points[pid] if start < p[0] <= stop), key=lambda p: (p[0], not p[3]))
            seg = [(start, *home, True)]
            hard = [p for p in raw if p[3]]
            for p in raw:
                prev = seg[-1]
                if p[0] - prev[0] < 400:  # 거의 같은 시각은 하나로
                    if p[3] and not prev[3]:
                        seg[-1] = p
                    continue
                if not p[3]:
                    nxt = next((h for h in hard if h[0] > p[0]), None)
                    if math.dist(prev[1:3], p[1:3]) / (p[0] - prev[0]) > VMAX:
                        continue
                    if nxt and math.dist(p[1:3], nxt[1:3]) / max(nxt[0] - p[0], 1) > VMAX:
                        continue
                seg.append(p)
            path.segments.append([(t, x, y) for t, x, y, _ in seg])
        path.build()
    return paths


def separate(points: dict[int, tuple[float, float]]) -> dict[int, tuple[float, float]]:
    """겹친 아이콘을 살짝 민다. 매 장면 같은 규칙이라 위치가 이어서 바뀐다."""
    pos = {pid: list(p) for pid, p in points.items()}
    ids = sorted(pos)
    for _ in range(10):
        moved = False
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
                dist = math.hypot(dx, dy)
                if dist >= CHAMP_GAP:
                    continue
                if dist < 0.01:  # 같은 점이면 번호로 정한 방향으로
                    angle = (a * 7 + b * 3) % 12 * math.pi / 6
                    dx, dy, dist = math.cos(angle), math.sin(angle), 1.0
                push = (CHAMP_GAP - dist) / 2 * 0.6
                pos[a][0] -= dx / dist * push
                pos[a][1] -= dy / dist * push
                pos[b][0] += dx / dist * push
                pos[b][1] += dy / dist * push
                moved = True
        if not moved:
            break
    lo, hi = MAP_X + CHAMP_D / 2, MAP_X + MAP - CHAMP_D / 2
    lo_y, hi_y = MAP_Y + CHAMP_D / 2, MAP_Y + MAP - CHAMP_D / 2
    return {pid: (min(max(x, lo), hi), min(max(y, lo_y), hi_y)) for pid, (x, y) in pos.items()}


# ── 장면 ────────────────────────────────────────────────
def schedule(events: list[float], end: float, sec_per_min: float = SEC_PER_MIN) -> list[float]:
    """재생할 경기 시각들. 평소엔 일정하게, 킬·오브젝트 근처는 느리게."""
    if end <= 0 or sec_per_min <= 0:
        raise ValueError("경기 길이와 재생 속도는 양수여야 한다")
    base = 60000 / (sec_per_min * 1000 / FRAME_MS)
    marks = sorted(events)
    out, t = [], 0.0
    while t < end:
        out.append(t)
        step = base * (FAST if t < FAST_UNTIL else 1)
        k = bisect_right(marks, t + SLOW_BEFORE)
        if k and marks[k - 1] + SLOW_AFTER >= t:
            step *= SLOW
        t += step
    out.append(end)
    return out


def encode_bounded(frames: list[Image.Image], max_bytes: int = GIF_MAX_BYTES) -> bytes:
    """Keep 10fps and the entire match, accelerating playback if encoded bytes exceed the limit."""
    if len(frames) < 2:
        raise ValueError("경기 흐름에는 시작과 끝 장면이 필요하다")
    count = len(frames)
    while True:
        indices = [round(i * (len(frames) - 1) / (count - 1)) for i in range(count)]
        selected = [frames[i] for i in indices]
        durations = [FRAME_MS] * count
        durations[0], durations[-1] = FIRST_MS, LAST_MS
        out = BytesIO()
        selected[0].save(out, "GIF", save_all=True, append_images=selected[1:], duration=durations,
                         loop=0, optimize=True)
        data = out.getvalue()
        if len(data) <= max_bytes:
            return data
        if count == 2:
            raise ValueError("시작·종료 장면도 GIF 용량 한도를 초과했다")
        count = max(2, min(count - 1, int(count * max_bytes / len(data) * .85)))


def render(match: dict, timeline: dict, friend_puuids: set[str],
           ddragon: DDragon | None = None) -> tuple[bytes, Image.Image]:
    info = match["info"]
    if info["mapId"] != 11:
        raise ValueError("소환사의 협곡만 그린다")
    dd = ddragon or DDragon()
    ps = {p["participantId"]: p for p in info["participants"]}
    mine = [p for p in ps.values() if p["puuid"] in friend_puuids]
    team_id = mine[0]["teamId"] if mine else 100
    color = lambda team: ALLY if team == team_id else ENEMY
    color_of = lambda pid: color(ps[pid]["teamId"]) if pid in ps else LABEL
    end = info["gameDuration"] * 1000
    frames_tl = timeline["info"]["frames"]
    events = sorted((e for f in frames_tl for e in f["events"]), key=lambda e: e["timestamp"])
    paths = build_paths(info, timeline)
    stats = _scores(info["participants"], info["gameDuration"] / 60)
    mvp = max(mine or list(ps.values()), key=lambda p: stats[p["puuid"]]["score"])
    splash = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{mvp['championName']}_0.jpg"
    champ_url = {pid: dd.champion_icon(p["championName"]) for pid, p in ps.items()}
    friend = {pid: p["puuid"] in friend_puuids for pid, p in ps.items()}
    order = {team: [pid for pid in sorted(ps) if ps[pid]["teamId"] == team] for team in (team_id, 300 - team_id)}

    # 사건: 킬 · 오브젝트 · 건물
    kills = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("position")]
    label = {}
    for s in (e for e in events if e["type"] == "CHAMPION_SPECIAL_KILL"):
        cands = [k for k in kills if k["killerId"] == s["killerId"] and abs(k["timestamp"] - s["timestamp"]) <= 1000]
        if cands:
            k = min(cands, key=lambda k: abs(k["timestamp"] - s["timestamp"]))
            text = MULTI.get(s.get("multiKillLength")) if s["killType"] == "KILL_MULTI" else SPECIALS.get(s["killType"])
            if text and (id(k) not in label or s["killType"] == "KILL_MULTI"):
                label[id(k)] = text
    monsters = [e for e in events if e["type"] == "ELITE_MONSTER_KILL" and e.get("position")]
    buildings = [e for e in events if e["type"] == "BUILDING_KILL" and e.get("position")]

    def taker(e) -> int:
        if e["type"] == "BUILDING_KILL":
            return 300 - e["teamId"]
        return e.get("killerTeamId") or ps.get(e.get("killerId"), {}).get("teamId", 0)

    def monster_meta(e) -> tuple[str, str, str]:
        """(집계 키, 미니맵 아이콘 이름, 표시 이름)"""
        if e["monsterType"] == "DRAGON":
            sub = e.get("monsterSubType", "")
            return "dragon", DRAGON_ICONS.get(sub, "dragon"), f"{DRAGON_NAMES.get(sub, '')} 드래곤".strip()
        name, ic = MONSTERS.get(e["monsterType"], (e["monsterType"], "dragon"))
        return ("atakhan" if ic.startswith("atakhan") else ic), ic, name

    # 포탑·억제기가 언제 부서졌나 (가장 가까운 알려진 자리)
    structures = []  # (팀, 좌표, 종류)
    for team in (100, 200):
        structures += [(team, p, "tower") for p in TURRETS[team]] + [(team, p, "inhibitor") for p in INHIBITORS[team]]
    broken = defaultdict(list)  # 구조물 번호 → [부서진 시각]
    for e in buildings:
        kind = "inhibitor" if e.get("buildingType") == "INHIBITOR_BUILDING" else "tower"
        pos = (e["position"]["x"], e["position"]["y"])
        cands = [n for n, (team, p, k) in enumerate(structures) if team == e["teamId"] and k == kind]
        if cands:
            broken[min(cands, key=lambda n: math.dist(structures[n][1], pos))].append(e["timestamp"])

    def standing(n: int, t: float) -> bool:
        team, _, kind = structures[n]
        for at in broken.get(n, []):
            if at <= t and (kind == "tower" or t < at + INHIBITOR_RESPAWN):
                return False
        return True

    # ── 그림 조각 ──
    map_sprite = {pid: disc(champ_url[pid], CHAMP_D, color_of(pid), dot=friend[pid]) for pid in ps}
    dead_sprite = {pid: disc(champ_url[pid], CHAMP_D, (110, 115, 118), dead=True) for pid in ps}
    roster_size = 38
    roster = {pid: icon(champ_url[pid], roster_size, radius=8) for pid in ps}
    roster_dead = {}
    for pid, im in roster.items():
        grey = ImageEnhance.Brightness(ImageOps.grayscale(im.convert("RGB"))).enhance(0.45).convert("RGBA")
        grey.putalpha(im.getchannel("A"))
        roster_dead[pid] = grey
    feed_size = 26
    feed_icon = {pid: icon(champ_url[pid], feed_size, radius=6) for pid in ps}
    tower = {team: tinted(f"{MINIMAP_ICONS}/tower.png", 15, color(team)) for team in (100, 200)}
    inhib = {team: tinted(f"{MINIMAP_ICONS}/inhibitor.png", 13, color(team)) for team in (100, 200)}
    rubble = {"tower": tinted(f"{MINIMAP_ICONS}/tower.png", 15, (90, 90, 90), alpha=0.45),
              "inhibitor": tinted(f"{MINIMAP_ICONS}/inhibitor.png", 13, (90, 90, 90), alpha=0.45)}
    rings = {c: [ring_sprite(8 + 20 * k / 7, c, round(230 * (1 - k / 9))) for k in range(8)] for c in (ALLY, ENEMY, LABEL)}
    pops = {}  # (아이콘, 팀) → [크기별]

    def pop(name: str, team: int, scale: int) -> Image.Image:
        key = (name, team, scale)
        if key not in pops:
            pops[key] = disc(f"{MINIMAP_ICONS}/{name}.png", [24, 30, 36][scale], color(team), crop=0.8)
        return pops[key]

    tally_icon = {k: icon(f"{MINIMAP_ICONS}/{v}.png", 18, radius=0) for k, v in TALLY_ICON.items()}

    # 경기 전체에서 한 번이라도 나온 오브젝트만 집계한다
    def tally_at(t: float) -> dict[int, dict[str, int]]:
        counts = {100: defaultdict(int), 200: defaultdict(int)}
        for e in monsters + buildings:
            if e["timestamp"] > t:
                continue
            key = monster_meta(e)[0] if e["type"] == "ELITE_MONSTER_KILL" else \
                ("inhibitor" if e.get("buildingType") == "INHIBITOR_BUILDING" else "tower")
            team = taker(e)
            if team in counts:
                counts[team][key] += 1
        return counts

    final = tally_at(end)
    tally_keys = [k for k in TALLY if final[100][k] or final[200][k]][:5]

    # ── 고정 바탕 ──
    base = Image.new("RGB", (WIDTH, HEIGHT), BG)
    ambient_bg(base, splash, dim=AMBIENT_DIM, focus_y=0.45)
    rift = ImageEnhance.Brightness(_image(RIFT).convert("RGB")).enhance(0.78).resize((MAP, MAP), Image.LANCZOS)
    mask = Image.new("L", (MAP * SS, MAP * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, MAP * SS - 1, MAP * SS - 1), radius=14 * SS, fill=255)
    base.paste(rift, (MAP_X, MAP_Y), mask.resize((MAP, MAP), Image.LANCZOS))
    for pos, name in PITS:
        pit = icon(f"{MINIMAP_ICONS}/{name}.png", 20, radius=0)
        pit.putalpha(pit.getchannel("A").point(lambda v: v * 4 // 10))
        put(base, pit, *to_map(pos))
    team_panel = (RX, 144, RR, 268)
    feed_top, feed_h, feed_gap, feed_rows = 282, 36, 6, 4
    gold_panel = (RX, 460, RR, MAP_Y + MAP)
    glass(base, team_panel)
    for n in range(feed_rows):
        y = feed_top + n * (feed_h + feed_gap)
        glass(base, (RX, y, RR, y + feed_h), radius=10, alpha=10)
    glass(base, gold_panel)
    dr = ImageDraw.Draw(base)
    header(dr, "경기 흐름", [(ALLY, "우리 팀"), (ENEMY, "상대 팀")])
    dr.text((RX + 16, gold_panel[1] + 12), "골드 차이", font=font(15), fill=LABEL)
    gx0, gx1 = RX + 16, RR - 16
    gy0, gy1 = gold_panel[1] + 40, gold_panel[3] - 12
    gmid = (gy0 + gy1) / 2
    for x in range(gx0, gx1, 8):
        dr.line((x, gmid, x + 3, gmid), fill=shade(LABEL, 0.5), width=1)
    row_y = {team_id: team_panel[1] + 31, 300 - team_id: team_panel[3] - 31}
    for team, y in row_y.items():
        dr.rounded_rectangle((RX + 12, y - 15, RX + 15, y + 15), radius=2, fill=color(team))
    bar(base, (RX, 126, RR, 131), TRACK, dim_side=None)

    # 골드 차이 (스냅샷 사이는 직선으로)
    gold_t = [f["timestamp"] for f in frames_tl]
    gold_v = []
    for f in frames_tl:
        totals = {100: 0, 200: 0}
        for pid, pf in f["participantFrames"].items():
            totals[ps[int(pid)]["teamId"]] += pf["totalGold"]
        gold_v.append(totals[team_id] - totals[300 - team_id])
    gold_t[-1] = min(gold_t[-1], end)
    gold_span = max(max(abs(v) for v in gold_v), 1000)

    def gold_at(t: float) -> float:
        k = max(0, min(bisect_right(gold_t, t) - 1, len(gold_t) - 2))
        u = min(max((t - gold_t[k]) / max(gold_t[k + 1] - gold_t[k], 1), 0), 1)
        return gold_v[k] + (gold_v[k + 1] - gold_v[k]) * u

    gpx = lambda t: gx0 + (gx1 - gx0) * min(t / end, 1)
    gpy = lambda v: gmid - v / gold_span * (gy1 - gy0) / 2

    # 피드: 킬 · 오브젝트 · 건물 (시간 순)
    feed = [(e["timestamp"], "kill", e) for e in kills] + [(e["timestamp"], "monster", e) for e in monsters] + \
           [(e["timestamp"], "building", e) for e in buildings]
    feed.sort(key=lambda r: r[0])
    feed_t = [r[0] for r in feed]

    drawn: dict[int, tuple[int, int]] = {}

    def draw_frame(t: float) -> Image.Image:
        im = base.copy()
        dr = ImageDraw.Draw(im)

        # 지도: 구조물 → 오브젝트 → 죽은 자리 → 챔피언 → 효과
        for n, (team, pos, kind) in enumerate(structures):
            sprite = (tower if kind == "tower" else inhib)[team] if standing(n, t) else rubble[kind]
            put(im, sprite, *to_map(pos))
        for pid, path in paths.items():
            death = path.dead_at(t)
            if death and t - death[0] < 8000:
                put(im, dead_sprite[pid], *to_map(death[2]))
        live = {}
        for pid, path in paths.items():
            if path.dead_at(t):
                continue
            p = path.at(t)
            if p:
                live[pid] = to_map(p)
        placed = separate(live)
        for pid, (x, y) in placed.items():  # 1px 안쪽 흔들림은 무시: 안 움직인 아이콘은 픽셀이 그대로
            old = drawn.get(pid)
            placed[pid] = old if old and abs(x - old[0]) < 0.9 and abs(y - old[1]) < 0.9 else (round(x), round(y))
            drawn[pid] = placed[pid]
        for pid in sorted(placed, key=lambda q: (friend[q], q)):  # 친구가 위에
            put(im, map_sprite[pid], *placed[pid])
        for e in monsters:  # 오브젝트는 챔피언 위에 잠시 크게
            age = t - e["timestamp"]
            if 0 <= age < 12000:
                _, ic, _ = monster_meta(e)
                put(im, pop(ic, taker(e), 0 if age < 400 else 1 if age < 800 else 2), *to_map(e["position"]))
        for e in kills + buildings:
            age = t - e["timestamp"]
            if 0 <= age < 2400:
                c = color_of(e["killerId"]) if e["type"] == "CHAMPION_KILL" else color(taker(e))
                if e["type"] == "CHAMPION_KILL" and e["killerId"] not in ps:
                    c = LABEL
                put(im, rings[c][min(7, int(age / 300))], *to_map(e["position"]))

        # 시계 · 킬 스코어 · 진행
        dr.text((RX, 98), clock(t), font=font(40, 6), fill=WHITE, anchor="lm")
        score = {100: 0, 200: 0}
        for e in kills:
            if e["timestamp"] > t:
                break
            if e["killerId"] in ps:
                score[ps[e["killerId"]]["teamId"]] += 1
        right = str(score[300 - team_id])
        f40 = font(40, 6)
        dr.text((RR, 98), right, font=f40, fill=ENEMY, anchor="rm")
        x = RR - dr.textlength(right, font=f40) - 12
        dr.text((x, 98), ":", font=font(30, 4), fill=LABEL, anchor="rm")
        x -= dr.textlength(":", font=font(30, 4)) + 12
        dr.text((x, 98), str(score[team_id]), font=f40, fill=ALLY, anchor="rm")
        x -= dr.textlength(str(score[team_id]), font=f40) + 14
        dr.text((x, 102), "킬", font=font(16), fill=LABEL, anchor="rm")
        if t > 0:
            bar(im, (RX, 126, RX + (RR - RX) * min(t / end, 1), 131), GOLD)

        # 팀 줄: 챔피언 (죽으면 흑백 + 남은 시간) · 오브젝트 집계
        counts = tally_at(t)
        for team, y in row_y.items():
            for n, pid in enumerate(order[team]):
                x0 = RX + 26 + n * (roster_size + 6)
                y0 = y - roster_size // 2
                death = paths[pid].dead_at(t)
                im.paste(roster_dead[pid] if death else roster[pid], (x0, y0), roster[pid])
                if friend[pid]:
                    dr.rounded_rectangle((x0 - 2, y0 - 2, x0 + roster_size + 1, y0 + roster_size + 1), radius=10,
                                         outline=HIGHLIGHT, width=2)
                if death:
                    left = math.ceil((death[1] - t) / 1000)
                    dr.text((x0 + roster_size / 2, y0 + roster_size / 2), str(left), font=font(17, 6), fill=WHITE,
                            anchor="mm", stroke_width=2, stroke_fill=BG)
            if tally_keys:
                x_start = RX + 26 + 5 * (roster_size + 6) + 8
                cell = (RR - 14 - x_start) / len(tally_keys)
                for n, key in enumerate(tally_keys):
                    cx = x_start + n * cell
                    ic = tally_icon[key]
                    if ic:
                        im.paste(ic, (round(cx), y - 9), ic)
                    v = counts[team][key]
                    dr.text((cx + 22, y), str(v), font=font(17, 6), fill=color(team) if v else LABEL, anchor="lm")

        # 피드: 최근 4개, 새 줄이 위
        recent = feed[max(0, bisect_right(feed_t, t) - feed_rows):bisect_right(feed_t, t)][::-1]
        for n, (at, kind, e) in enumerate(recent):
            y = feed_top + n * (feed_h + feed_gap)
            cy = y + feed_h / 2
            if t - at < 3000:  # 막 일어난 일은 판을 밝게
                glass(im, (RX, y, RR, y + feed_h), radius=10, alpha=22)
            dr.text((RX + 14, cy), clock(at), font=font(15), fill=LABEL, anchor="lm")
            x = RX + 66
            if kind == "kill":  # [처치자] 이름 → [피해자] 이름 · 특수 킬
                killer, victim = e["killerId"], e["victimId"]
                ours = ps[victim]["teamId"] != team_id
                tag = label.get(id(e))
                right = RR - 14
                if tag:
                    dr.text((right, cy), tag, font=font(16, 6), fill=ALLY if ours else ENEMY, anchor="rm")
                    right -= dr.textlength(tag, font=font(16, 6)) + 12
                room = (right - x - 2 * (feed_size + 8) - 28) / 2  # 두 이름이 나눠 쓴다

                def fit(name: str) -> str:
                    while len(name) > 1 and dr.textlength(name, font=font(15)) > room:
                        name = name[:-2] + "…"
                    return name

                if killer in ps:
                    im.paste(feed_icon[killer], (x, round(cy - feed_size / 2)), feed_icon[killer])
                    name = fit(nickname(ps[killer]))
                    dr.text((x + feed_size + 8, cy), name, font=font(15), fill=WHITE, anchor="lm")
                    x += feed_size + 8 + dr.textlength(name, font=font(15)) + 10
                else:
                    dr.text((x, cy), "처형", font=font(15), fill=LABEL, anchor="lm")
                    x += dr.textlength("처형", font=font(15)) + 10
                dr.line((x, cy, x + 10, cy), fill=LABEL, width=2)
                dr.line((x + 6, cy - 4, x + 10, cy), fill=LABEL, width=2)
                dr.line((x + 6, cy + 4, x + 10, cy), fill=LABEL, width=2)
                x += 20
                grey = ImageOps.grayscale(feed_icon[victim].convert("RGB")).convert("RGB")
                im.paste(grey, (round(x), round(cy - feed_size / 2)), feed_icon[victim])
                dr.text((x + feed_size + 8, cy), fit(nickname(ps[victim])), font=font(15), fill=LABEL, anchor="lm")
            else:
                team = taker(e)
                if kind == "monster":
                    _, ic, title = monster_meta(e)
                    put(im, pop(ic, team, 0), x + feed_size / 2, cy)
                else:
                    title = "억제기 파괴" if e.get("buildingType") == "INHIBITOR_BUILDING" else "포탑 파괴"
                    sprite = inhib[300 - team] if "억제기" in title else tower[300 - team]
                    put(im, sprite, x + feed_size / 2, cy)
                dr.text((x + feed_size + 12, cy), title, font=font(15), fill=WHITE, anchor="lm")
                dr.text((RR - 14, cy), "우리 팀" if team == team_id else "상대 팀", font=font(16, 6),
                        fill=color(team), anchor="rm")

        # 골드 차이: 지금까지의 선 + 값
        diff = gold_at(t)
        dr.text((RR - 16, gold_panel[1] + 10), f"{diff / 1000:+.1f}k", font=font(20, 6),
                fill=ALLY if diff >= 0 else ENEMY, anchor="ra")
        layer = Image.new("RGBA", ((gx1 - gx0 + 12) * SS, (gy1 - gy0 + 12) * SS), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ox, oy = gx0 - 6, gy0 - 6
        knots = [tt for tt in gold_t if tt < t] + [t]
        pts = [((gpx(tt) - ox) * SS, (gpy(gold_at(tt)) - oy) * SS, gold_at(tt)) for tt in knots]
        zero = (gmid - oy) * SS
        for (x1, y1, v1), (x2, y2, v2) in zip(pts, pts[1:]):
            if v1 * v2 < 0:
                xm = x1 + (x2 - x1) * v1 / (v1 - v2)
                ld.line((x1, y1, xm, zero), fill=ALLY if v1 >= 0 else ENEMY, width=2 * SS)
                ld.line((xm, zero, x2, y2), fill=ALLY if v2 >= 0 else ENEMY, width=2 * SS)
            else:
                ld.line((x1, y1, x2, y2), fill=ALLY if (v1 + v2) >= 0 else ENEMY, width=2 * SS)
        ex, ey, ev = pts[-1]
        ld.ellipse((ex - 4 * SS, ey - 4 * SS, ex + 4 * SS, ey + 4 * SS), fill=ALLY if ev >= 0 else ENEMY)
        layer = layer.resize((gx1 - gx0 + 12, gy1 - gy0 + 12), Image.LANCZOS)
        im.paste(layer, (ox, oy), layer)
        return im

    # ── GIF: 팔레트 하나 · 바탕은 점 섞기, 바뀐 곳은 점 없이 ──
    speed = SEC_PER_MIN * min(1.0, 34 * 60000 / end)
    marks = [e["timestamp"] for e in kills + monsters + buildings]
    times = schedule(marks, end, speed)
    while len(times) > MAX_FLOW_FRAMES:
        speed *= (MAX_FLOW_FRAMES - 2) / len(times)
        times = schedule(marks, end, speed)
    sample = [draw_frame(times[k]) for k in (len(times) // 4, len(times) // 2, len(times) * 3 // 4, -1)]
    ui = Image.new("RGB", (WIDTH - MAP - MAP_X, HEIGHT * len(sample)))
    for n, im in enumerate(sample):  # 지도 바깥: 원화 바탕 · 글자 · 판
        ui.paste(im.crop((MAP_X + MAP, 0, WIDTH, HEIGHT)), (0, HEIGHT * n))
    sprites = Image.new("RGB", (CHAMP_D * len(ps), CHAMP_D * 2), BG)
    for n, pid in enumerate(ps):
        sprites.paste(map_sprite[pid], (n * CHAMP_D, 0), map_sprite[pid])
        sprites.paste(roster[pid].resize((CHAMP_D, CHAMP_D)), (n * CHAMP_D, CHAMP_D), roster[pid].resize((CHAMP_D, CHAMP_D)))
    palette = shared_palette([(base.crop((MAP_X, MAP_Y, MAP_X + MAP, MAP_Y + MAP)), 48), (ui, 96), (sprites, 86)],
                             [ALLY, ENEMY, WHITE, GOLD, LABEL, HIGHLIGHT])
    base_idx = base.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
    # 지도는 면이 평평해 점을 섞지 않는다: 아이콘이 지나간 자리가 다시 칠해질 때 작게 저장된다
    flat = base.crop((MAP_X, MAP_Y, MAP_X + MAP, MAP_Y + MAP))
    base_idx.paste(flat.quantize(palette=palette, dither=Image.Dither.NONE), (MAP_X, MAP_Y))
    drawn.clear()
    indexed = []
    for n, t in enumerate(times):
        im = draw_frame(t)
        q = im.quantize(palette=palette, dither=Image.Dither.NONE)
        r, g, b = ImageChops.difference(im, base).split()
        same = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v == 0 else 0)
        q.paste(base_idx, mask=same)
        indexed.append(q)
        if n % 200 == 0:
            print(f"rendered {n + 1}/{len(times)}", flush=True)
    data = encode_bounded(indexed)
    drawn.clear()
    preview = draw_frame(max((e["timestamp"] for e in kills if label.get(id(e)) == "트리플 킬"), default=end * 0.7) + 600)
    return data, preview
