from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot, Qt
from PySide6.QtGui import QImage

from .image_io import crop_region_image, parse_png_phys
from .models import Region, SourceInfo
from .palette import compute_palette_from_image
from .utils import safe_emit


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

