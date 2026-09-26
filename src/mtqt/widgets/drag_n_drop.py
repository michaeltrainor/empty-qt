"""Standalone gallery that imports mtqt like a production tool."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QDragEnterEvent, QDropEvent, QPaintEvent, QPainter, QPen)
from PySide6.QtWidgets import QWidget

from mtqt import (
    apply_stylesheet,
    ensure_application,
)

_MARGIN = 16
_BORDER_RADIUS = 8
_PEN_WIDTH = 2
_LABEL_PIXEL_SIZE = 16


class DropWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        apply_stylesheet(self, "base", "widgets")

        self.setAcceptDrops(True)
        self.setWindowTitle("Drag and Drop Example")
        self.resize(400, 300)

    def paintEvent(self, event: QPaintEvent, /) -> None:
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        box = self.rect().adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        base = self.palette().color(self.foregroundRole())

        fill = QColor(base)
        fill.setAlpha(28)
        painter.setBrush(fill)

        pen_color = QColor(base)
        pen_color.setAlpha(110)
        pen = QPen(pen_color)
        pen.setStyle(Qt.PenStyle.DotLine)
        pen.setWidth(_PEN_WIDTH)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawRoundedRect(box, _BORDER_RADIUS, _BORDER_RADIUS)

        text_color = QColor(base)
        text_color.setAlpha(150)
        font = painter.font()
        font.setPixelSize(_LABEL_PIXEL_SIZE)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "Drop Here")

    def dragEnterEvent(self, event: QDragEnterEvent, /) -> None:
        event.accept()

    def dropEvent(self, event: QDropEvent, /) -> None:
        print(event.type())
        print(event.mimeData().urls())


def main() -> int:
    app = ensure_application()

    widget = DropWidget()
    widget.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
