from __future__ import annotations

import math
import os
from dataclasses import fields
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, QThreadPool, QTimer, Slot, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QKeyEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
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
    QVBoxLayout,
    QWidget,
)

from .constants import MAX_ZOOM, SETTINGS_VERSION, ZOOM_STEP
from .dialogs import RegionDialog
from .image_io import crop_region_image
from .models import AppState, CursorInfo, PaletteColor, Region, SourceInfo
from .rendering import CanvasWidget
from .utils import clamp, clamp_int, round2, rgb_to_hex, snap_quarter
from .widgets import ColorBox, FloatingPanel, SwatchTile, clear_layout
from .workers import ImageLoadWorker, PaletteWorker


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
            self.settings_store.setValue("last_image_path", source.path)
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


