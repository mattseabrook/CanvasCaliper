from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .models import Region
from .utils import clamp_int


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


