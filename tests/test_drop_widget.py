"""Drop target used by the gallery example."""

from __future__ import annotations

from pathlib import Path

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


def test_drop_widget_drag_enter_refuses_web_url(qtbot) -> None:
    widget = DropWidget()
    qtbot.addWidget(widget)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/a.txt")])
    event = QDragEnterEvent(
        QPoint(10, 12),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dragEnterEvent(event)
    assert not event.isAccepted()


def _drop_event(mime: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPointF(10, 12),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_drop_widget_emits_local_files_only(qtbot, tmp_path: Path, capsys) -> None:
    widget = DropWidget()
    qtbot.addWidget(widget)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(first)),
            QUrl("https://example.com/skipped.txt"),
            QUrl.fromLocalFile(str(second)),
        ]
    )
    event = _drop_event(mime)
    with qtbot.waitSignal(widget.filesDropped) as blocker:
        widget.dropEvent(event)
    assert [Path(p) for p in blocker.args[0]] == [first, second]
    assert event.isAccepted()
    assert capsys.readouterr().out == ""


def test_drop_widget_ignores_drop_without_local_files(qtbot) -> None:
    widget = DropWidget()
    qtbot.addWidget(widget)
    mime = QMimeData()
    mime.setText("not a file")
    event = _drop_event(mime)
    with qtbot.assertNotEmitted(widget.filesDropped):
        widget.dropEvent(event)
    assert not event.isAccepted()


def test_drop_widget_paint_offscreen(qtbot) -> None:
    widget = DropWidget()
    widget.resize(400, 300)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    pixmap = widget.grab()
    assert not pixmap.isNull()
    widget.repaint()
