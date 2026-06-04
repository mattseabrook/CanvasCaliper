from __future__ import annotations

import math
import os
import struct
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSettings,
    QSize,
    Signal,
    Slot,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)


MAX_ZOOM = 32.0
ZOOM_STEP = 1.25
SETTINGS_VERSION = "canvascaliper-settings-v7-pyside"


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


def modal_ruler_tick_meta(inch: float) -> tuple[int, float, str, float, QFont.Weight]:
    eps = 0.0001
    is_whole = abs(inch - round(inch)) < eps
    is_half = abs(inch * 2 - round(inch * 2)) < eps
    is_quarter = abs(inch * 4 - round(inch * 4)) < eps
    is_eighth = abs(inch * 8 - round(inch * 8)) < eps
    is_sixteenth = abs(inch * 16 - round(inch * 16)) < eps
    if is_whole:
        return 22, 1.0, str(round(inch)), 11.0, QFont.Weight.Black
    if is_half:
        return 17, 0.9, "1/2", 8.0, QFont.Weight.ExtraBold
    if is_quarter:
        frac = "1/4" if abs((inch % 1.0) - 0.25) < eps else "3/4"
        return 13, 0.78, frac, 6.0, QFont.Weight.Bold
    if is_eighth:
        return 11, 0.72, "", 0.0, QFont.Weight.Bold
    if is_sixteenth:
        return 7, 0.52, "", 0.0, QFont.Weight.DemiBold
    return 4, 0.34, "", 0.0, QFont.Weight.DemiBold


@dataclass
class AppState:
    image_width_in: float = 30.0
    image_height_in: float = 40.0
    image_ppi: float = 100.0
    canvas_width_in: float = 29.75
    canvas_height_in: float = 39.5
    grid_size_in: float = 2.0
    major_every: int = 0
    offset_x_in: float = 0.0
    offset_y_in: float = 0.0
    view_pan_x_in: float = 0.0
    view_pan_y_in: float = 0.0
    zoom_scale: float = 1.0
    palette_count: int = 32
    panel_x: float = 8.0
    panel_y: float = 8.0
    palette_x: float = -1.0
    palette_y: float = 8.0
    palette_docked_right: int = 1


@dataclass
class SourceInfo:
    width: int
    height: int
    detected_ppi: float
    ppi_source: str
    path: str


@dataclass
class CanvasLayout:
    ruler_thickness: float = 28.0
    origin_x: float = 0.0
    origin_y: float = 0.0
    fit_px_per_in: float = 1.0
    px_per_in: float = 1.0
    content_min_x_in: float = 0.0
    content_min_y_in: float = 0.0
    content_max_x_in: float = 1.0
    content_max_y_in: float = 1.0


@dataclass
class Region:
    cell_x: int
    cell_y: int
    left_in: float
    top_in: float
    width_in: float
    height_in: float
    cells_x: int = 1
    cells_y: int = 1


@dataclass
class CursorInfo:
    x_in: float
    y_in: float
    cell_x: int
    cell_y: int
    local_x_thou: int
    local_y_thou: int
    units_per_cell: int


@dataclass
class PaletteColor:
    r: int
    g: int
    b: int
    count: int
    weight: float
    hex: str
    sat: int
    lum: float
    score: float
    h: float
    s: float
    light: float


def parse_png_phys(path: str) -> tuple[float, float, int, int] | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return None
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        if chunk_type == b"pHYs" and length >= 9:
            ppm_x, ppm_y, unit = struct.unpack(">IIB", chunk_data[:9])
            if unit == 1 and ppm_x > 0 and ppm_y > 0:
                return ppm_x * 0.0254, ppm_y * 0.0254, ppm_x, ppm_y
        offset += 12 + length
    return None


def qcolor_alpha(r: int, g: int, b: int, alpha_float: float) -> QColor:
    return QColor(r, g, b, clamp_int(round(alpha_float * 255), 0, 255))


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


class PaletteWorkerSignals(QObject):
    finished = Signal(int, object)
    error = Signal(int, str)


class PaletteWorker(QRunnable):
    def __init__(self, job_id: int, image: QImage, color_count: int, region_mode: bool) -> None:
        super().__init__()
        self.job_id = job_id
        self.image = image
        self.color_count = color_count
        self.region_mode = region_mode
        self.signals = PaletteWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            colors = compute_palette_from_image(self.image, self.color_count, self.region_mode)
        except Exception as exc:
            safe_emit(self.signals.error, self.job_id, str(exc))
            return
        safe_emit(self.signals.finished, self.job_id, colors)


def crop_region_image(
    image: QImage,
    region: Region,
    source_width: int,
    source_height: int,
    image_width_in: float,
    image_height_in: float,
    offset_x_in: float,
    offset_y_in: float,
) -> QImage | None:
    crop_left = max(region.left_in, offset_x_in)
    crop_top = max(region.top_in, offset_y_in)
    crop_right = min(region.left_in + region.width_in, offset_x_in + image_width_in)
    crop_bottom = min(region.top_in + region.height_in, offset_y_in + image_height_in)
    if crop_right <= crop_left or crop_bottom <= crop_top:
        return None
    sx = ((crop_left - offset_x_in) / image_width_in) * source_width
    sy = ((crop_top - offset_y_in) / image_height_in) * source_height
    sw = ((crop_right - crop_left) / image_width_in) * source_width
    sh = ((crop_bottom - crop_top) / image_height_in) * source_height
    rect = QRect(
        clamp_int(round(sx), 0, source_width - 1),
        clamp_int(round(sy), 0, source_height - 1),
        max(1, round(sw)),
        max(1, round(sh)),
    )
    rect = rect.intersected(QRect(0, 0, source_width, source_height))
    if rect.isEmpty():
        return None
    return image.copy(rect)


class RegionPaletteWorker(QRunnable):
    def __init__(
        self,
        job_id: int,
        image: QImage,
        region: Region,
        source_width: int,
        source_height: int,
        image_width_in: float,
        image_height_in: float,
        offset_x_in: float,
        offset_y_in: float,
        color_count: int,
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self.image = image
        self.region = region
        self.source_width = source_width
        self.source_height = source_height
        self.image_width_in = image_width_in
        self.image_height_in = image_height_in
        self.offset_x_in = offset_x_in
        self.offset_y_in = offset_y_in
        self.color_count = color_count
        self.signals = PaletteWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            cropped = crop_region_image(
                self.image,
                self.region,
                self.source_width,
                self.source_height,
                self.image_width_in,
                self.image_height_in,
                self.offset_x_in,
                self.offset_y_in,
            )
            colors = [] if cropped is None else compute_palette_from_image(cropped, self.color_count, True)
        except Exception as exc:
            safe_emit(self.signals.error, self.job_id, str(exc))
            return
        safe_emit(self.signals.finished, self.job_id, colors)


class RegionPreviewWorkerSignals(QObject):
    finished = Signal(int, object, object)
    error = Signal(int, str)


class RegionPreviewWorker(QRunnable):
    def __init__(
        self,
        job_id: int,
        image: QImage,
        region: Region,
        source_width: int,
        source_height: int,
        image_width_in: float,
        image_height_in: float,
        offset_x_in: float,
        offset_y_in: float,
        scale: float,
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self.image = image
        self.region = region
        self.source_width = source_width
        self.source_height = source_height
        self.image_width_in = image_width_in
        self.image_height_in = image_height_in
        self.offset_x_in = offset_x_in
        self.offset_y_in = offset_y_in
        self.scale = scale
        self.signals = RegionPreviewWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            crop_left = max(self.region.left_in, self.offset_x_in)
            crop_top = max(self.region.top_in, self.offset_y_in)
            crop_right = min(self.region.left_in + self.region.width_in, self.offset_x_in + self.image_width_in)
            crop_bottom = min(self.region.top_in + self.region.height_in, self.offset_y_in + self.image_height_in)
            if crop_right <= crop_left or crop_bottom <= crop_top:
                safe_emit(self.signals.finished, self.job_id, QImage(), None)
                return
            cropped = crop_region_image(
                self.image,
                self.region,
                self.source_width,
                self.source_height,
                self.image_width_in,
                self.image_height_in,
                self.offset_x_in,
                self.offset_y_in,
            )
            if cropped is None or cropped.isNull():
                safe_emit(self.signals.finished, self.job_id, QImage(), None)
                return
            target_w = max(1, round((crop_right - crop_left) * self.scale))
            target_h = max(1, round((crop_bottom - crop_top) * self.scale))
            preview = cropped.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            bounds = (crop_left, crop_top, crop_right - crop_left, crop_bottom - crop_top)
        except Exception as exc:
            safe_emit(self.signals.error, self.job_id, str(exc))
            return
        safe_emit(self.signals.finished, self.job_id, preview, bounds)


class ImageLoadWorkerSignals(QObject):
    finished = Signal(int, object, object, bool, bool)
    error = Signal(int, str)


class ImageLoadWorker(QRunnable):
    def __init__(
        self,
        job_id: int,
        path: str,
        persist: bool,
        apply_specs: bool,
        fallback_ppi: float,
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self.path = path
        self.persist = persist
        self.apply_specs = apply_specs
        self.fallback_ppi = fallback_ppi
        self.signals = ImageLoadWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            if not self.path.lower().endswith(".png"):
                raise ValueError("Please load a PNG")
            image = QImage(self.path)
            if image.isNull():
                raise ValueError("Could not load PNG")
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            phys = parse_png_phys(self.path)
            detected_ppi = (phys[0] + phys[1]) / 2.0 if phys else self.fallback_ppi
            ppi_source = f"PNG pHYs {phys[0]:.2f}x{phys[1]:.2f}" if phys else "fallback/manual"
            source = SourceInfo(
                width=image.width(),
                height=image.height(),
                detected_ppi=detected_ppi,
                ppi_source=ppi_source,
                path=self.path,
            )
        except Exception as exc:
            safe_emit(self.signals.error, self.job_id, str(exc))
            return
        safe_emit(self.signals.finished, self.job_id, image, source, self.persist, self.apply_specs)


class TitleBar(QWidget):
    def __init__(self, title: str, parent_panel: "FloatingPanel") -> None:
        super().__init__(parent_panel)
        self.title = title
        self.panel = parent_panel
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor("#d9dbe1"))
        for y in range(0, rect.height(), 6):
            painter.fillRect(QRect(0, y + 2, rect.width(), 2), QColor("#c7cbd3"))
            painter.fillRect(QRect(0, y + 4, rect.width(), 2), QColor("#e3e5ea"))
        painter.setPen(QColor("#ffffff"))
        painter.drawLine(0, 0, rect.width(), 0)
        painter.drawLine(0, 0, 0, rect.height())
        painter.setPen(QColor("#575c66"))
        painter.drawLine(0, rect.height() - 1, rect.width(), rect.height() - 1)
        painter.drawLine(rect.width() - 1, 0, rect.width() - 1, rect.height())

        painter.fillRect(QRect(10, 6, 6, 10), QColor("#8b9099"))
        painter.fillRect(QRect(18, 6, 6, 10), QColor("#8b9099"))
        painter.fillRect(QRect(26, 6, 6, 10), QColor("#8b9099"))
        font = QFont("Liberation Mono", 8)
        font.setWeight(QFont.Weight.Black)
        painter.setFont(font)
        painter.setPen(QColor("#12161d"))
        painter.drawText(QRect(46, 0, rect.width() - 50, rect.height()), Qt.AlignmentFlag.AlignVCenter, self.title)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.panel.start_drag(event.globalPosition().toPoint())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.panel.drag_to(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.panel.end_drag()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()


class FloatingPanel(QFrame):
    def __init__(
        self,
        title: str,
        parent: QWidget,
        on_moved: Callable[[float, float], None],
        on_user_drag_started: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("floatingPanel")
        self.on_moved = on_moved
        self.on_user_drag_started = on_user_drag_started
        self._drag_origin: QPoint | None = None
        self._panel_origin: QPoint | None = None
        self.title_bar = TitleBar(title, self)
        self.body = QWidget(self)
        self.body.setObjectName("panelBody")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.body)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def start_drag(self, global_pos: QPoint) -> None:
        self._drag_origin = global_pos
        self._panel_origin = self.pos()
        self.raise_()
        if self.on_user_drag_started is not None:
            self.on_user_drag_started()

    def drag_to(self, global_pos: QPoint) -> None:
        if self._drag_origin is None or self._panel_origin is None:
            return
        delta = global_pos - self._drag_origin
        next_pos = self._panel_origin + delta
        next_pos = self.clamped_pos(next_pos)
        self.move(next_pos)
        self.on_moved(float(next_pos.x()), float(next_pos.y()))

    def end_drag(self) -> None:
        snapped = self.clamped_pos(self.pos())
        if snapped != self.pos():
            self.move(snapped)
        self.on_moved(float(snapped.x()), float(snapped.y()))
        self._drag_origin = None
        self._panel_origin = None

    def clamped_pos(self, pos: QPoint) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return pos
        max_x = max(8, parent.width() - self.width() - 8)
        max_y = max(8, parent.height() - self.height() - 8)
        return QPoint(round(clamp(pos.x(), 8, max_x)), round(clamp(pos.y(), 8, max_y)))


class ColorBox(QFrame):
    def __init__(self, color: str = "#000000", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("colorBox")
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            "QFrame#colorBox {"
            f"background: {color};"
            "border-top: 1px solid rgba(255,255,255,51);"
            "border-left: 1px solid rgba(255,255,255,51);"
            "border-right: 1px solid rgba(0,0,0,245);"
            "border-bottom: 1px solid rgba(0,0,0,245);"
            "}"
        )


class SwatchTile(QFrame):
    def __init__(self, color: PaletteColor, total: int, daub: bool = False) -> None:
        super().__init__()
        self.setObjectName("mixTile" if daub else "swatchTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(6)
        if daub:
            color_widget = DaubWidget(QColor(color.r, color.g, color.b), self)
            color_widget.setMinimumHeight(40)
        else:
            color_widget = QFrame(self)
            color_widget.setObjectName("swatchColor")
            color_widget.setMinimumHeight(48)
            color_widget.setStyleSheet(
                "QFrame#swatchColor {"
                f"background: rgb({color.r}, {color.g}, {color.b});"
                "border-bottom: 1px solid rgba(0,0,0,191);"
                "}"
            )
        layout.addWidget(color_widget)
        pct = (color.count / max(1, total)) * 100.0
        meta = QLabel(f"<b>{color.hex}</b><br>rgb({color.r}, {color.g}, {color.b})<br>{pct:.1f}%", self)
        meta.setObjectName("swatchMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)
        self.setMinimumHeight(92 if daub else 84)


class DaubWidget(QWidget):
    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = color
        self.setMinimumHeight(40)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 3, -1, -4)
        path = QPainterPath()
        w = rect.width()
        h = rect.height()
        x = rect.x()
        y = rect.y()
        points = [
            (0.01, 0.47),
            (0.04, 0.29),
            (0.08, 0.20),
            (0.15, 0.14),
            (0.26, 0.10),
            (0.40, 0.09),
            (0.55, 0.11),
            (0.70, 0.14),
            (0.82, 0.19),
            (0.92, 0.28),
            (0.98, 0.43),
            (0.99, 0.56),
            (0.96, 0.70),
            (0.89, 0.81),
            (0.78, 0.88),
            (0.63, 0.92),
            (0.46, 0.93),
            (0.29, 0.90),
            (0.16, 0.85),
            (0.07, 0.76),
            (0.02, 0.63),
        ]
        first = True
        for px, py in points:
            point = QPointF(x + px * w, y + py * h)
            if first:
                path.moveTo(point)
                first = False
            else:
                path.lineTo(point)
        path.closeSubpath()
        painter.fillPath(path, self.color)
        painter.setPen(QPen(QColor(255, 255, 255, 42), 1))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(255, 255, 255, 18))


class CanvasWidget(QWidget):
    def __init__(self, main_window: "CanvasCaliperWindow") -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.state = main_window.state
        self.layout_info = CanvasLayout()
        self.image: QImage | None = None
        self.source_info: SourceInfo | None = None
        self.active_region: Region | None = None
        self.select_region: Region | None = None
        self.pan_active = False
        self.pan_start = QPoint()
        self.pan_base_x = 0.0
        self.pan_base_y = 0.0
        self.select_active = False
        self.select_started = False
        self.select_start_cell: Region | None = None
        self.select_start_pos = QPoint()
        self.click_candidate: tuple[QPoint, Region] | None = None
        self.last_pointer = QPoint()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_image(self, image: QImage | None, source_info: SourceInfo | None) -> None:
        self.image = image
        self.source_info = source_info
        self.update()

    def recompute_layout(self) -> CanvasLayout:
        state = self.state
        ruler = 28.0
        content_min_x = min(0.0, state.offset_x_in)
        content_min_y = min(0.0, state.offset_y_in)
        content_max_x = max(state.canvas_width_in, state.offset_x_in + state.image_width_in)
        content_max_y = max(state.canvas_height_in, state.offset_y_in + state.image_height_in)
        content_w = max(0.01, content_max_x - content_min_x)
        content_h = max(0.01, content_max_y - content_min_y)
        edge_pad = 24.0
        avail_w = max(80.0, self.width() - ruler - edge_pad * 2.0)
        avail_h = max(80.0, self.height() - ruler - edge_pad * 2.0)
        fit_px_per_in = min(avail_w / content_w, avail_h / content_h)
        px_per_in = fit_px_per_in * state.zoom_scale
        total_w = content_w * px_per_in + ruler
        total_h = content_h * px_per_in + ruler
        stage_left = round((self.width() - total_w) / 2.0)
        stage_top = round((self.height() - total_h) / 2.0)
        origin_x = stage_left + ruler - content_min_x * px_per_in + state.view_pan_x_in * px_per_in
        origin_y = stage_top + ruler - content_min_y * px_per_in + state.view_pan_y_in * px_per_in
        self.layout_info = CanvasLayout(
            ruler_thickness=ruler,
            origin_x=origin_x,
            origin_y=origin_y,
            fit_px_per_in=fit_px_per_in,
            px_per_in=px_per_in,
            content_min_x_in=content_min_x,
            content_min_y_in=content_min_y,
            content_max_x_in=content_max_x,
            content_max_y_in=content_max_y,
        )
        return self.layout_info

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.draw_background(painter)
        self.recompute_layout()
        if self.image is not None and self.source_info is not None:
            self.draw_image(painter)
            self.draw_overlay(painter)
        else:
            self.draw_empty_state(painter)

    def draw_background(self, painter: QPainter) -> None:
        rect = self.rect()
        painter.fillRect(rect, QColor("#030407"))
        painter.setPen(QPen(QColor(255, 0, 72, 14), 1))
        for x in range(0, rect.width(), 32):
            painter.drawLine(x, 0, x, rect.height())
        painter.setPen(QPen(QColor(38, 214, 230, 10), 1))
        for y in range(0, rect.height(), 32):
            painter.drawLine(0, y, rect.width(), y)

        magenta = QRadialGradient(QPointF(rect.width() * 0.18, rect.height() * 0.12), rect.width() * 0.28)
        magenta.setColorAt(0, QColor(177, 60, 255, 33))
        magenta.setColorAt(1, QColor(177, 60, 255, 0))
        painter.fillRect(rect, magenta)
        red = QRadialGradient(QPointF(rect.width() * 0.82, rect.height() * 0.86), rect.width() * 0.34)
        red.setColorAt(0, QColor(194, 0, 23, 38))
        red.setColorAt(1, QColor(194, 0, 23, 0))
        painter.fillRect(rect, red)

    def draw_empty_state(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        title_font = QFont("Liberation Mono", 34)
        title_font.setWeight(QFont.Weight.Black)
        body_font = QFont("Liberation Mono", 10)
        rect = QRectF(240, 0, max(80, self.width() - 560), self.height())
        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, 224))
        title_rect = QRectF(rect.x(), self.height() / 2 - 52, rect.width(), 48)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, "Drop PNG")
        painter.setFont(body_font)
        painter.setPen(QColor("#98a2ad"))
        body = (
            "Left-click opens a cell. Left-click drag selects a square region. "
            "Right-click drag pans the zoomed work view. Modal palette is computed "
            "from the exact region you clicked."
        )
        painter.drawText(
            QRectF(rect.x(), self.height() / 2 + 2, rect.width(), 70),
            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            body,
        )

    def draw_image(self, painter: QPainter) -> None:
        if self.image is None:
            return
        layout = self.layout_info
        state = self.state
        img_rect = QRectF(
            layout.origin_x + state.offset_x_in * layout.px_per_in,
            layout.origin_y + state.offset_y_in * layout.px_per_in,
            state.image_width_in * layout.px_per_in,
            state.image_height_in * layout.px_per_in,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(img_rect, self.image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def draw_overlay(self, painter: QPainter) -> None:
        layout = self.layout_info
        state = self.state
        px_per_in = layout.px_per_in
        ox = layout.origin_x
        oy = layout.origin_y
        canvas_w = state.canvas_width_in * px_per_in
        canvas_h = state.canvas_height_in * px_per_in
        canvas_right = ox + canvas_w
        canvas_bottom = oy + canvas_h
        ruler = layout.ruler_thickness
        max_ruler_x = max(state.canvas_width_in, state.offset_x_in + state.image_width_in, 0.0)
        max_ruler_y = max(state.canvas_height_in, state.offset_y_in + state.image_height_in, 0.0)

        painter.fillRect(QRectF(ox, oy - ruler, max_ruler_x * px_per_in, ruler), QColor(0, 0, 0, 46))
        painter.fillRect(QRectF(ox - ruler, oy, ruler, max_ruler_y * px_per_in), QColor(0, 0, 0, 46))

        painter.setPen(QPen(QColor("#c20017"), 1.2))
        painter.drawRect(QRectF(ox, oy, canvas_w, canvas_h))

        grid_step = max(0.001, state.grid_size_in) * px_per_in
        full_cols = math.floor(state.canvas_width_in / max(0.001, state.grid_size_in))
        full_rows = math.floor(state.canvas_height_in / max(0.001, state.grid_size_in))
        for i in range(full_cols + 1):
            x = ox + i * grid_step
            major = state.major_every > 0 and i % state.major_every == 0
            painter.setPen(QPen(qcolor_alpha(155, 0, 14, 0.95 if major else 0.8), 1.8 if major else 0.9))
            painter.drawLine(QPointF(x, oy), QPointF(x, canvas_bottom))
        if full_cols * state.grid_size_in < state.canvas_width_in - 0.0001:
            painter.setPen(QPen(qcolor_alpha(194, 0, 23, 0.95), 1.1))
            painter.drawLine(QPointF(canvas_right, oy), QPointF(canvas_right, canvas_bottom))
        for i in range(full_rows + 1):
            y = oy + i * grid_step
            major = state.major_every > 0 and i % state.major_every == 0
            painter.setPen(QPen(qcolor_alpha(155, 0, 14, 0.95 if major else 0.8), 1.8 if major else 0.9))
            painter.drawLine(QPointF(ox, y), QPointF(canvas_right, y))
        if full_rows * state.grid_size_in < state.canvas_height_in - 0.0001:
            painter.setPen(QPen(qcolor_alpha(194, 0, 23, 0.95), 1.1))
            painter.drawLine(QPointF(ox, canvas_bottom), QPointF(canvas_right, canvas_bottom))

        label_font = QFont("Liberation Mono", 7)
        label_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(label_font)
        painter.setPen(QColor("#9b000e"))
        quarter = 0.25
        for i in range(math.floor(max_ruler_x / quarter) + 1):
            inch = i * quarter
            x = ox + inch * px_per_in
            length = 4
            width = 1.0
            opacity = 0.55
            if abs(inch - round(inch)) < 0.0001:
                length = 14
                width = 1.15
                opacity = 1.0
            elif abs(inch * 2 - round(inch * 2)) < 0.0001:
                length = 9
                width = 1.02
                opacity = 0.8
            painter.setPen(QPen(qcolor_alpha(155, 0, 14, opacity), width))
            painter.drawLine(QPointF(x, oy - length), QPointF(x, oy))
            if abs(inch - round(inch)) < 0.0001:
                painter.setPen(QColor("#9b000e"))
                painter.drawText(QRectF(x - 18, oy - 27, 36, 11), Qt.AlignmentFlag.AlignCenter, str(round(inch)))

        for i in range(math.floor(max_ruler_y / quarter) + 1):
            inch = i * quarter
            y = oy + inch * px_per_in
            length = 4
            width = 1.0
            opacity = 0.55
            if abs(inch - round(inch)) < 0.0001:
                length = 14
                width = 1.15
                opacity = 1.0
            elif abs(inch * 2 - round(inch * 2)) < 0.0001:
                length = 9
                width = 1.02
                opacity = 0.8
            painter.setPen(QPen(qcolor_alpha(155, 0, 14, opacity), width))
            painter.drawLine(QPointF(ox - length, y), QPointF(ox, y))
            if abs(inch - round(inch)) < 0.0001:
                painter.setPen(QColor("#9b000e"))
                painter.drawText(QRectF(ox - 38, y - 6, 26, 12), Qt.AlignmentFlag.AlignRight, str(round(inch)))

        if self.select_region is not None:
            region = self.select_region
            selection_rect = QRectF(
                ox + region.left_in * px_per_in,
                oy + region.top_in * px_per_in,
                region.width_in * px_per_in,
                region.height_in * px_per_in,
            )
            painter.fillRect(selection_rect, QColor(194, 0, 23, 36))
            pen = QPen(QColor("#ff3048"), 1.5)
            pen.setDashPattern([6, 4])
            painter.setPen(pen)
            painter.drawRect(selection_rect)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#c20017"))
        painter.drawEllipse(QPointF(ox, oy), 2.3, 2.3)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def get_absolute_inches(self, pos: QPoint) -> tuple[float, float]:
        layout = self.recompute_layout()
        return (
            (pos.x() - layout.origin_x) / layout.px_per_in,
            (pos.y() - layout.origin_y) / layout.px_per_in,
        )

    def get_cursor_grid_info(self, pos: QPoint) -> CursorInfo | None:
        if self.source_info is None:
            return None
        x_in, y_in = self.get_absolute_inches(pos)
        state = self.state
        if x_in < 0 or y_in < 0 or x_in > state.canvas_width_in or y_in > state.canvas_height_in:
            return None
        grid_size = max(0.001, state.grid_size_in)
        cell_x = math.floor(x_in / grid_size)
        cell_y = math.floor(y_in / grid_size)
        local_x_in = x_in - cell_x * grid_size
        local_y_in = y_in - cell_y * grid_size
        units_per_cell = round(grid_size * 1000)
        return CursorInfo(
            x_in=x_in,
            y_in=y_in,
            cell_x=cell_x,
            cell_y=cell_y,
            local_x_thou=clamp_int(round(local_x_in * 1000), 0, max(0, units_per_cell - 1)),
            local_y_thou=clamp_int(round(local_y_in * 1000), 0, max(0, units_per_cell - 1)),
            units_per_cell=units_per_cell,
        )

    def get_grid_cell_at(self, pos: QPoint) -> Region | None:
        info = self.get_cursor_grid_info(pos)
        if info is None:
            return None
        grid_size = max(0.001, self.state.grid_size_in)
        left = info.cell_x * grid_size
        top = info.cell_y * grid_size
        width = min(grid_size, self.state.canvas_width_in - left)
        height = min(grid_size, self.state.canvas_height_in - top)
        if width <= 0 or height <= 0:
            return None
        return Region(info.cell_x, info.cell_y, left, top, width, height, 1, 1)

    def square_region_from_cells(self, start_cell: Region, end_cell: Region) -> Region:
        grid_size = max(0.001, self.state.grid_size_in)
        max_cols = math.ceil(self.state.canvas_width_in / grid_size)
        max_rows = math.ceil(self.state.canvas_height_in / grid_size)
        dx = end_cell.cell_x - start_cell.cell_x
        dy = end_cell.cell_y - start_cell.cell_y
        cells = max(abs(dx), abs(dy)) + 1
        left_cell = start_cell.cell_x - cells + 1 if dx < 0 else start_cell.cell_x
        top_cell = start_cell.cell_y - cells + 1 if dy < 0 else start_cell.cell_y
        left_cell = clamp_int(left_cell, 0, max_cols - 1)
        top_cell = clamp_int(top_cell, 0, max_rows - 1)
        cells = min(cells, max_cols - left_cell, max_rows - top_cell)
        left = left_cell * grid_size
        top = top_cell * grid_size
        width = min(cells * grid_size, self.state.canvas_width_in - left)
        height = min(cells * grid_size, self.state.canvas_height_in - top)
        return Region(left_cell, top_cell, left, top, width, height, cells, cells)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.source_info is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.pan_active = True
            self.pan_start = event.position().toPoint()
            self.pan_base_x = self.state.view_pan_x_in
            self.pan_base_y = self.state.view_pan_y_in
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            start_cell = self.get_grid_cell_at(event.position().toPoint())
            if start_cell is not None:
                self.select_active = True
                self.select_started = False
                self.select_start_cell = start_cell
                self.select_start_pos = event.position().toPoint()
                self.click_candidate = (self.select_start_pos, start_cell)
                event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        pos = event.position().toPoint()
        self.last_pointer = pos
        if not self.pan_active:
            self.main_window.update_cursor_readout(pos, self.get_cursor_grid_info(pos))
            self.main_window.update_pixel_probe_from_pos(pos)

        if self.pan_active:
            dx_in = (pos.x() - self.pan_start.x()) / max(0.001, self.layout_info.px_per_in)
            dy_in = (pos.y() - self.pan_start.y()) / max(0.001, self.layout_info.px_per_in)
            self.state.view_pan_x_in = self.pan_base_x + dx_in
            self.state.view_pan_y_in = self.pan_base_y + dy_in
            self.main_window.save_settings()
            self.update()
            self.main_window.update_cursor_readout(pos, self.get_cursor_grid_info(pos))
            self.main_window.update_pixel_probe_from_pos(pos)
            event.accept()
            return

        if self.select_active and self.select_start_cell is not None:
            moved = math.hypot(pos.x() - self.select_start_pos.x(), pos.y() - self.select_start_pos.y())
            end_cell = self.get_grid_cell_at(pos)
            if moved >= 4 and end_cell is not None:
                self.select_started = True
                self.select_region = self.square_region_from_cells(self.select_start_cell, end_cell)
                self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.RightButton and self.pan_active:
            self.pan_active = False
            self.unsetCursor()
            self.main_window.save_settings()
            self.update()
            self.main_window.refresh_info_panel()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.select_active:
            region: Region | None = None
            if self.select_started and self.select_region is not None:
                region = self.select_region
            elif self.click_candidate is not None:
                click_pos, click_region = self.click_candidate
                if math.hypot(pos.x() - click_pos.x(), pos.y() - click_pos.y()) < 4:
                    region = click_region
            self.select_active = False
            self.select_started = False
            self.select_start_cell = None
            self.select_region = None
            self.click_candidate = None
            self.update()
            if region is not None:
                self.main_window.open_region_dialog(region)
            event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.main_window.zoom_in()
            else:
                self.main_window.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".png"):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".png"):
                self.main_window.load_png(url.toLocalFile(), persist=True, apply_specs=True)
                event.acceptProposedAction()
                return


class RegionCanvas(QWidget):
    def __init__(self, dialog: "RegionDialog") -> None:
        super().__init__(dialog)
        self.dialog = dialog
        self.view: tuple[float, float, float, float, float] | None = None
        self.crosshair: QPointF | None = None
        self.preview_image: QImage | None = None
        self.preview_bounds: tuple[float, float, float, float] | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(420, 360)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background: #050609;")

    def compute_view_geometry(self) -> tuple[float, float, float, float, float]:
        region = self.dialog.region
        ruler_top = 42.0
        ruler_left = 58.0
        pad = 12.0
        inner_w = max(1.0, self.width() - ruler_left - pad * 2.0)
        inner_h = max(1.0, self.height() - ruler_top - pad * 2.0)
        scale = min(inner_w / region.width_in, inner_h / region.height_in)
        draw_w = region.width_in * scale
        draw_h = region.height_in * scale
        ox = round(ruler_left + 10.0)
        oy = round(ruler_top + 10.0)
        return ox, oy, draw_w, draw_h, scale

    def set_preview(self, preview: QImage, bounds: tuple[float, float, float, float] | None) -> None:
        self.preview_image = None if preview.isNull() else preview
        self.preview_bounds = bounds
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, QColor("#050609"))
        painter.setPen(QPen(QColor(255, 0, 72, 9), 1))
        for x in range(0, rect.width(), 24):
            painter.drawLine(x, 0, x, rect.height())
        painter.setPen(QPen(QColor(38, 214, 230, 6), 1))
        for y in range(0, rect.height(), 24):
            painter.drawLine(0, y, rect.width(), y)

        region = self.dialog.region
        state = self.dialog.window.state
        ox, oy, draw_w, draw_h, scale = self.compute_view_geometry()
        self.view = (ox, oy, draw_w, draw_h, scale)

        ruler_top = 42.0
        ruler_left = 58.0
        painter.fillRect(QRectF(ox, oy - ruler_top + 2, draw_w, ruler_top - 2), QColor(0, 0, 0, 71))
        painter.fillRect(QRectF(ox - ruler_left + 2, oy, ruler_left - 2, draw_h), QColor(0, 0, 0, 71))

        if self.preview_image is not None and self.preview_bounds is not None:
            crop_left, crop_top, crop_w, crop_h = self.preview_bounds
            dx = ox + (crop_left - region.left_in) * scale
            dy = oy + (crop_top - region.top_in) * scale
            dw = crop_w * scale
            dh = crop_h * scale
            painter.drawImage(QRectF(dx, dy, dw, dh), self.preview_image)
        else:
            painter.setPen(QColor("#a7adba"))
            painter.drawText(
                QRectF(ox, oy, draw_w, draw_h),
                Qt.AlignmentFlag.AlignCenter,
                "Preparing region image...",
            )

        painter.setPen(QPen(QColor("#c20017"), 1))
        painter.drawRect(QRectF(ox, oy, draw_w, draw_h))
        grid_size = max(0.001, state.grid_size_in)
        x = grid_size
        while x < region.width_in - 0.0001:
            px = ox + x * scale
            painter.drawLine(QPointF(px, oy), QPointF(px, oy + draw_h))
            x += grid_size
        y = grid_size
        while y < region.height_in - 0.0001:
            py = oy + y * scale
            painter.drawLine(QPointF(ox, py), QPointF(ox + draw_w, py))
            y += grid_size

        tick_step = 1 / 32
        i = 0
        while i <= round(region.width_in / tick_step):
            inch = i * tick_step
            px = ox + inch * scale
            length, alpha, label, font_size, weight = modal_ruler_tick_meta(inch)
            painter.setPen(QPen(qcolor_alpha(194, 0, 23, alpha), 1))
            painter.drawLine(QPointF(px, oy - length), QPointF(px, oy))
            self.draw_ruler_label(painter, label, px, oy - 30, font_size, weight, Qt.AlignmentFlag.AlignCenter)
            i += 1
        i = 0
        while i <= round(region.height_in / tick_step):
            inch = i * tick_step
            py = oy + inch * scale
            length, alpha, label, font_size, weight = modal_ruler_tick_meta(inch)
            painter.setPen(QPen(qcolor_alpha(194, 0, 23, alpha), 1))
            painter.drawLine(QPointF(ox - length, py), QPointF(ox, py))
            self.draw_ruler_label(painter, label, ox - 28, py, font_size, weight, Qt.AlignmentFlag.AlignRight)
            i += 1

        if self.crosshair is not None:
            painter.setPen(QPen(QColor("#ff001f"), 1))
            x = self.crosshair.x()
            y = self.crosshair.y()
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.drawRect(QRectF(x - 6, y - 6, 12, 12))

    def draw_ruler_label(
        self,
        painter: QPainter,
        label: str,
        x: float,
        y: float,
        font_size: float,
        weight: QFont.Weight,
        alignment: Qt.AlignmentFlag,
    ) -> None:
        if not label:
            return
        font = QFont("Liberation Mono", max(6, round(font_size)))
        font.setWeight(weight)
        painter.setFont(font)
        painter.setPen(QColor("#c20017"))
        rect = QRectF(x - 20, y - 7, 40, 14) if alignment == Qt.AlignmentFlag.AlignCenter else QRectF(x - 40, y - 7, 38, 14)
        painter.drawText(rect, alignment | Qt.AlignmentFlag.AlignVCenter, label)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self.view is None:
            return
        pos = event.position()
        ox, oy, draw_w, draw_h, scale = self.view
        if pos.x() < ox or pos.y() < oy or pos.x() > ox + draw_w or pos.y() > oy + draw_h:
            self.crosshair = None
            self.dialog.hide_cell_cursor()
            self.dialog.reset_live_color()
            self.update()
            return
        self.crosshair = QPointF(pos.x(), pos.y())
        local_x_in = (pos.x() - ox) / scale
        local_y_in = (pos.y() - oy) / scale
        abs_x = self.dialog.region.left_in + local_x_in
        abs_y = self.dialog.region.top_in + local_y_in
        self.dialog.update_live_color(abs_x, abs_y)

        grid_size = max(0.001, self.dialog.window.state.grid_size_in)
        local_cell_x = math.floor(local_x_in / grid_size)
        local_cell_y = math.floor(local_y_in / grid_size)
        x_in_cell = local_x_in - local_cell_x * grid_size
        y_in_cell = local_y_in - local_cell_y * grid_size
        units_per_cell = round(grid_size * 1000)
        x_thou = clamp_int(round(x_in_cell * 1000), 0, max(0, units_per_cell - 1))
        y_thou = clamp_int(round(y_in_cell * 1000), 0, max(0, units_per_cell - 1))
        self.dialog.show_cell_cursor(self.mapToGlobal(event.position().toPoint()), f"X: {x_thou}  Y: {y_thou}")
        self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.crosshair = None
        self.dialog.hide_cell_cursor()
        self.dialog.reset_live_color()
        self.update()


class RegionDialog(QDialog):
    def __init__(self, window: "CanvasCaliperWindow", region: Region) -> None:
        super().__init__(window)
        self.window = window
        self.region = region
        self.setWindowTitle("Grid Region")
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setObjectName("regionDialog")
        self.cell_cursor = QLabel("", self)
        self.cell_cursor.setObjectName("cursorTag")
        self.cell_cursor.hide()
        self._palette_job_id = 0
        self._preview_job_id = 0
        self._started_jobs = False
        self._palette_worker: RegionPaletteWorker | None = None
        self._preview_worker: RegionPreviewWorker | None = None
        self.finished.connect(self.cancel_jobs)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame(self)
        header.setObjectName("modalHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 10, 0)
        self.title_label = QLabel(self.title_text(), header)
        self.title_label.setObjectName("modalTitle")
        hint = QLabel('1/32-inch ruler - labels to 1/8 - blood lines show selected grid units', header)
        hint.setObjectName("mutedText")
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(hint, 1)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.region_canvas = RegionCanvas(self)
        body.addWidget(self.region_canvas, 1)
        side = QFrame(self)
        side.setObjectName("modalPalettePanel")
        side.setFixedWidth(420)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(10)
        title = QLabel("SECTION PALETTE", side)
        title.setObjectName("panelHeading")
        side_layout.addWidget(title)

        live = QFrame(side)
        live.setObjectName("liveColorCard")
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(10, 10, 10, 10)
        live_layout.setSpacing(8)
        self.live_color_box = ColorBox("#050609", live)
        self.live_color_box.setMinimumHeight(208)
        live_layout.addWidget(self.live_color_box)
        self.live_hex = QLabel("-", live)
        self.live_hex.setObjectName("liveHex")
        self.live_rgb = QLabel("Move over the zoomed image", live)
        self.live_rgb.setObjectName("mutedText")
        self.live_pos = QLabel("-", live)
        self.live_pos.setObjectName("mutedText")
        live_layout.addWidget(QLabel("UNDER CURSOR", live))
        live_layout.addWidget(self.live_hex)
        live_layout.addWidget(self.live_rgb)
        live_layout.addWidget(self.live_pos)
        side_layout.addWidget(live)

        self.palette_info = QLabel("Click or select a region.", side)
        self.palette_info.setObjectName("mutedText")
        side_layout.addWidget(self.palette_info)
        scroll = QScrollArea(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.palette_widget = QWidget(scroll)
        self.palette_layout = QGridLayout(self.palette_widget)
        self.palette_layout.setContentsMargins(0, 0, 0, 0)
        self.palette_layout.setSpacing(10)
        scroll.setWidget(self.palette_widget)
        side_layout.addWidget(scroll, 1)
        body.addWidget(side)
        body_widget = QWidget(self)
        body_widget.setLayout(body)
        root.addWidget(body_widget, 1)

        parent_size = window.size()
        self.resize(max(760, round(parent_size.width() * 0.75)), max(520, round(parent_size.height() * 0.75)))
        self.move(
            window.x() + max(0, (parent_size.width() - self.width()) // 2),
            window.y() + max(0, (parent_size.height() - self.height()) // 2),
        )
        self.show_palette_placeholder("Computing section swatches in background...")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._started_jobs:
            return
        self._started_jobs = True
        QTimer.singleShot(0, self.start_preview_job)
        QTimer.singleShot(0, self.start_palette_job)

    def title_text(self) -> str:
        if self.region.cells_x == 1:
            return (
                f"Grid Cell {self.region.cell_x}, {self.region.cell_y} - "
                f'{self.region.width_in:.3f}" x {self.region.height_in:.3f}"'
            )
        return (
            f"Grid Region {self.region.cell_x},{self.region.cell_y} - "
            f"{self.region.cells_x}x{self.region.cells_y} cells - "
            f'{self.region.width_in:.3f}" x {self.region.height_in:.3f}"'
        )

    def show_palette_placeholder(self, text: str) -> None:
        clear_layout(self.palette_layout)
        self.palette_info.setText(text)
        placeholder = QLabel(text, self.palette_widget)
        placeholder.setObjectName("mutedText")
        placeholder.setWordWrap(True)
        self.palette_layout.addWidget(placeholder, 0, 0, 1, 4)

    def start_palette_job(self) -> None:
        if self.window.image is None or self.window.source_info is None:
            self.show_palette_placeholder("No image pixels in this selected region.")
            return
        self._palette_job_id += 1
        count = max(24, min(48, self.window.state.palette_count * 2))
        worker = RegionPaletteWorker(
            self._palette_job_id,
            QImage(self.window.image),
            self.region,
            self.window.source_info.width,
            self.window.source_info.height,
            self.window.state.image_width_in,
            self.window.state.image_height_in,
            self.window.state.offset_x_in,
            self.window.state.offset_y_in,
            count,
        )
        self._palette_worker = worker
        worker.signals.finished.connect(self.populate_palette)
        worker.signals.error.connect(self.palette_error)
        self.window.thread_pool.start(worker)

    def cancel_jobs(self, result: int | None = None) -> None:
        self._palette_job_id += 1
        self._preview_job_id += 1

    def start_preview_job(self) -> None:
        if self.window.image is None or self.window.source_info is None:
            return
        _ox, _oy, _draw_w, _draw_h, scale = self.region_canvas.compute_view_geometry()
        self._preview_job_id += 1
        worker = RegionPreviewWorker(
            self._preview_job_id,
            QImage(self.window.image),
            self.region,
            self.window.source_info.width,
            self.window.source_info.height,
            self.window.state.image_width_in,
            self.window.state.image_height_in,
            self.window.state.offset_x_in,
            self.window.state.offset_y_in,
            scale,
        )
        self._preview_worker = worker
        worker.signals.finished.connect(self.preview_ready)
        worker.signals.error.connect(self.preview_error)
        self.window.thread_pool.start(worker)

    @Slot(int, object, object)
    def preview_ready(
        self,
        job_id: int,
        preview: QImage,
        bounds: tuple[float, float, float, float] | None,
    ) -> None:
        if job_id != self._preview_job_id:
            return
        self.region_canvas.set_preview(preview, bounds)

    @Slot(int, str)
    def preview_error(self, job_id: int, message: str) -> None:
        if job_id == self._preview_job_id:
            self.region_canvas.set_preview(QImage(), None)

    @Slot(int, object)
    def populate_palette(self, job_id: int, colors: list[PaletteColor]) -> None:
        if job_id != self._palette_job_id:
            return
        clear_layout(self.palette_layout)
        if not colors:
            self.palette_info.setText("No image pixels in this selected region.")
            return
        total = sum(color.count for color in colors) or 1
        self.palette_info.setText(f"{len(colors)} distinct section swatches from just this selected region")
        columns = 4
        for index, color in enumerate(colors):
            self.palette_layout.addWidget(SwatchTile(color, total, daub=True), index // columns, index % columns)
        self.palette_layout.setRowStretch((len(colors) + columns - 1) // columns, 1)

    @Slot(int, str)
    def palette_error(self, job_id: int, message: str) -> None:
        if job_id == self._palette_job_id:
            self.show_palette_placeholder(f"Section palette failed: {message}")

    def show_cell_cursor(self, global_pos: QPoint, text: str) -> None:
        local = self.mapFromGlobal(global_pos)
        self.cell_cursor.setText(text)
        self.cell_cursor.adjustSize()
        self.cell_cursor.move(local.x() + 16, local.y() + 18)
        self.cell_cursor.raise_()
        self.cell_cursor.show()

    def hide_cell_cursor(self) -> None:
        self.cell_cursor.hide()

    def reset_live_color(self) -> None:
        self.live_color_box.set_color("#050609")
        self.live_hex.setText("-")
        self.live_rgb.setText("Move over the zoomed image")
        self.live_pos.setText("-")

    def update_live_color(self, abs_x_in: float, abs_y_in: float) -> None:
        sample = self.window.sample_pixel(abs_x_in, abs_y_in)
        if sample is None:
            self.live_color_box.set_color("#050609")
            self.live_hex.setText("outside image")
            self.live_rgb.setText("No source pixel under cursor")
            self.live_pos.setText(f'{abs_x_in:.3f}", {abs_y_in:.3f}"')
            return
        r, g, b, a, px, py = sample
        hex_value = rgb_to_hex(r, g, b)
        self.live_color_box.set_color(hex_value)
        self.live_hex.setText(hex_value)
        self.live_rgb.setText(f"rgb({r}, {g}, {b})  a:{a}")
        self.live_pos.setText(f'px {px}, {py} - {abs_x_in:.3f}", {abs_y_in:.3f}"')


class CanvasCaliperWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CanvasCaliper")
        self.setAcceptDrops(True)
        self.settings_store = QSettings("CanvasCaliper", SETTINGS_VERSION)
        self._restoring_window = True
        self._window_save_timer = QTimer(self)
        self._window_save_timer.setSingleShot(True)
        self._window_save_timer.timeout.connect(self.save_window_placement)
        self.resize(1360, 860)
        self.state = self.load_state()
        self.image: QImage | None = None
        self.source_info: SourceInfo | None = None
        self.palette_colors: list[PaletteColor] = []
        self._syncing = False
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(max(2, min(4, self.thread_pool.maxThreadCount())))
        self._load_job_id = 0
        self._palette_job_id = 0
        self.workspace = CanvasWidget(self)
        self.setCentralWidget(self.workspace)

        self.cursor_tag = QLabel("", self.workspace)
        self.cursor_tag.setObjectName("cursorTag")
        self.cursor_tag.hide()

        self.settings_panel = FloatingPanel("SETTINGS", self.workspace, self.on_settings_panel_moved)
        self.palette_panel = FloatingPanel(
            "PALETTE",
            self.workspace,
            self.on_palette_panel_moved,
            self.on_palette_panel_user_drag_started,
        )
        self.build_settings_panel()
        self.build_palette_panel()
        self.apply_styles()
        self.sync_state_to_controls()
        self.refresh_palette_panel([])
        self.refresh_info_panel()
        QApplication.instance().installEventFilter(self)
        self.restore_window_placement()
        self._restoring_window = False
        QTimer.singleShot(0, self.position_panels)
        QTimer.singleShot(0, self.restore_last_image)

    def load_state(self) -> AppState:
        state = AppState()
        store = self.settings_store
        int_fields = {"major_every", "palette_count", "palette_docked_right"}
        for field in fields(AppState):
            saved = store.value(field.name, None)
            if saved is None:
                continue
            try:
                if field.name in int_fields:
                    setattr(state, field.name, int(float(saved)))
                else:
                    setattr(state, field.name, float(saved))
            except (TypeError, ValueError):
                pass
        state.zoom_scale = clamp(state.zoom_scale or 1.0, 1.0, MAX_ZOOM)
        state.palette_count = clamp_int(round(state.palette_count or 32), 8, 64)
        return state

    def save_settings(self) -> None:
        for field in fields(AppState):
            self.settings_store.setValue(field.name, getattr(self.state, field.name))

    def saved_screen_is_available(self) -> bool:
        saved_screen = self.settings_store.value("window_screen_name", "", str)
        if not saved_screen:
            return True
        return any(screen.name() == saved_screen for screen in QApplication.screens())

    def restore_window_placement(self) -> None:
        geometry = self.settings_store.value("window_geometry", None)
        if geometry is not None and self.saved_screen_is_available():
            self.restoreGeometry(geometry)
            state_value = self.settings_store.value("window_state_value", None)
            if state_value is not None:
                try:
                    self.setWindowState(Qt.WindowState(int(float(state_value))))
                except (TypeError, ValueError):
                    pass

    def window_state_value(self) -> int:
        state = self.windowState()
        return int(state.value) if hasattr(state, "value") else int(state)

    def save_window_placement(self) -> None:
        if self._restoring_window:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            self.settings_store.setValue("window_screen_name", screen.name())
            self.settings_store.setValue("window_screen_geometry", screen.geometry().getRect())
        self.settings_store.setValue("window_geometry", self.saveGeometry())
        self.settings_store.setValue("window_state_value", self.window_state_value())
        self.settings_store.sync()

    def schedule_window_placement_save(self) -> None:
        if not self._restoring_window:
            self._window_save_timer.start(180)

    def build_settings_panel(self) -> None:
        self.settings_panel.setFixedWidth(300)
        root = QWidget(self.settings_panel.body)
        self.settings_root = root
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status_text = QLabel("No image loaded", root)
        self.src_pixels = QLabel("-", root)
        self.src_ppi = QLabel("-", root)
        self.src_size = QLabel("-", root)
        src = self.make_section("PNG Source", root)
        src.layout().addWidget(self.spec_label("Status:", self.status_text))
        src.layout().addWidget(self.spec_label("Pixels:", self.src_pixels))
        src.layout().addWidget(self.spec_label("Detected PPI:", self.src_ppi))
        src.layout().addWidget(self.spec_label("Detected Size:", self.src_size))
        layout.addWidget(src)

        ref = self.make_section("Reference Image Size", root)
        self.image_width = self.add_double_row(ref, "Image width (in)", 0.01, 100000, 30.0, 0.01)
        self.image_height = self.add_double_row(ref, "Image height (in)", 0.01, 100000, 40.0, 0.01)
        self.image_ppi = self.add_double_row(ref, "Image PPI", 1.0, 100000, 100.0, 0.01)
        layout.addWidget(ref)

        bounds = self.make_section("Real Canvas / Grid Bounds", root)
        self.canvas_width = self.add_double_row(bounds, "Canvas width (in)", 0.01, 100000, 29.75, 0.01)
        self.canvas_height = self.add_double_row(bounds, "Canvas height (in)", 0.01, 100000, 39.5, 0.01)
        layout.addWidget(bounds)

        grid = self.make_section("Grid Settings", root)
        self.grid_size = self.add_double_row(grid, "Grid square size (in)", 0.01, 100000, 2.0, 0.01)
        self.major_every = self.add_int_row(grid, "Major line every N grid cells", 0, 100000, 0)
        layout.addWidget(grid)

        offset = self.make_section("Image Offset", root)
        self.offset_x = self.add_double_row(offset, "Offset X (in)", -100000, 100000, 0.0, 0.25)
        self.offset_y = self.add_double_row(offset, "Offset Y (in)", -100000, 100000, 0.0, 0.25)
        layout.addWidget(offset)

        zoom = self.make_section("Zoom", root)
        self.zoom_scale = self.add_double_row(zoom, "Zoom scale", 1.0, MAX_ZOOM, 1.0, 0.25)
        tiny = QLabel("Ctrl +, Ctrl -, Ctrl 0, or Ctrl + mouse wheel.", zoom)
        tiny.setObjectName("mutedText")
        zoom.layout().addWidget(tiny)
        layout.addWidget(zoom)

        palette = self.make_section("Palette", root)
        self.palette_count = self.add_int_row(palette, "Swatches", 8, 64, 32)
        layout.addWidget(palette)

        self.image_summary = QLabel("-", root)
        self.canvas_summary = QLabel("-", root)
        self.grid_summary = QLabel("-", root)
        self.cursor_summary = QLabel("-", root)
        self.display_summary = QLabel("-", root)
        view = self.make_section("View", root)
        view.layout().addWidget(self.spec_label("Image:", self.image_summary))
        view.layout().addWidget(self.spec_label("Canvas:", self.canvas_summary))
        view.layout().addWidget(self.spec_label("Grid:", self.grid_summary))
        view.layout().addWidget(self.spec_label("Cursor:", self.cursor_summary))
        view.layout().addWidget(self.spec_label("Display:", self.display_summary))
        layout.addWidget(view)

        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setHorizontalSpacing(7)
        buttons.setVerticalSpacing(7)
        button_specs = [
            ("Open PNG", self.open_png_dialog, False),
            ("Use PNG Specs", self.use_png_specs, False),
            ("Reset Offset", self.reset_offset, False),
            ("Fit View", self.reset_zoom, False),
            ("Zoom -", self.zoom_out, False),
            ("Zoom +", self.zoom_in, False),
            ("Clear Image", self.clear_image, False),
            ("Save Settings", self.save_and_render, True),
        ]
        for index, (text, callback, primary) in enumerate(button_specs):
            button = QPushButton(text, root)
            if primary:
                button.setObjectName("primaryButton")
            button.clicked.connect(callback)
            buttons.addWidget(button, index // 2, index % 2)
        layout.addLayout(buttons)
        layout.addStretch(1)
        panel_layout = QVBoxLayout(self.settings_panel.body)
        panel_layout.setContentsMargins(0, 8, 0, 0)
        panel_layout.addWidget(root)

        controls: Iterable[QAbstractSpinBox] = [
            self.image_width,
            self.image_height,
            self.image_ppi,
            self.canvas_width,
            self.canvas_height,
            self.grid_size,
            self.major_every,
            self.offset_x,
            self.offset_y,
            self.zoom_scale,
        ]
        for control in controls:
            control.valueChanged.connect(self.controls_changed)
        self.palette_count.valueChanged.connect(self.palette_count_changed)

    def build_palette_panel(self) -> None:
        self.palette_panel.setFixedWidth(360)
        root = QVBoxLayout(self.palette_panel.body)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(9)
        probe = QFrame(self.palette_panel.body)
        probe.setObjectName("pixelProbe")
        probe_layout = QVBoxLayout(probe)
        probe_layout.setContentsMargins(10, 10, 10, 10)
        probe_layout.setSpacing(8)
        self.pixel_color = ColorBox("#000000", probe)
        self.pixel_color.setMinimumHeight(228)
        probe_layout.addWidget(self.pixel_color)
        self.pixel_title = QLabel("PIXEL COLOR", probe)
        self.pixel_hex = QLabel("-", probe)
        self.pixel_rgb = QLabel("Move over image", probe)
        self.pixel_pos = QLabel("-", probe)
        for label in (self.pixel_title, self.pixel_hex, self.pixel_rgb, self.pixel_pos):
            label.setObjectName("mutedText")
            probe_layout.addWidget(label)
        root.addWidget(probe)
        self.palette_info = QLabel("Load a PNG to generate computed swatches.", self.palette_panel.body)
        self.palette_info.setObjectName("mutedText")
        self.palette_info.setWordWrap(True)
        root.addWidget(self.palette_info)
        scroll = QScrollArea(self.palette_panel.body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.palette_grid_widget = QWidget(scroll)
        self.palette_grid = QGridLayout(self.palette_grid_widget)
        self.palette_grid.setContentsMargins(0, 0, 0, 0)
        self.palette_grid.setSpacing(7)
        scroll.setWidget(self.palette_grid_widget)
        root.addWidget(scroll, 1)

    def make_section(self, title: str, parent: QWidget) -> QFrame:
        section = QFrame(parent)
        section.setObjectName("sectionFrame")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(6)
        label = QLabel(title.upper(), section)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        return section

    def add_double_row(
        self,
        section: QFrame,
        label: str,
        minimum: float,
        maximum: float,
        value: float,
        step: float,
    ) -> QDoubleSpinBox:
        row = QWidget(section)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(7)
        text = QLabel(label, row)
        text.setObjectName("mutedText")
        spin = QDoubleSpinBox(row)
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(64)
        row_layout.addWidget(text, 1)
        row_layout.addWidget(spin)
        section.layout().addWidget(row)
        return spin

    def add_int_row(self, section: QFrame, label: str, minimum: int, maximum: int, value: int) -> QSpinBox:
        row = QWidget(section)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(7)
        text = QLabel(label, row)
        text.setObjectName("mutedText")
        spin = QSpinBox(row)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(64)
        row_layout.addWidget(text, 1)
        row_layout.addWidget(spin)
        section.layout().addWidget(row)
        return spin

    def spec_label(self, name: str, value: QLabel) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(f"<b>{name}</b>", row)
        label.setObjectName("specName")
        value.setObjectName("mutedText")
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(label)
        layout.addWidget(value, 1)
        return row

    def sync_state_to_controls(self) -> None:
        self._syncing = True
        self.image_width.setValue(self.state.image_width_in)
        self.image_height.setValue(self.state.image_height_in)
        self.image_ppi.setValue(self.state.image_ppi)
        self.canvas_width.setValue(self.state.canvas_width_in)
        self.canvas_height.setValue(self.state.canvas_height_in)
        self.grid_size.setValue(self.state.grid_size_in)
        self.major_every.setValue(self.state.major_every)
        self.offset_x.setValue(self.state.offset_x_in)
        self.offset_y.setValue(self.state.offset_y_in)
        self.zoom_scale.setValue(round2(self.state.zoom_scale))
        self.palette_count.setValue(self.state.palette_count)
        self._syncing = False

    def read_controls_to_state(self) -> None:
        self.state.image_width_in = max(0.01, self.image_width.value() or 30.0)
        self.state.image_height_in = max(0.01, self.image_height.value() or 40.0)
        self.state.image_ppi = max(1.0, self.image_ppi.value() or 100.0)
        self.state.canvas_width_in = max(0.01, self.canvas_width.value() or 29.75)
        self.state.canvas_height_in = max(0.01, self.canvas_height.value() or 39.5)
        self.state.grid_size_in = max(0.01, self.grid_size.value() or 2.0)
        self.state.major_every = max(0, int(self.major_every.value() or 0))
        self.state.offset_x_in = snap_quarter(self.offset_x.value() or 0.0)
        self.state.offset_y_in = snap_quarter(self.offset_y.value() or 0.0)
        self.state.zoom_scale = clamp(self.zoom_scale.value() or 1.0, 1.0, MAX_ZOOM)
        self.state.palette_count = clamp_int(self.palette_count.value() or 32, 8, 64)

    def controls_changed(self) -> None:
        if self._syncing:
            return
        self.read_controls_to_state()
        self.sync_state_to_controls()
        self.save_settings()
        self.workspace.update()
        self.refresh_info_panel()
        self.update_cursor_readout(self.workspace.last_pointer, self.workspace.get_cursor_grid_info(self.workspace.last_pointer))

    def palette_count_changed(self) -> None:
        if self._syncing:
            return
        self.read_controls_to_state()
        self.sync_state_to_controls()
        self.save_settings()
        self.refresh_palette()

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                color: #f2f4f7;
                font-family: "Liberation Mono", "Consolas", monospace;
                font-size: 11px;
                letter-spacing: 0;
            }
            QMainWindow, QDialog#regionDialog {
                background: #030407;
            }
            QFrame#floatingPanel {
                background: rgba(9, 11, 16, 252);
                border: 1px solid #0e1117;
            }
            QWidget#panelBody {
                background: transparent;
            }
            QFrame#sectionFrame {
                background: rgba(255,255,255,5);
                border-top: 1px solid rgba(255,255,255,19);
                border-left: 1px solid rgba(255,255,255,19);
                border-right: 1px solid rgba(0,0,0,204);
                border-bottom: 1px solid rgba(0,0,0,204);
            }
            QLabel#sectionTitle {
                color: #ff3850;
                font-size: 9px;
                font-weight: 900;
            }
            QLabel#mutedText {
                color: #a7adba;
            }
            QLabel#specName {
                color: #f2f4f7;
                font-weight: 800;
            }
            QDoubleSpinBox, QSpinBox {
                background: #050609;
                color: #f5f5ff;
                border-top: 1px solid rgba(0,0,0,245);
                border-left: 1px solid rgba(0,0,0,245);
                border-right: 1px solid rgba(255,255,255,41);
                border-bottom: 1px solid rgba(255,255,255,41);
                padding: 5px 7px;
                selection-background-color: #9b000e;
            }
            QDoubleSpinBox:focus, QSpinBox:focus {
                border: 1px solid rgba(255, 20, 48, 242);
            }
            QPushButton {
                background: #161820;
                border-top: 1px solid rgba(255,255,255,56);
                border-left: 1px solid rgba(255,255,255,56);
                border-right: 1px solid rgba(0,0,0,245);
                border-bottom: 1px solid rgba(0,0,0,245);
                padding: 7px 7px;
                font-size: 10px;
                font-weight: 800;
                text-transform: uppercase;
            }
            QPushButton:hover {
                background: #201a27;
            }
            QPushButton#primaryButton {
                background: #9b000e;
                color: #ffffff;
            }
            QFrame#pixelProbe, QFrame#liveColorCard {
                background: rgba(10,13,19,235);
                border: 1px solid rgba(255,255,255,26);
                border-right-color: rgba(0,0,0,224);
                border-bottom-color: rgba(0,0,0,224);
            }
            QFrame#swatchTile, QFrame#mixTile {
                background: #0b0d12;
                border-top: 1px solid rgba(255,255,255,36);
                border-left: 1px solid rgba(255,255,255,36);
                border-right: 1px solid rgba(0,0,0,235);
                border-bottom: 1px solid rgba(0,0,0,235);
            }
            QLabel#swatchMeta {
                color: rgba(255,255,255,184);
                background: rgba(0,0,0,107);
                font-size: 10px;
                padding: 4px;
            }
            QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
                background: transparent;
                border: none;
            }
            QFrame#modalHeader {
                background: #11131a;
                border-bottom: 1px solid rgba(255,0,36,107);
                min-height: 42px;
            }
            QLabel#modalTitle {
                font-size: 11px;
                font-weight: 900;
            }
            QFrame#modalPalettePanel {
                background: rgba(8,10,14,246);
                border-left: 1px solid rgba(255,255,255,20);
            }
            QLabel#panelHeading {
                padding: 6px 8px;
                background: #141b22;
                border: 1px solid rgba(255,255,255,26);
                font-size: 12px;
                font-weight: 900;
            }
            QLabel#liveHex {
                color: #ff3850;
                font-size: 16px;
                font-weight: 900;
            }
            QLabel#cursorTag {
                background: rgba(0,0,0,235);
                color: #ff3048;
                border: 1px solid rgba(255,0,36,245);
                padding: 10px 16px;
                font-size: 22px;
                font-weight: 900;
            }
            """
        )

    def position_panels(self) -> None:
        settings_content_h = self.settings_root.sizeHint().height() + 42 if hasattr(self, "settings_root") else 620
        self.settings_panel.resize(300, min(max(620, settings_content_h), self.height() - 16))
        self.palette_panel.resize(360, min(max(620, self.height() - 16), self.height() - 16))
        if self.state.palette_docked_right:
            self.state.palette_x = max(8.0, self.workspace.width() - self.palette_panel.width() - 8.0)
            self.state.palette_y = 8.0
        elif self.state.palette_x < 0:
            self.state.palette_x = max(8.0, self.workspace.width() - self.palette_panel.width() - 8.0)
        self.place_panel(self.settings_panel, self.state.panel_x, self.state.panel_y)
        self.place_panel(self.palette_panel, self.state.palette_x, self.state.palette_y)
        self.settings_panel.raise_()
        self.palette_panel.raise_()
        self.cursor_tag.raise_()

    def place_panel(self, panel: FloatingPanel, x: float, y: float) -> None:
        max_x = max(8, self.workspace.width() - panel.width() - 8)
        max_y = max(8, self.workspace.height() - panel.height() - 8)
        panel.move(round(clamp(x, 8, max_x)), round(clamp(y, 8, max_y)))
        panel.on_moved(float(panel.x()), float(panel.y()))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self.position_panels)
        self.workspace.update()
        self.schedule_window_placement_save()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self.schedule_window_placement_save()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self.position_panels)
            self.schedule_window_placement_save()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.save_window_placement()
        self.save_settings()
        super().closeEvent(event)

    def on_settings_panel_moved(self, x: float, y: float) -> None:
        self.state.panel_x = x
        self.state.panel_y = y
        self.save_settings()

    def on_palette_panel_moved(self, x: float, y: float) -> None:
        self.state.palette_x = x
        self.state.palette_y = y
        self.save_settings()

    def on_palette_panel_user_drag_started(self) -> None:
        self.state.palette_docked_right = 0
        self.save_settings()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if QApplication.activeWindow() is not self:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if event.angleDelta().y() > 0:
                    self.zoom_in()
                else:
                    self.zoom_out()
                event.accept()
                return True
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                key = event.key()
                if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    self.zoom_in()
                    event.accept()
                    return True
                if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    self.zoom_out()
                    event.accept()
                    return True
                if key == Qt.Key.Key_0:
                    self.reset_zoom()
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.zoom_in()
                event.accept()
                return
            if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self.zoom_out()
                event.accept()
                return
            if key == Qt.Key.Key_0:
                self.reset_zoom()
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        self.workspace.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        self.workspace.dropEvent(event)

    def set_zoom(self, value: float) -> None:
        self.state.zoom_scale = clamp(value, 1.0, MAX_ZOOM)
        self.sync_state_to_controls()
        self.save_settings()
        self.workspace.update()
        self.refresh_info_panel()
        self.update_cursor_readout(self.workspace.last_pointer, self.workspace.get_cursor_grid_info(self.workspace.last_pointer))

    def zoom_in(self) -> None:
        self.set_zoom(self.state.zoom_scale * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self.state.zoom_scale / ZOOM_STEP)

    def reset_zoom(self) -> None:
        self.state.zoom_scale = 1.0
        self.state.view_pan_x_in = 0.0
        self.state.view_pan_y_in = 0.0
        self.sync_state_to_controls()
        self.save_settings()
        self.workspace.update()
        self.refresh_info_panel()

    def reset_offset(self) -> None:
        self.state.offset_x_in = 0.0
        self.state.offset_y_in = 0.0
        self.sync_state_to_controls()
        self.save_settings()
        self.workspace.update()
        self.refresh_info_panel()

    def save_and_render(self) -> None:
        self.read_controls_to_state()
        self.sync_state_to_controls()
        self.save_settings()
        self.workspace.update()
        self.refresh_info_panel()

    def open_png_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PNG", "", "PNG Images (*.png)")
        if path:
            self.load_png(path, persist=True, apply_specs=True)

    def restore_last_image(self) -> None:
        path = self.settings_store.value("last_image_path", "", str)
        if path and os.path.exists(path):
            self.load_png(path, persist=False, apply_specs=False)

    def load_png(self, path: str, persist: bool = True, apply_specs: bool = True) -> None:
        self._load_job_id += 1
        self._palette_job_id += 1
        job_id = self._load_job_id
        self.status_text.setText(f"Loading PNG: {Path(path).name}")
        self.refresh_palette_panel([], "Waiting for image load...")
        worker = ImageLoadWorker(job_id, path, persist, apply_specs, self.state.image_ppi or 100.0)
        self._load_worker = worker
        worker.signals.finished.connect(self.finish_load_png)
        worker.signals.error.connect(self.image_load_error)
        self.thread_pool.start(worker)

    @Slot(int, object, object, bool, bool)
    def finish_load_png(
        self,
        job_id: int,
        image: QImage,
        source: SourceInfo,
        persist: bool,
        apply_specs: bool,
    ) -> None:
        if job_id != self._load_job_id:
            return
        self.image = image
        self.source_info = source
        self.workspace.set_image(image, source)
        if apply_specs:
            self.use_png_specs()
        if persist:
            self.settings_store.setValue("last_image_path", path)
        self.refresh_info_panel()
        self.refresh_palette()

    @Slot(int, str)
    def image_load_error(self, job_id: int, message: str) -> None:
        if job_id == self._load_job_id:
            self.status_text.setText(message)
            self.refresh_palette_panel([])

    def clear_image(self) -> None:
        self._load_job_id += 1
        self._palette_job_id += 1
        self.image = None
        self.source_info = None
        self.workspace.set_image(None, None)
        self.palette_colors = []
        self.refresh_palette_panel([])
        self.status_text.setText("No image loaded")
        self.cursor_tag.hide()
        self.pixel_color.set_color("#000000")
        self.pixel_hex.setText("-")
        self.pixel_rgb.setText("Move over image")
        self.pixel_pos.setText("-")
        self.settings_store.remove("last_image_path")
        self.workspace.update()
        self.refresh_info_panel()

    def use_png_specs(self) -> None:
        if self.source_info is None:
            return
        self.state.image_ppi = round2(self.source_info.detected_ppi)
        self.state.image_width_in = round2(self.source_info.width / self.source_info.detected_ppi)
        self.state.image_height_in = round2(self.source_info.height / self.source_info.detected_ppi)
        self.sync_state_to_controls()
        self.save_settings()
        self.workspace.update()
        self.refresh_info_panel()

    def sample_pixel(self, abs_x_in: float, abs_y_in: float) -> tuple[int, int, int, int, int, int] | None:
        if self.image is None or self.source_info is None:
            return None
        img_local_x = abs_x_in - self.state.offset_x_in
        img_local_y = abs_y_in - self.state.offset_y_in
        if (
            img_local_x < 0
            or img_local_y < 0
            or img_local_x >= self.state.image_width_in
            or img_local_y >= self.state.image_height_in
        ):
            return None
        px = clamp_int(math.floor((img_local_x / self.state.image_width_in) * self.source_info.width), 0, self.source_info.width - 1)
        py = clamp_int(math.floor((img_local_y / self.state.image_height_in) * self.source_info.height), 0, self.source_info.height - 1)
        color = self.image.pixelColor(px, py)
        return color.red(), color.green(), color.blue(), color.alpha(), px, py

    def update_pixel_probe_from_pos(self, pos: QPoint) -> None:
        if self.source_info is None:
            return
        abs_x, abs_y = self.workspace.get_absolute_inches(pos)
        sample = self.sample_pixel(abs_x, abs_y)
        if sample is None:
            return
        r, g, b, a, px, py = sample
        hex_value = rgb_to_hex(r, g, b)
        self.pixel_color.set_color(hex_value)
        self.pixel_hex.setText(hex_value)
        self.pixel_rgb.setText(f"rgb({r}, {g}, {b})  a:{a}")
        self.pixel_pos.setText(f'px {px}, {py} - {abs_x:.3f}", {abs_y:.3f}"')

    def update_cursor_readout(self, pos: QPoint, info: CursorInfo | None) -> None:
        if info is None:
            self.cursor_tag.hide()
            self.cursor_summary.setText("-")
            return
        text = f"X: {info.local_x_thou}  Y: {info.local_y_thou}"
        self.cursor_tag.setText(text)
        self.cursor_tag.adjustSize()
        self.cursor_tag.move(pos.x() + 14, pos.y() + 16)
        self.cursor_tag.raise_()
        self.cursor_tag.show()
        self.cursor_summary.setText(
            f'{text} / {info.units_per_cell} per cell, cell {info.cell_x},{info.cell_y}, '
            f'absolute {info.x_in:.3f}", {info.y_in:.3f}"'
        )

    def refresh_info_panel(self) -> None:
        source = self.source_info
        if self.workspace.width() > 0 and self.workspace.height() > 0:
            self.workspace.recompute_layout()
        layout = self.workspace.layout_info
        if source is None:
            self.src_pixels.setText("-")
            self.src_ppi.setText("-")
            self.src_size.setText("-")
            self.image_summary.setText("-")
            self.canvas_summary.setText("-")
            self.grid_summary.setText("-")
            self.display_summary.setText("-")
            return
        src_w_in = source.width / source.detected_ppi
        src_h_in = source.height / source.detected_ppi
        grid_size = max(0.001, self.state.grid_size_in)
        cols = math.floor(self.state.canvas_width_in / grid_size)
        rows = math.floor(self.state.canvas_height_in / grid_size)
        right_remainder = self.state.canvas_width_in - cols * grid_size
        bottom_remainder = self.state.canvas_height_in - rows * grid_size
        units_per_cell = round(grid_size * 1000)
        self.status_text.setText(f"Loaded: {Path(source.path).name}")
        self.src_pixels.setText(f"{source.width} x {source.height}px")
        self.src_ppi.setText(f"{source.detected_ppi:.2f} ({source.ppi_source})")
        self.src_size.setText(f"{src_w_in:.2f} x {src_h_in:.2f} in")
        self.image_summary.setText(
            f"{self.state.image_width_in:.2f} x {self.state.image_height_in:.2f} in @ {self.state.image_ppi:.2f} PPI"
        )
        self.canvas_summary.setText(f"{self.state.canvas_width_in:.2f} x {self.state.canvas_height_in:.2f} in")
        self.grid_summary.setText(
            f'{grid_size:.2f}" cells, {units_per_cell} thou/cell\n'
            f"{cols} x {rows} full cells\n"
            f'right {right_remainder:.2f}", bottom {bottom_remainder:.2f}"'
        )
        self.display_summary.setText(
            f"{layout.px_per_in:.2f} screen px/in, fit {layout.fit_px_per_in:.2f}, "
            f"zoom {self.state.zoom_scale:.2f}x, image offset "
            f'{self.state.offset_x_in:.2f}", {self.state.offset_y_in:.2f}", view pan '
            f'{self.state.view_pan_x_in:.2f}", {self.state.view_pan_y_in:.2f}"'
        )

    def refresh_palette(self) -> None:
        if self.image is None:
            self.refresh_palette_panel([])
            return
        self._palette_job_id += 1
        job_id = self._palette_job_id
        self.refresh_palette_panel([], "Computing swatches in background...")
        worker = PaletteWorker(job_id, QImage(self.image), self.state.palette_count, False)
        self._palette_worker = worker
        worker.signals.finished.connect(self.finish_palette)
        worker.signals.error.connect(self.palette_error)
        self.thread_pool.start(worker)

    @Slot(int, object)
    def finish_palette(self, job_id: int, colors: list[PaletteColor]) -> None:
        if job_id != self._palette_job_id:
            return
        self.palette_colors = colors
        self.refresh_palette_panel(colors)

    @Slot(int, str)
    def palette_error(self, job_id: int, message: str) -> None:
        if job_id == self._palette_job_id:
            self.refresh_palette_panel([], f"Palette failed: {message}")

    def refresh_palette_panel(self, colors: list[PaletteColor], placeholder_text: str | None = None) -> None:
        clear_layout(self.palette_grid)
        if not colors:
            text = placeholder_text or "Load a PNG to generate computed swatches."
            self.palette_info.setText(text)
            placeholder = QLabel(
                placeholder_text or "Swatches are extracted from actual image data with stronger diversity clustering.",
                self.palette_grid_widget,
            )
            placeholder.setObjectName("mutedText")
            placeholder.setWordWrap(True)
            self.palette_grid.addWidget(placeholder, 0, 0, 1, 4)
            return
        total = sum(color.count for color in colors) or 1
        self.palette_info.setText(f"{len(colors)} computed swatches from blurred + detailed image analysis")
        columns = 4 if self.palette_panel.width() >= 340 else 3
        for index, color in enumerate(colors):
            self.palette_grid.addWidget(SwatchTile(color, total), index // columns, index % columns)
        self.palette_grid.setRowStretch((len(colors) + columns - 1) // columns, 1)

    def region_image_for_palette(self, region: Region) -> QImage | None:
        if self.image is None or self.source_info is None:
            return None
        return crop_region_image(
            self.image,
            region,
            self.source_info.width,
            self.source_info.height,
            self.state.image_width_in,
            self.state.image_height_in,
            self.state.offset_x_in,
            self.state.offset_y_in,
        )

    def open_region_dialog(self, region: Region) -> None:
        if self.image is None or self.source_info is None:
            return
        dialog = RegionDialog(self, region)
        dialog.exec()


def clear_layout(layout: QGridLayout | QVBoxLayout | QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        nested = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif nested is not None:
            clear_layout(nested)  # type: ignore[arg-type]


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("CanvasCaliper")
    app.setApplicationName("CanvasCaliper")
    if "Fusion" in QStyleFactory.keys():
        app.setStyle(QStyleFactory.create("Fusion"))
    window = CanvasCaliperWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
