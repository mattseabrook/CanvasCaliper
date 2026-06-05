from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .models import CanvasLayout, CursorInfo, Region, SourceInfo
from .utils import clamp_int


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


def qcolor_alpha(r: int, g: int, b: int, alpha_float: float) -> QColor:
    return QColor(r, g, b, clamp_int(round(alpha_float * 255), 0, 255))


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


