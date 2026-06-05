from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QStyleFactory

from .window import CanvasCaliperWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("CanvasCaliper")
    app.setApplicationName("CanvasCaliper")
    if "Fusion" in QStyleFactory.keys():
        app.setStyle(QStyleFactory.create("Fusion"))
    window = CanvasCaliperWindow()
    window.show()
    return app.exec()
