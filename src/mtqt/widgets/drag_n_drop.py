"""QWidget drop target that accepts file drops and draws a dashed area."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QWidget

from mtqt import (
    apply_stylesheet,
    ensure_application,
)

_MARGIN = 16
_BORDER_RADIUS = 8
_PEN_WIDTH = 2
_LABEL_PIXEL_SIZE = 16


def _local_files(mime: QMimeData) -> list[str]:
    """Return the local file paths in ``mime``, skipping non-file URLs."""
    return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]


class DropWidget(QWidget):
    """A ``QWidget`` drop target that accepts local file drops.

    Draws a dashed ``Drop Here`` area and can be shown as a window. A drag
    with no local files, such as plain text or a web link, is refused.

    ``filesDropped`` emits a ``list[str]`` of local paths. It uses ``str``
    because ``Path`` is not a Qt metatype, so wrap each one in
    ``pathlib.Path`` in Python.

    Args:
        parent: Qt parent. ``None`` means Python owns this widget until it
            is parented or goes out of scope.
    """

    filesDropped = Signal(list)

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
        if _local_files(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent, /) -> None:
        files = _local_files(event.mimeData())
        if not files:
            event.ignore()
            return
        event.acceptProposedAction()
        self.filesDropped.emit(files)


def main() -> int:
    """Show ``DropWidget`` as a window and run the event loop.

    Returns:
        Process exit code.
    """
    app = ensure_application()

    widget = DropWidget()
    widget.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
