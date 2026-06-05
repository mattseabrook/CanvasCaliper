from __future__ import annotations

from dataclasses import dataclass


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

