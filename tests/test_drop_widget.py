"""Drop target used by the gallery example."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QWidget

from mtqt import DropWidget


def test_drop_widget_constructs(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = DropWidget(host)
    qtbot.addWidget(widget)
    assert widget.parent() is host
    assert widget.acceptDrops()
    assert widget.windowTitle() == "Drag and Drop Example"


def test_drop_widget_drag_enter_accepts_file_url(qtbot) -> None:
    widget = DropWidget()
    qtbot.addWidget(widget)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("C:/tmp/entered.txt")])
    event = QDragEnterEvent(
        QPoint(10, 12),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert not event.isAccepted()
    widget.dragEnterEvent(event)
    assert event.isAccepted()


def test_drop_widget_drop_event_reads_urls(qtbot, capsys) -> None:
    widget = DropWidget()
    qtbot.addWidget(widget)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("C:/tmp/dropped.txt")])
    event = QDropEvent(
        QPointF(10, 12),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)
    captured = capsys.readouterr()
    assert "dropped.txt" in captured.out


def test_drop_widget_paint_offscreen(qtbot) -> None:
    widget = DropWidget()
    widget.resize(400, 300)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    pixmap = widget.grab()
    assert not pixmap.isNull()
    widget.repaint()
