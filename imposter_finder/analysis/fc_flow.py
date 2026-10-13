"""FC 온라인 경기 흐름 GIF: 슈팅을 시간 순서로 다시 보여 준다.

넥슨 API에서 시간이 찍힌 기록은 슈팅뿐이라 (패스·위치 추적 없음) 슈팅 장면만 움직인다.
장면마다: 시계가 슈팅 시각으로 → 어시스트 위치에서 슈터로 패스선 → 골문으로 공 → 결과.
아래 띠는 두 팀의 누적 xG. 마지막 장면은 xG 크기 슈팅맵으로 멈춘다.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from imposter_finder.analysis.cards import GIF_MAX_BYTES, PAD, SS, WIDTH, font, icon
from imposter_finder.analysis.fc_report import (BG, LABEL, LINE, PITCH, PITCH_DARK, SHOT_GOAL, SHOT_ON, WHITE, Team,
                                                _stage, goals)
from imposter_finder.analysis.fc_xg import shot_label, shot_xg

FRAME_MS, FIRST_MS, LAST_MS = 100, 900, 4500
CLOCK_FRAMES, PASS_FRAMES, BALL_FRAMES, RESULT_FRAMES = 3, 4, 4, 9

TOP, PITCH_Y = 0, 96  # 점수판 높이 · 경기장 시작
PW = WIDTH - PAD * 2
PH = round(PW * 68 / 105)
STRIP_Y = PITCH_Y + PH + 22  # 누적 xG 띠
HEIGHT = STRIP_Y + 74
GOLD = (240, 180, 41)


def _minute(goal_time: int) -> float:
    """goalTime 상위 비트 = 구간(전반·후반·연장), 하위 24비트 = 그 구간의 초."""
    half, seconds = goal_time >> 24, goal_time & 0xFFFFFF
    return {0: 0, 1: 45, 2: 90, 3: 105, 4: 120}.get(half, 0) + seconds / 60


def _pitch() -> Image.Image:
    """가로 경기장 (105×68m). 줄무늬 + 반투명 흰 선."""
    w, h = PW * SS, PH * SS
    big = Image.new("RGB", (w, h), PITCH)
    d = ImageDraw.Draw(big)
    for i in range(0, 14, 2):
        d.rectangle((i * w / 14, 0, (i + 1) * w / 14, h), fill=PITCH_DARK)
    lines = Image.new("L", big.size, 0)
    ld = ImageDraw.Draw(lines)
    lw = 2 * SS
    sx, sy = (w - 1) / 105, (h - 1) / 68
    ld.rectangle((0, 0, w - 1, h - 1), outline=110, width=lw)
    ld.line((w / 2, 0, w / 2, h), fill=110, width=lw)
    ld.ellipse((w / 2 - 9.15 * sx, h / 2 - 9.15 * sy, w / 2 + 9.15 * sx, h / 2 + 9.15 * sy), outline=110, width=lw)
    for left in (True, False):
        for depth, half_w in ((16.5, 20.16), (5.5, 9.16)):
            x0, x1 = (0, depth * sx) if left else (w - 1 - depth * sx, w - 1)
            ld.rectangle((x0, h / 2 - half_w * sy, x1, h / 2 + half_w * sy), outline=110, width=lw)
        gx = 0 if left else w - 3 * SS
        ld.rectangle((gx, h / 2 - 3.66 * sy, gx + 3 * SS, h / 2 + 3.66 * sy), fill=200)  # 골대
    big.paste(Image.new("RGB", big.size, LINE), (0, 0), lines)
    return big.resize((PW, PH), Image.LANCZOS)


def _point(x: float, y: float, attacks_right: bool) -> tuple[float, float]:
    """API 좌표(x: 자기 골대 0 → 상대 골대 1, y: 공격 방향 왼쪽 0 → 오른쪽 1) → 화면.
    오른쪽으로 공격하면 공격수의 왼쪽이 화면 위다."""
    if not attacks_right:
        x, y = 1 - x, 1 - y
    return PAD + min(max(x, 0), 1) * PW, PITCH_Y + min(max(y, 0), 1) * PH


def _radius(xg: float) -> float:
    return 6 + xg ** 0.5 * 13


def render(left: Team, right: Team, names: dict[int, str]) -> bytes:
    """각 팀의 슈팅이 자기 이름 쪽에 모이게: left는 왼쪽 골문, right는 오른쪽 골문을 공격한다.
    names: spId → 짧은 이름."""
    shots = []
    for team, attacks_right in ((left, False), (right, True)):
        for s in team.side.get("shootDetail", []):
            shots.append({**s, "team": team, "right": attacks_right, "minute": _minute(s["goalTime"]),
                          "xg": shot_xg(s)})
    shots.sort(key=lambda s: s["minute"])
    end = max([90.0] + [s["minute"] for s in shots])

    base = _stage(HEIGHT, left, right)
    base.paste(_pitch(), (PAD, PITCH_Y))
    dr = ImageDraw.Draw(base)
    for team, x, anchor in ((left, PAD, "la"), (right, WIDTH - PAD, "ra")):
        crest = icon(team.crest, 40, radius=0)
        tx = x
        if crest:
            img_x = x if anchor == "la" else x - 40
            base.paste(crest, (img_x, 18), crest)
            tx = x + 50 if anchor == "la" else x - 50
        dr.text((tx, 20), team.nickname, font=font(20, 6), fill=WHITE, anchor=anchor)
    # 누적 xG 띠의 축
    sx0, sx1, sy0, sy1 = PAD + 40, WIDTH - PAD, STRIP_Y, STRIP_Y + 52
    total = max(sum(s["xg"] for s in shots if s["team"] is left), sum(s["xg"] for s in shots if s["team"] is right), 1)
    dr.text((PAD, sy0 + 26), "xG", font=font(14), fill=LABEL, anchor="lm")
    dr.line((sx0, sy1, sx1, sy1), fill=(60, 66, 76), width=1)
    for m in (0, 45, 90):
        dr.text((sx0 + (sx1 - sx0) * m / end, sy1 + 6), f"{m}'", font=font(12), fill=LABEL, anchor="ma")
    sx = lambda m: sx0 + (sx1 - sx0) * min(m, end) / end
    sy = lambda v: sy1 - (sy1 - sy0) * v / total

    def frame(now: float, upto: int, phase: str | None = None, k: float = 1.0, final: bool = False) -> Image.Image:
        """now: 시계(분), upto: 다 보여 준 슈팅 수, phase: 진행 중인 슈팅의 단계."""
        im = base.copy()
        layer = Image.new("RGBA", (WIDTH * SS, HEIGHT * SS), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        circle = lambda cx, cy, r, **kw: ld.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), **kw)
        score = {id(left): 0, id(right): 0}
        for s in shots[:upto]:  # 끝난 슈팅: 크기 = xG
            cx, cy = _point(s["x"], s["y"], s["right"])
            c, r = s["team"].color, _radius(s["xg"])
            if s["result"] == SHOT_GOAL:
                score[id(s["team"])] += 1
                circle(cx, cy, r, fill=c + (255,), outline=WHITE + (255,), width=2 * SS)
            elif s["result"] == SHOT_ON:
                circle(cx, cy, r * .85, outline=c + (255,), width=3 * SS)
            else:
                circle(cx, cy, r * .7, fill=c + (90,))
            if final and s["result"] == SHOT_GOAL and s.get("assist") and s.get("assistX") is not None:
                ax, ay = _point(s["assistX"], s["assistY"], s["right"])
                ld.line((ax * SS, ay * SS, cx * SS, cy * SS), fill=c + (80,), width=SS * 2)
        current = shots[upto] if phase else None
        if current:
            c = current["team"].color
            cx, cy = _point(current["x"], current["y"], current["right"])
            has_pass = current.get("assist") and current.get("assistX") is not None
            if has_pass:
                ax, ay = _point(current["assistX"], current["assistY"], current["right"])
                t = k if phase == "pass" else 1
                ld.line((ax * SS, ay * SS, (ax + (cx - ax) * t) * SS, (ay + (cy - ay) * t) * SS),
                        fill=c + (230,), width=3 * SS)
                circle(ax, ay, 5, fill=c + (255,))
            if phase in ("ball", "result"):
                goal_x = PAD + (PW if current["right"] else 0)
                spread = (current["y"] - .5) * 6.5 / 68 * PH * (1 if current["right"] else -1)
                if current["result"] == SHOT_GOAL:
                    gy = PITCH_Y + PH / 2 + spread
                elif current["result"] == SHOT_ON:  # 막힘: 골라인 앞에서 멈춘다
                    goal_x += -18 if current["right"] else 18
                    gy = PITCH_Y + PH / 2 + spread
                else:  # 빗나감: 골대 바깥으로
                    gy = PITCH_Y + PH / 2 + (1 if current["y"] >= .5 else -1) * (5.5 / 68 * PH) * (1 if current["right"] else -1)
                t = k if phase == "ball" else 1
                bx, by = cx + (goal_x - cx) * t, cy + (gy - cy) * t
                ld.line((cx * SS, cy * SS, bx * SS, by * SS), fill=(GOLD if current["result"] == SHOT_GOAL else WHITE) + (220,),
                        width=3 * SS)
                circle(bx, by, 6, fill=WHITE + (255,), outline=BG + (255,), width=SS)
            circle(cx, cy, _radius(current["xg"]), fill=c + (255,), outline=WHITE + (255,), width=2 * SS)
        layer = layer.resize((WIDTH, HEIGHT), Image.LANCZOS)
        im.paste(layer, (0, 0), layer)
        d = ImageDraw.Draw(im)
        # 점수판: 진행 중인 골은 결과 단계에서 올린다
        if current and phase == "result" and current["result"] == SHOT_GOAL:
            score[id(current["team"])] += 1
        d.text((WIDTH / 2, 12), f"{score[id(left)]} : {score[id(right)]}", font=font(44, 6), fill=WHITE, anchor="ma")
        d.text((WIDTH / 2, 66), "경기 종료" if final else f"{int(now)}'", font=font(15), fill=LABEL, anchor="ma")
        for team, x, anchor in ((left, PAD, "la"), (right, WIDTH - PAD, "ra")):
            xg = sum(s["xg"] for s in shots[:upto] if s["team"] is team)
            if current and current["team"] is team and phase == "result":
                xg += current["xg"]
            tx = x + (50 if anchor == "la" else -50) if team.crest else x
            d.text((tx, 50), f"xG {xg:.1f}", font=font(15), fill=LABEL, anchor=anchor)
        if current:  # 슈터 · 종류 · xG, 그리고 결과
            cx, cy = _point(current["x"], current["y"], current["right"])
            who = names.get(current["spId"], "")
            kind = shot_label(current)
            text = " · ".join(v for v in (who, kind, f"xG {current['xg']:.2f}") if v)
            ty = cy - _radius(current["xg"]) - 12
            tx = min(max(cx, PAD + 140), WIDTH - PAD - 140)
            d.text((tx, ty), text, font=font(16, 6), fill=WHITE, anchor="ms", stroke_width=3, stroke_fill=PITCH_DARK)
            if phase == "pass" and current.get("assist") and current.get("assistSpId"):
                ax, ay = _point(current["assistX"], current["assistY"], current["right"])
                d.text((ax, ay + 12), names.get(current["assistSpId"], ""), font=font(14, 6), fill=WHITE, anchor="ma",
                       stroke_width=3, stroke_fill=PITCH_DARK)
            if phase == "result":
                word = ("골대" if current.get("hitPost") else "")
                word = {SHOT_GOAL: "골!", SHOT_ON: "선방"}.get(current["result"], word or "빗나감")
                color = current["team"].color if current["result"] == SHOT_GOAL else WHITE
                d.text((WIDTH / 2, PITCH_Y + 26), word, font=font(30 if current["result"] == SHOT_GOAL else 22, 6),
                       fill=color, anchor="ma", stroke_width=4, stroke_fill=PITCH_DARK)
        # 누적 xG: 지금 시각까지
        for team in (left, right):
            v, pts = 0.0, [(sx(0), sy(0))]
            done = shots[:upto] + ([current] if current and phase == "result" else [])
            for s in done:
                if s["team"] is team:
                    pts += [(sx(s["minute"]), sy(v))]
                    v += s["xg"]
                    pts += [(sx(s["minute"]), sy(v))]
            pts.append((sx(end if final else now), sy(v)))
            d.line(pts, fill=team.color, width=2)
        return im

    frames: list[tuple[Image.Image, int]] = [(frame(0, 0), FIRST_MS)]
    clock = 0.0
    for n, s in enumerate(shots):
        for i in range(1, CLOCK_FRAMES + 1):
            frames.append((frame(clock + (s["minute"] - clock) * i / CLOCK_FRAMES, n), FRAME_MS))
        clock = s["minute"]
        if s.get("assist") and s.get("assistX") is not None:
            for i in range(1, PASS_FRAMES + 1):
                frames.append((frame(clock, n, "pass", i / PASS_FRAMES), FRAME_MS))
        for i in range(1, BALL_FRAMES + 1):
            frames.append((frame(clock, n, "ball", i / BALL_FRAMES), FRAME_MS))
        frames.append((frame(clock, n, "result"), FRAME_MS * RESULT_FRAMES))
    frames.append((frame(end, len(shots), final=True), LAST_MS))
    return _encode(base, frames)


def _encode(base: Image.Image, frames: list[tuple[Image.Image, int]]) -> bytes:
    """한 팔레트: 바탕(원화 빛·잔디)은 점 섞기, 바뀐 곳은 점 없이. 같은 픽셀은 바탕 값을 그대로."""
    sample = Image.new("RGB", (WIDTH, HEIGHT * 3))
    for n, (im, _) in enumerate((frames[len(frames) // 3], frames[2 * len(frames) // 3], frames[-1])):
        sample.paste(im, (0, HEIGHT * n))
    palette = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    base_idx = base.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
    indexed = []
    for im, _ in frames:
        q = im.quantize(palette=palette, dither=Image.Dither.NONE)
        r, g, b = ImageChops.difference(im, base).split()
        same = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v == 0 else 0)
        q.paste(base_idx, mask=same)
        indexed.append(q)
    out = BytesIO()
    indexed[0].save(out, "GIF", save_all=True, append_images=indexed[1:], duration=[d for _, d in frames], loop=0,
                    optimize=True)
    data = out.getvalue()
    if len(data) > GIF_MAX_BYTES:
        raise ValueError("FC 경기 흐름 GIF 용량 초과")
    return data
