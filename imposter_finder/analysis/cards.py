"""리포트 이미지 공통 도구 (Pillow). 디스코드 카드 안에서 크게 보이도록 모든 이미지는 폭 1000, 가로로 길게."""
from __future__ import annotations

import urllib.request
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"  # index 4 SemiBold, 6 Bold
WIDTH, PAD = 1000, 32
BG, PANEL, TRACK = (43, 45, 49), (54, 57, 63), (64, 66, 73)
GOLD, RED, GREEN, BLUE = (240, 178, 50), (237, 66, 69), (87, 242, 135), (88, 101, 242)
LABEL, WHITE = (181, 186, 193), (255, 255, 255)


@lru_cache(maxsize=None)
def font(size: int, weight: int = 4) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size, index=weight)


def canvas(height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, height), BG)
    return img, ImageDraw.Draw(img)


def png(img: Image.Image) -> bytes:
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


@lru_cache(maxsize=512)
def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "imposter-finder/0.1"})
    return urllib.request.urlopen(req, timeout=20).read()


def icon(url: str | None, size: int, radius: int = 12) -> Image.Image | None:
    """둥근 모서리 아이콘. 받을 수 없으면 None (이미지 없이 그린다)."""
    if not url:
        return None
    try:
        im = Image.open(BytesIO(_download(url))).convert("RGBA")
    except Exception:
        return None
    # 비율을 유지하며 정사각형에 맞춘다
    im.thumbnail((size, size))
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    square.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    alpha = Image.composite(square.getchannel("A"), Image.new("L", (size, size), 0), mask)
    square.putalpha(alpha)
    return square


def white_icon(url: str, size: int) -> Image.Image | None:
    """팀 색이 들어간 아이콘을 흰 실루엣으로 바꾼다."""
    src = icon(url, size, radius=0)
    if src is None:
        return None
    out = Image.new("RGBA", (size, size), WHITE + (0,))
    out.putalpha(src.getchannel("A"))
    return out


def shade(color: tuple, k: float = 0.35) -> tuple:
    return tuple(int(c * k + b * (1 - k)) for c, b in zip(color, BG))


def pill(draw: ImageDraw.ImageDraw, center_x: float, top: float, text: str, fill: tuple, size: int = 16) -> None:
    f = font(size, 6)
    w = draw.textlength(text, font=f)
    draw.rounded_rectangle((center_x - w / 2 - 10, top, center_x + w / 2 + 10, top + size + 10), radius=(size + 10) // 2,
                           fill=fill)
    draw.text((center_x, top + 4), text, font=f, fill=BG, anchor="ma")


def split_bar(draw: ImageDraw.ImageDraw, x0: float, x1: float, y: float, left: float, right: float,
              left_color: tuple = GREEN, right_color: tuple = RED, height: int = 10) -> None:
    """한 줄 막대를 두 값의 비율로 나눈다."""
    draw.rounded_rectangle((x0, y, x1, y + height), radius=height // 2, fill=TRACK)
    total = left + right
    if not total:
        return
    split = x0 + (x1 - x0) * left / total
    if left:
        draw.rounded_rectangle((x0, y, max(split - 2, x0 + height), y + height), radius=height // 2, fill=left_color)
    if right:
        draw.rounded_rectangle((min(split + 2, x1 - height), y, x1, y + height), radius=height // 2, fill=right_color)


def grade_color(grade: int) -> tuple:
    """FC 강화 등급 색: 2~4 동, 5~7 은, 8~10 금, 11~13 보석."""
    if grade >= 11:
        return (120, 220, 255)
    if grade >= 8:
        return GOLD
    if grade >= 5:
        return (200, 205, 212)
    if grade >= 2:
        return (205, 127, 50)
    return LABEL


def backdrop(img: Image.Image, url: str | None, box: tuple[int, int, int, int], dim: float = 0.78,
             focus_y: float = 0.15) -> None:
    """box 영역에 원화를 꽉 채워 깔고 배경색으로 어둡게 한다. focus_y는 세로 위치(0 위 ~ 1 아래)."""
    if not url:
        return
    try:
        art = Image.open(BytesIO(_download(url))).convert("RGB")
    except Exception:
        return
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    scale = max(w / art.width, h / art.height)
    art = art.resize((int(art.width * scale) + 1, int(art.height * scale) + 1))
    left = (art.width - w) // 2
    top = int((art.height - h) * focus_y)
    art = art.crop((left, top, left + w, top + h))
    img.paste(Image.blend(art, Image.new("RGB", (w, h), BG), dim), (x0, y0))
