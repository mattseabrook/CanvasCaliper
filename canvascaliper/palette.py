from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .models import PaletteColor
from .utils import clamp_int, dist2, luminance, rgb_to_hex, rgb_to_hsl, saturation_approx


def image_pixels(image: QImage) -> list[tuple[int, int, int, int]]:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = converted.width()
    height = converted.height()
    bytes_per_line = converted.bytesPerLine()
    try:
        raw = bytes(converted.constBits())
        return [
            (
                raw[y * bytes_per_line + x * 4],
                raw[y * bytes_per_line + x * 4 + 1],
                raw[y * bytes_per_line + x * 4 + 2],
                raw[y * bytes_per_line + x * 4 + 3],
            )
            for y in range(height)
            for x in range(width)
        ]
    except Exception:
        pass
    pixels: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            color = converted.pixelColor(x, y)
            pixels.append((color.red(), color.green(), color.blue(), color.alpha()))
    return pixels


def saturate_pixel(r: int, g: int, b: int, factor: float) -> tuple[int, int, int]:
    gray = luminance(r, g, b)
    return (
        clamp_int(round(gray + (r - gray) * factor), 0, 255),
        clamp_int(round(gray + (g - gray) * factor), 0, 255),
        clamp_int(round(gray + (b - gray) * factor), 0, 255),
    )


def blur_pixels(
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    radius: int,
    saturation: float,
) -> list[tuple[int, int, int, int]]:
    if radius <= 0:
        return pixels
    blurred: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            rs = gs = bs = alphas = samples = 0
            for yy in range(max(0, y - radius), min(height, y + radius + 1)):
                base = yy * width
                for xx in range(max(0, x - radius), min(width, x + radius + 1)):
                    r, g, b, a = pixels[base + xx]
                    rs += r
                    gs += g
                    bs += b
                    alphas += a
                    samples += 1
            r = round(rs / samples)
            g = round(gs / samples)
            b = round(bs / samples)
            r, g, b = saturate_pixel(r, g, b, saturation)
            blurred.append((r, g, b, round(alphas / samples)))
    return blurred


def filter_distinct_colors(
    clusters: list[PaletteColor],
    desired_count: int,
    region_mode: bool = False,
) -> list[PaletteColor]:
    if not region_mode:
        selected: list[PaletteColor] = []
        min_dist = 1050
        hue_min = 4
        for cluster in clusters:
            too_close = False
            for chosen in selected:
                distance = dist2((cluster.r, cluster.g, cluster.b), (chosen.r, chosen.g, chosen.b))
                hue_delta = abs(cluster.h - chosen.h)
                wrapped_hue = min(hue_delta, 360.0 - hue_delta)
                lum_delta = abs(cluster.lum - chosen.lum)
                if distance < min_dist and wrapped_hue < hue_min and lum_delta < 12:
                    too_close = True
                    break
            if not too_close:
                selected.append(cluster)
            if len(selected) >= desired_count:
                break
        if len(selected) < desired_count:
            for cluster in clusters:
                if cluster not in selected:
                    selected.append(cluster)
                if len(selected) >= desired_count:
                    break
        return selected[:desired_count]

    if not clusters:
        return []
    selected: list[PaletteColor] = []
    remaining = clusters[:]

    def add_unique(candidate: PaletteColor | None) -> None:
        if candidate is None or candidate in selected:
            return
        selected.append(candidate)
        if candidate in remaining:
            remaining.remove(candidate)

    add_unique(min(clusters, key=lambda item: item.lum))
    add_unique(max(clusters, key=lambda item: item.lum))
    add_unique(max(clusters, key=lambda item: item.sat))
    add_unique(clusters[0])

    while len(selected) < desired_count and remaining:
        best: PaletteColor | None = None
        best_score = -math.inf
        for cluster in remaining:
            nearest_rgb = math.inf
            nearest_hue = math.inf
            nearest_lum = math.inf
            nearest_sat = math.inf
            for chosen in selected:
                nearest_rgb = min(
                    nearest_rgb,
                    dist2((cluster.r, cluster.g, cluster.b), (chosen.r, chosen.g, chosen.b)),
                )
                hue_delta = abs(cluster.h - chosen.h)
                nearest_hue = min(nearest_hue, min(hue_delta, 360.0 - hue_delta))
                nearest_lum = min(nearest_lum, abs(cluster.lum - chosen.lum))
                nearest_sat = min(nearest_sat, abs(cluster.sat - chosen.sat))
            score = (
                nearest_rgb * 0.65
                + nearest_hue * 240
                + nearest_lum * 110
                + nearest_sat * 18
                + cluster.score * 2.5
            )
            if score > best_score:
                best_score = score
                best = cluster
        add_unique(best)
    return selected[:desired_count]


def compute_palette_from_image(
    image: QImage,
    color_count: int = 32,
    region_mode: bool = False,
) -> list[PaletteColor]:
    if image.isNull():
        return []
    color_count = clamp_int(round(color_count), 8, 96)
    max_dim = 88 if region_mode else 96
    scale = min(1.0, max_dim / max(1, image.width(), image.height()))
    width = max(24, round(image.width() * scale))
    height = max(24, round(image.height() * scale))
    scaled = image.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_RGBA8888)

    try:
        raw_bytes = bytes(scaled.constBits())
        bytes_per_line = scaled.bytesPerLine()
        pixel_iter = (
            (
                raw_bytes[y * bytes_per_line + x * 4],
                raw_bytes[y * bytes_per_line + x * 4 + 1],
                raw_bytes[y * bytes_per_line + x * 4 + 2],
                raw_bytes[y * bytes_per_line + x * 4 + 3],
            )
            for y in range(height)
            for x in range(width)
        )
    except Exception:
        pixel_iter = iter(image_pixels(scaled))

    bins: dict[int, list[float]] = {}
    for r, g, b, a in pixel_iter:
        if a < 20:
            continue
        sat = saturation_approx(r, g, b)
        lum = luminance(r, g, b)
        useful = 1.0 + sat / 72.0 + (255.0 - abs(lum - 132.0)) / 360.0
        key = (r >> 3) << 10 | (g >> 3) << 5 | (b >> 3)
        bucket = bins.get(key)
        if bucket is None:
            bins[key] = [r * useful, g * useful, b * useful, useful, 1.0]
        else:
            bucket[0] += r * useful
            bucket[1] += g * useful
            bucket[2] += b * useful
            bucket[3] += useful
            bucket[4] += 1.0

    if not bins:
        return []

    clusters: list[PaletteColor] = []
    for rs, gs, bs, weight, count in bins.values():
        if weight <= 0:
            continue
        r = clamp_int(round(rs / weight), 0, 255)
        g = clamp_int(round(gs / weight), 0, 255)
        b = clamp_int(round(bs / weight), 0, 255)
        sat = saturation_approx(r, g, b)
        lum = luminance(r, g, b)
        hue, hsl_sat, light = rgb_to_hsl(r, g, b)
        score = math.sqrt(weight) * 8.0 + sat * 1.8 + (255.0 - abs(lum - 128.0)) * 0.25
        clusters.append(
            PaletteColor(
                r=r,
                g=g,
                b=b,
                count=int(count),
                weight=float(weight),
                hex=rgb_to_hex(r, g, b),
                sat=sat,
                lum=lum,
                score=score,
                h=hue,
                s=hsl_sat,
                light=light,
            )
        )
    clusters.sort(key=lambda item: item.score, reverse=True)
    candidates = clusters[: max(96, color_count * 10)]
    distinct = filter_distinct_colors(candidates, color_count, region_mode)
    return sorted(distinct, key=lambda item: item.lum)


