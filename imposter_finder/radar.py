from __future__ import annotations

import struct
import zlib
from math import cos, pi, sin


def render_radar_png(axes: dict[str, int]) -> bytes:
    """Render a compact six-axis radar chart without third-party dependencies."""
    width, height = 720, 460
    pixels = bytearray([15, 23, 42, 255]) * (width * height)
    center_x, center_y, radius = width // 2, height // 2 + 10, 170
    labels = list(axes)
    angles = [-pi / 2 + index * 2 * pi / len(labels) for index in range(len(labels))]

    for level in (0.25, 0.5, 0.75, 1.0):
        points = [_point(center_x, center_y, radius * level, angle) for angle in angles]
        _polygon_outline(pixels, width, height, points, (56, 71, 99, 255))
    for angle in angles:
        _line(pixels, width, height, (center_x, center_y), _point(center_x, center_y, radius, angle), (56, 71, 99, 255))

    values = [max(0, min(100, int(axes[label]))) / 100 for label in labels]
    data_points = [_point(center_x, center_y, radius * value, angle) for value, angle in zip(values, angles)]
    _fill_polygon(pixels, width, height, data_points, (42, 188, 180, 92))
    _polygon_outline(pixels, width, height, data_points, (65, 225, 210, 255), thickness=3)
    for point in data_points:
        _circle(pixels, width, height, point[0], point[1], 5, (231, 255, 252, 255))
    return _png(width, height, pixels)


def _point(x: int, y: int, radius: float, angle: float) -> tuple[int, int]:
    return round(x + cos(angle) * radius), round(y + sin(angle) * radius)


def _set(pixels: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        index = (y * width + x) * 4
        alpha = color[3] / 255
        for channel in range(3):
            pixels[index + channel] = round(pixels[index + channel] * (1 - alpha) + color[channel] * alpha)
        pixels[index + 3] = 255


def _line(pixels: bytearray, width: int, height: int, start: tuple[int, int], end: tuple[int, int], color: tuple[int, int, int, int], thickness: int = 1) -> None:
    x0, y0 = start
    x1, y1 = end
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for step in range(steps + 1):
        x = round(x0 + (x1 - x0) * step / steps)
        y = round(y0 + (y1 - y0) * step / steps)
        for offset_x in range(-(thickness // 2), thickness // 2 + 1):
            for offset_y in range(-(thickness // 2), thickness // 2 + 1):
                _set(pixels, width, height, x + offset_x, y + offset_y, color)


def _polygon_outline(pixels: bytearray, width: int, height: int, points: list[tuple[int, int]], color: tuple[int, int, int, int], thickness: int = 1) -> None:
    for index, point in enumerate(points):
        _line(pixels, width, height, point, points[(index + 1) % len(points)], color, thickness)


def _fill_polygon(pixels: bytearray, width: int, height: int, points: list[tuple[int, int]], color: tuple[int, int, int, int]) -> None:
    min_y = max(0, min(point[1] for point in points))
    max_y = min(height - 1, max(point[1] for point in points))
    for y in range(min_y, max_y + 1):
        intersections: list[float] = []
        for index, (x1, y1) in enumerate(points):
            x2, y2 = points[(index + 1) % len(points)]
            if (y1 <= y < y2) or (y2 <= y < y1):
                intersections.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        intersections.sort()
        for start, end in zip(intersections[::2], intersections[1::2]):
            for x in range(max(0, round(start)), min(width, round(end) + 1)):
                _set(pixels, width, height, x, y, color)


def _circle(pixels: bytearray, width: int, height: int, x: int, y: int, radius: int, color: tuple[int, int, int, int]) -> None:
    for offset_y in range(-radius, radius + 1):
        for offset_x in range(-radius, radius + 1):
            if offset_x * offset_x + offset_y * offset_y <= radius * radius:
                _set(pixels, width, height, x + offset_x, y + offset_y, color)


def _png(width: int, height: int, pixels: bytearray) -> bytes:
    rows = b"".join(b"\x00" + bytes(pixels[y * width * 4 : (y + 1) * width * 4]) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
