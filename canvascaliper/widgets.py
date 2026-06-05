from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .models import PaletteColor
from .utils import clamp


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



def clear_layout(layout: QGridLayout | QVBoxLayout | QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        nested = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif nested is not None:
            clear_layout(nested)  # type: ignore[arg-type]

