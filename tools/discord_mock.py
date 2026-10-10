"""README용: 리포트 메시지를 디스코드에 올라온 모습처럼 한 장으로 (다크 테마, 2배 해상도).

봇 이름 → 멘션 → 왼쪽 색 띠 컨테이너(이미지 갤러리 + 총평/평가 + 버튼). GIF가 있으면 같은 장면 수로 함께 움직인다.
"""
from __future__ import annotations

import re
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from imposter_finder.analysis.cards import font, gif

S = 2  # README에서 width=600으로 보여 레티나에서도 선명하게
BG, CARD, TEXT, MUTED = (49, 51, 56), (43, 45, 49), (219, 222, 225), (148, 155, 164)
NAME, MENTION_BG, MENTION = (242, 243, 245), (60, 66, 112), (201, 205, 251)
BUTTON, BLURPLE = (78, 80, 88), (88, 101, 242)
W, LEFT, CARD_W, PAD, GAP = 600, 72, 512, 12, 4


def _f(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return font(size * S, 6 if bold else 4)


def _wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            test = f"{line} {word}".strip()
            if draw.textlength(test, font=f) <= width or not line:
                line = test
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _blocks(payload: dict) -> tuple[int, str, str | None]:
    """(색 띠, 총평·평가 글, 버튼 글자)."""
    container = next(c for c in payload["components"] if c.get("type") == 17)
    text = "\n\n".join(c["content"] for c in container["components"] if c.get("type") == 10)
    button = next((b["label"] for c in container["components"] if c.get("type") == 1 for b in c["components"]), None)
    return container.get("accent_color", 0), text, button


def _frames(data: bytes) -> list[Image.Image]:
    im = Image.open(BytesIO(data))
    out = []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        out.append(im.convert("RGB"))
    return out


def _corner_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", (size[0] * 4, size[1] * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius=radius * 4, fill=255)
    return mask.resize(size, Image.LANCZOS)


def render(files: list[tuple], payload: dict, mentions: list[str], time: str = "오늘 오후 9:41") -> tuple[bytes, str]:
    """반환: (이미지, 확장자 gif|png)."""
    accent, text, button = _blocks(payload)
    media = [_frames(data) for _, data, *_ in files]
    count = max(len(m) for m in media)
    inner = (CARD_W - PAD * 2) * S
    scaled = [[f.resize((inner, round(f.height * inner / f.width)), Image.LANCZOS) for f in m] for m in media]

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    body = _f(15)
    lines = []  # (글자, 굵게?)
    for raw in _wrap(probe, re.sub(r"\n{3,}", "\n\n", text), body, inner):
        bold = raw.startswith("**") and raw.endswith("**")
        lines.append((raw.strip("*"), bold))

    line_h = 21 * S
    card_h = (PAD * S + sum(m[0].height for m in scaled) + GAP * S * (len(scaled) - 1)
              + (PAD * S + len(lines) * line_h if lines else 0) + (PAD * S + 32 * S if button else 0) + PAD * S)
    top = 16 * S
    card_y = top + 22 * S + 8 * S + (26 * S if mentions else 0)
    height = card_y + card_h + 16 * S

    base = Image.new("RGB", (W * S, height), BG)
    d = ImageDraw.Draw(base)
    # 봇 프로필·이름·앱 배지·시각
    d.ellipse((16 * S, top, 56 * S, top + 40 * S), fill=(242, 169, 0))
    d.text((36 * S, top + 20 * S), "범", font=_f(18, True), fill=(20, 20, 20), anchor="mm")
    d.text((LEFT * S, top + 10 * S), "범인찾기", font=_f(16, True), fill=NAME, anchor="lm")
    x = LEFT * S + d.textlength("범인찾기", font=_f(16, True)) + 6 * S
    d.rounded_rectangle((x, top + 3 * S, x + 26 * S, top + 17 * S), radius=3 * S, fill=BLURPLE)
    d.text((x + 13 * S, top + 10 * S), "앱", font=_f(10, True), fill=(255, 255, 255), anchor="mm")
    d.text((x + 34 * S, top + 10 * S), time, font=_f(12), fill=MUTED, anchor="lm")
    # 멘션 (@silent라 알림은 안 간다)
    y = top + 22 * S + 4 * S
    if mentions:
        x = LEFT * S
        for name in mentions:
            label = f"@{name}"
            w = d.textlength(label, font=_f(15)) + 4 * S
            d.rounded_rectangle((x, y, x + w, y + 22 * S), radius=3 * S, fill=MENTION_BG)
            d.text((x + 2 * S, y + 11 * S), label, font=_f(15), fill=MENTION, anchor="lm")
            x += w + 4 * S
    # 컨테이너: 왼쪽 색 띠 + 어두운 판
    cx0, cy0 = LEFT * S, card_y
    d.rounded_rectangle((cx0, cy0, cx0 + CARD_W * S, cy0 + card_h), radius=8 * S, fill=CARD)
    d.rounded_rectangle((cx0, cy0, cx0 + 8 * S, cy0 + card_h), radius=8 * S, fill=((accent >> 16) & 255, (accent >> 8) & 255, accent & 255))
    d.rectangle((cx0 + 4 * S, cy0, cx0 + 8 * S, cy0 + card_h), fill=CARD)
    y = cy0 + PAD * S
    slots = []
    for m in scaled:
        slots.append((cx0 + PAD * S, y))
        y += m[0].height + GAP * S
    y += PAD * S - GAP * S
    for line, bold in lines:
        d.text((cx0 + PAD * S, y + line_h / 2), line, font=_f(15, bold), fill=NAME if bold else TEXT, anchor="lm")
        y += line_h
    if button:
        y += PAD * S
        label = f"{button}  ↗"
        w = d.textlength(label, font=_f(14, True)) + 32 * S
        d.rounded_rectangle((cx0 + PAD * S, y, cx0 + PAD * S + w, y + 32 * S), radius=4 * S, fill=BUTTON)
        d.text((cx0 + PAD * S + w / 2, y + 16 * S), label, font=_f(14, True), fill=NAME, anchor="mm")

    masks = [_corner_mask(m[0].size, 4 * S) for m in scaled]
    frames = []
    for i in range(count):
        frame = base.copy()
        for m, mask, slot in zip(scaled, masks, slots):
            frame.paste(m[min(i, len(m) - 1)], slot, mask)
        frames.append(frame)
    if count == 1:
        out = BytesIO()
        frames[0].save(out, "PNG", optimize=True)
        return out.getvalue(), "png"
    return gif(frames, dither=True), "gif"
