"""Rename a node by double-clicking its header."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QLineEdit, QWidget

from mtqt.widgets.node_graph import (
    Node,
    NodeGraph,
    NodeGraphWidget,
    Port,
    PortDirection,
)


def _title_past_minimum() -> str:
    """Title whose header is wider than the 180px floor on this machine's font."""
    font = QFont()
    font.setFamilies(["IBM Plex Sans", "Segoe UI"])
    font.setPixelSize(13)
    font.setBold(True)
    metrics = QFontMetrics(font)
    title = "Upstream Plate"
    # Matches NodeItem._title_width: advance + header inset * 2 + 4.
    while metrics.horizontalAdvance(title) + 28 <= 180 and len(title) < 400:
        title += " M"
    return title


def _show(qtbot, graph: NodeGraph) -> tuple[QWidget, NodeGraphWidget]:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    host.show()
    widget.show()
    qtbot.waitExposed(widget)
    view = widget.view
    qtbot.waitUntil(lambda: view.viewport().width() > 400)
    view.set_zoom(1)
    return host, widget


def _open_header(qtbot, widget: NodeGraphWidget, node_id: str) -> QLineEdit:
    item = widget.scene.node_item(node_id)
    assert item is not None
    view = widget.view
    view.centerOn(item)
    pos = view.mapFromScene(item.mapToScene(QPointF(48, 16)))
    qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    qtbot.waitUntil(lambda: item.title_editor() is not None)
    edit = item.title_editor()
    assert isinstance(edit, QLineEdit)
    qtbot.waitUntil(lambda: edit.selectedText() == edit.text())
    return edit


def test_double_click_header_renames_and_undoes(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(
        Node(
            "a",
            "A",
            inputs=(Port("in", PortDirection.IN),),
            outputs=(Port("out", PortDirection.OUT),),
            position=(0, 0),
        )
    )
    _host, widget = _show(qtbot, graph)
    item = widget.scene.node_item("a")
    assert item is not None
    origin = QPointF(item.pos())
    before = item.boundingRect().width()

    edit = _open_header(qtbot, widget, "a")
    assert edit.text() == "A"
    assert item.pos().x() == origin.x()
    assert item.pos().y() == origin.y()
    assert graph.has_node("a")

    scroll = widget.view.horizontalScrollBar().value()
    qtbot.keyClick(edit, Qt.Key.Key_Space)
    assert edit.text() == " "
    assert widget.view.horizontalScrollBar().value() == scroll
    qtbot.keyClick(edit, Qt.Key.Key_Escape)
    assert item.title_editor() is None
    assert graph.node("a").title == "A"
    assert widget.history.can_undo is False

    edit = _open_header(qtbot, widget, "a")
    view = widget.view
    qtbot.keyClick(view, Qt.Key.Key_Delete)
    assert graph.has_node("a")
    assert item.title_editor() is edit
    assert edit.text() == ""
    qtbot.keyClick(view, Qt.Key.Key_Backspace)
    assert graph.has_node("a")
    assert edit.text() == ""
    title = _title_past_minimum()
    qtbot.keyClicks(view, f"  {title}  ")
    qtbot.keyClick(edit, Qt.Key.Key_Return)
    assert item.title_editor() is None
    assert graph.node("a").title == title
    assert item.title == title
    assert item.boundingRect().width() > before

    qtbot.waitUntil(widget.view.hasFocus)
    qtbot.keyClick(widget.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert graph.node("a").title == "A"
    assert item.title == "A"
    qtbot.keyClick(widget.view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert graph.node("a").title == title
    assert item.title == title
    item.setSelected(True)
    qtbot.keyClick(widget.view, Qt.Key.Key_Backspace)
    assert not graph.has_node("a")


def test_blank_title_and_body_click_do_not_rename(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(
        Node(
            "a",
            "A",
            inputs=(Port("in", PortDirection.IN),),
            outputs=(Port("out", PortDirection.OUT),),
            position=(0, 0),
        )
    )
    _host, widget = _show(qtbot, graph)
    item = widget.scene.node_item("a")
    assert item is not None
    view = widget.view

    edit = _open_header(qtbot, widget, "a")
    header = view.mapFromScene(item.mapToScene(QPointF(48, 16)))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=header)
    assert item.title_editor() is edit
    qtbot.keyClicks(edit, "   ")
    qtbot.keyClick(edit, Qt.Key.Key_Return)
    assert item.title_editor() is None
    assert graph.node("a").title == "A"
    assert widget.history.can_undo is False

    body = view.mapFromScene(item.mapToScene(QPointF(48, 56)))
    qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=body)
    assert item.title_editor() is None

    edit = _open_header(qtbot, widget, "a")
    qtbot.keyClicks(edit, "Beta")
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    qtbot.waitUntil(lambda: item.title_editor() is None)
    assert graph.node("a").title == "Beta"

    edit = _open_header(qtbot, widget, "a")
    edit.setText("Typed")
    graph.change_node("a", title="External")
    assert item.title_editor() is None
    assert graph.node("a").title == "External"
    assert item.title == "External"
