from __future__ import annotations

import math


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def clamp_int(value: int, low: int, high: int) -> int:
    return min(high, max(low, value))


def snap_quarter(value: float) -> float:
    return round(value * 4.0) / 4.0


def round2(value: float) -> float:
    return round(value * 100.0) / 100.0


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def safe_emit(signal, *args) -> None:
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


def luminance(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def saturation_approx(r: int, g: int, b: int) -> int:
    return max(r, g, b) - min(r, g, b)


def dist2(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dr = a[0] - b[0]
    dg = a[1] - b[1]
    db = a[2] - b[2]
    return dr * dr + dg * dg + db * db


def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a or 1


def format_fraction_only(value: float, denominator: int = 8) -> str:
    rounded = round(value * denominator)
    rem = rounded % denominator
    if rem == 0:
        return str(math.floor(rounded / denominator))
    div = gcd(rem, denominator)
    return f"{rem // div}/{denominator // div}"


def rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    rf = r / 255.0
    gf = g / 255.0
    bf = b / 255.0
    max_c = max(rf, gf, bf)
    min_c = min(rf, gf, bf)
    light = (max_c + min_c) / 2.0
    if max_c == min_c:
        return 0.0, 0.0, light
    delta = max_c - min_c
    sat = delta / (2.0 - max_c - min_c) if light > 0.5 else delta / (max_c + min_c)
    if max_c == rf:
        hue = (gf - bf) / delta + (6.0 if gf < bf else 0.0)
    elif max_c == gf:
        hue = (bf - rf) / delta + 2.0
    else:
        hue = (rf - gf) / delta + 4.0
    return hue / 6.0 * 360.0, sat, light


