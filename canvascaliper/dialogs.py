from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QTimer, Slot, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .models import PaletteColor, Region
from .rendering import RegionCanvas
from .utils import clamp_int, rgb_to_hex
from .widgets import ColorBox, SwatchTile, clear_layout
from .workers import RegionPaletteWorker, RegionPreviewWorker


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


