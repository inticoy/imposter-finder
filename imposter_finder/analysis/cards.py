"""리포트 이미지 공통 도구 (Pillow). 디스코드 카드 안에서 크게 보이도록 모든 이미지는 폭 1000, 가로로 길게."""
from __future__ import annotations

import urllib.request
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"  # index 4 SemiBold, 6 Bold
WIDTH, PAD = 1000, 32
# 롤 클라이언트 CSS 색 (rcp-fe-lol-match-history / postgame)
BG, PANEL, TRACK, GRID = (1, 10, 19), (30, 35, 40), (60, 60, 65), (30, 40, 45)  # #010A13 #1E2328 #3C3C41 #1E282D
ALLY, ENEMY = (71, 136, 182), (230, 33, 66)  # 상세 그래프 팀 색 #4788B6 #E62142
VICTORY, DEFEAT = (10, 203, 230), (255, 35, 69)  # 전적 승리 #0ACBE6 · 패배 #FF2345
GOLD, HIGHLIGHT, GOLD_DARK = (200, 155, 60), (250, 190, 10), (70, 55, 20)  # #C89B3C · 본인 강조 #FABE0A · #463714
SLATE = (91, 90, 86)  # #5B5A56 1등이 아닌 막대
LABEL, WHITE = (160, 155, 140), (240, 230, 210)  # #A09B8C #F0E6D2
SS = 4  # 막대·그래프는 크게 그려 줄여서 가장자리를 매끄럽게


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
    # 투명한 여백을 잘라내야 아이콘이 칸 크기만큼 보인다
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    # 비율을 유지하며 정사각형에 맞춘다 (작은 아이콘도 키운다)
    ratio = size / max(im.width, im.height)
    im = im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))), Image.LANCZOS)
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


def bar(img: Image.Image, box: tuple, color: tuple, dim_side: str | None = "left") -> None:
    """매끄러운 둥근 막대. dim_side 쪽이 어둡고 반대쪽이 밝은 가로 그라데이션."""
    x0, y0, x1, y1 = (round(v) for v in box)
    h = y1 - y0
    x1 = max(x1, x0 + h)  # 아주 작은 값도 점 하나는 보이게
    w = x1 - x0
    if w <= 0 or h <= 0:
        return
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=h * SS // 2, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    fill = Image.new("RGB", (w, h), color)
    if dim_side:
        ramp = Image.linear_gradient("L").rotate(90).resize((w, h))  # 왼쪽 0 → 오른쪽 255
        if dim_side == "right":
            ramp = ramp.transpose(Image.FLIP_LEFT_RIGHT)
        fill = Image.composite(fill, Image.new("RGB", (w, h), shade(color, 0.45)), ramp)
    img.paste(fill, (x0, y0), mask)


def split_bar(img: Image.Image, x0: float, x1: float, y: float, left: float, right: float, height: int = 6) -> None:
    """줄다리기 막대: 왼쪽 우리 팀, 오른쪽 상대. 앞선 쪽만 밝게, 가운데 눈금."""
    bar(img, (x0, y, x1, y + height), TRACK, dim_side=None)
    total = left + right
    if total:
        split = x0 + (x1 - x0) * left / total
        if left:
            bar(img, (x0, y, split - 2, y + height), ALLY if left >= right else shade(ALLY, 0.4), dim_side="left")
        if right:
            bar(img, (split + 2, y, x1, y + height), ENEMY if right >= left else shade(ENEMY, 0.4), dim_side="right")
    mid = (x0 + x1) / 2
    ImageDraw.Draw(img).line((mid, y - 4, mid, y + height + 3), fill=LABEL, width=1)


def header(draw: ImageDraw.ImageDraw, title: str, legend: list[tuple[tuple, str]]) -> int:
    """제목 · 오른쪽 범례 · 구분선. 내용이 시작할 y를 돌려준다."""
    draw.text((PAD, 20), title, font=font(21, 6), fill=WHITE)
    x = WIDTH - PAD
    for color, text in reversed(legend):
        x -= draw.textlength(text, font=font(15))
        draw.text((x, 23), text, font=font(15), fill=LABEL)
        x -= 18
        draw.rounded_rectangle((x, 26, x + 11, 37), radius=3, fill=color)
        x -= 20
    return 76


def ambient_bg(img: Image.Image, url: str | None, box: tuple[int, int, int, int] | None = None, dim: float = 0.7,
               focus_y: float = 0.3) -> None:
    """원화를 크게 흐려 색감만 남긴 바탕. 위치가 달라도 어색하지 않다."""
    x0, y0, x1, y1 = box or (0, 0, *img.size)
    w, h = x1 - x0, y1 - y0
    try:
        art = Image.open(BytesIO(_download(url))).convert("RGB") if url else None
    except Exception:
        art = None
    if art is None:
        img.paste(Image.new("RGB", (w, h), BG), (x0, y0))
        return
    small = (max(w // 8, 1), max(h // 8, 1))  # 작게 줄여 흐리면 빠르고 더 부드럽다
    scale = max(small[0] / art.width, small[1] / art.height)
    art = art.resize((int(art.width * scale) + 1, int(art.height * scale) + 1), Image.LANCZOS)
    left, top = (art.width - small[0]) // 2, int((art.height - small[1]) * focus_y)
    art = art.crop((left, top, left + small[0], top + small[1])).filter(ImageFilter.GaussianBlur(4))
    art = art.resize((w, h), Image.BICUBIC)
    img.paste(Image.blend(art, Image.new("RGB", (w, h), BG), dim), (x0, y0))


def glass(img: Image.Image, box: tuple, radius: int = 16, alpha: int = 16) -> None:
    """반투명 둥근 판 (선 없이 영역을 나눈다)."""
    x0, y0, x1, y1 = (round(v) for v in box)
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=alpha)
    img.paste(Image.new("RGB", (w, h), WHITE), (x0, y0), mask.resize((w, h), Image.LANCZOS))


def edge_shadow(img: Image.Image, x: int, height: int, width: int = 18, strength: int = 150) -> None:
    """세로 경계 x 오른쪽으로 부드러운 그늘 (선 대신 칸을 나눈다)."""
    ramp = Image.linear_gradient("L").rotate(90).resize((width, height)).point(lambda v: int((255 - v) * strength / 255))
    img.paste(Image.new("RGB", (width, height), BG), (x, 0), ramp)


def medallion(img: Image.Image, cx: float, cy: float, d: int, url: str | None, ring: tuple,
              crop: float = 0.8, ring_w: float = 2.5) -> None:
    """원형 아이콘 + 팀색 고리. 미니맵 아이콘은 자체 테두리가 있어 가운데만 잘라 쓴다. 크게 그려 줄여서 매끄럽게."""
    size = d * SS
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    disc = Image.new("L", (size, size), 0)
    ImageDraw.Draw(disc).ellipse((0, 0, size - 1, size - 1), fill=255)
    layer.paste(Image.new("RGBA", (size, size), BG + (255,)), (0, 0), disc)
    src = None
    if url:
        try:
            src = Image.open(BytesIO(_download(url))).convert("RGBA")
        except Exception:
            src = None
    if src is not None:
        bbox = src.getchannel("A").getbbox()
        if bbox:
            src = src.crop(bbox)
        w, h = src.size
        mx, my = w * (1 - crop) / 2, h * (1 - crop) / 2
        src = src.crop((round(mx), round(my), round(w - mx), round(h - my))).resize((size, size), Image.LANCZOS)
        layer.paste(src, (0, 0), ImageChops.multiply(src.getchannel("A"), disc))
    ImageDraw.Draw(layer).ellipse((0, 0, size - 1, size - 1), outline=ring, width=round(ring_w * SS))
    layer = layer.resize((d, d), Image.LANCZOS)
    img.paste(layer, (round(cx - d / 2), round(cy - d / 2)), layer)


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
             focus_y: float = 0.15, fade: float = 0.0) -> None:
    """box 영역에 원화를 꽉 채워 깔고 배경색으로 어둡게 한다. focus_y는 세로 위치(0 위 ~ 1 아래).
    fade는 아래로 갈수록 더 어둡게 (글자가 많은 아래쪽을 읽기 쉽게)."""
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
    art = Image.blend(art, Image.new("RGB", (w, h), BG), dim)
    if fade:
        ramp = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(v * fade))  # 위 0 → 아래 fade
        art = Image.composite(Image.new("RGB", (w, h), BG), art, ramp)
    img.paste(art, (x0, y0))
