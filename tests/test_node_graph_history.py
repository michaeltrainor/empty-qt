"""Undo and redo for the node graph."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLineEdit, QWidget

from mtqt import GraphHistory, NodeGraph, NodeGraphWidget
from mtqt.widgets import GraphHistory as WidgetsGraphHistory
from mtqt.widgets.node_graph import GraphHistory as PackageGraphHistory
from mtqt.widgets.node_graph import Node, Port, PortDirection


def test_graph_history_is_exported() -> None:
    assert GraphHistory is WidgetsGraphHistory is PackageGraphHistory


def _port_in(name: str = "in") -> Port:
    return Port(name, PortDirection.IN)


def _port_out(name: str = "out") -> Port:
    return Port(name, PortDirection.OUT)


def test_add_node_undo_and_redo() -> None:
    graph = NodeGraph()
    history = GraphHistory(graph)
    graph.add_node(Node("a", "Alpha", position=(8, 9), payload=5))
    history.seal()
    assert history.can_undo
    assert history.undo()
    assert not graph.has_node("a")
    assert history.can_redo
    assert history.redo()
    node = graph.node("a")
    assert node.title == "Alpha"
    assert node.position == (8, 9)
    assert node.payload == 5

    assert history.undo()
    graph.add_node(Node("b", "Beta"))
    history.seal()
    assert history.can_redo is False
    assert history.redo() is False
    assert graph.has_node("b")
    assert not graph.has_node("a")


def test_undo_delete_restores_node_and_edge_ids() -> None:
    graph = NodeGraph()
    payload = {"plate": 1}
    graph.add_node(Node("a", "A", outputs=(_port_out(),), position=(1, 2)))
    graph.add_node(
        Node(
            "b",
            "B",
            inputs=(_port_in(),),
            outputs=(_port_out("thru"),),
            position=(12, 34),
            payload=payload,
        )
    )
    graph.add_node(Node("c", "C", inputs=(_port_in(),), position=(3, 4)))
    graph.connect("a", "out", "b", "in", edge_id="in-b")
    graph.connect("b", "thru", "c", "in", edge_id="out-b")
    history = GraphHistory(graph)
    graph.remove_node("b")
    history.seal()
    assert not graph.has_node("b")
    assert not graph.has_edge("in-b")

    assert history.undo()
    restored = graph.node("b")
    assert restored.title == "B"
    assert restored.position == (12, 34)
    assert restored.payload is payload
    assert graph.edge("in-b").source_id == "a"
    assert graph.edge("in-b").target_id == "b"
    assert graph.edge("out-b").source_id == "b"
    assert graph.edge("out-b").target_id == "c"

    assert history.redo()
    assert not graph.has_node("b")
    assert not graph.has_edge("in-b")
    assert not graph.has_edge("out-b")


def test_join_moves_is_one_step_until_join_ends() -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A", position=(0.0, 0.0)))
    history = GraphHistory(graph)
    history.join_moves = True
    for point in ((1.0, 0.0), (2.0, 0.0), (3.0, 4.0)):
        graph.move_node("a", point)
        history.seal()
    assert history.undo()
    assert graph.node("a").position == (0.0, 0.0)
    assert history.undo() is False

    assert history.redo()
    assert graph.node("a").position == (3.0, 4.0)
    history.join_moves = False
    graph.move_node("a", (8.0, 9.0))
    history.seal()
    assert history.undo()
    assert graph.node("a").position == (3.0, 4.0)
    assert history.undo()
    assert graph.node("a").position == (0.0, 0.0)
    assert history.undo() is False


def test_rewire_and_change_undo_as_one_step() -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A", outputs=(_port_out(),), payload=1))
    graph.add_node(Node("b", "B", inputs=(_port_in(),)))
    graph.add_node(Node("c", "C", inputs=(_port_in(),)))
    graph.connect("a", "out", "b", "in", edge_id="keep")
    history = GraphHistory(graph)
    graph.disconnect("keep")
    graph.connect("a", "out", "c", "in", edge_id="next")
    graph.change_node("a", title="Alpha", payload=2)
    history.seal()

    assert history.undo()
    assert graph.edge("keep").target_id == "b"
    assert not graph.has_edge("next")
    assert graph.node("a").title == "A"
    assert graph.node("a").payload == 1
    assert history.redo()
    assert graph.edge("next").target_id == "c"
    assert not graph.has_edge("keep")
    assert graph.node("a").title == "Alpha"
    assert graph.node("a").payload == 2


def test_undo_when_empty_returns_false() -> None:
    history = GraphHistory(NodeGraph())
    assert history.can_undo is False
    assert history.can_redo is False
    assert history.undo() is False
    assert history.redo() is False
    history.seal()
    assert history.undo() is False


def test_bind_and_set_graph_clear_stacks(qtbot) -> None:
    graph = NodeGraph()
    history = GraphHistory(graph)
    graph.add_node(Node("a", "A"))
    history.seal()
    assert history.can_undo
    other = NodeGraph()
    other.add_node(Node("b", "B"))
    history.bind(other)
    assert history.can_undo is False
    assert history.can_redo is False
    assert history.undo() is False
    other.remove_node("b")
    history.seal()
    assert history.undo()
    assert other.has_node("b")

    widget = NodeGraphWidget(graph, frame_on_show=False)
    qtbot.addWidget(widget)
    assert widget.history.can_undo is False
    widget.graph.add_node(Node("c", "C"))
    widget.history.seal()
    assert widget.history.can_undo
    widget.set_graph(NodeGraph())
    assert widget.history.can_undo is False
    assert widget.history.can_redo is False
    assert widget.undo() is False
    assert widget.redo() is False


def test_undo_redo_shortcuts(qtbot) -> None:
    graph = NodeGraph()
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    widget.resize(480, 320)
    qtbot.addWidget(widget)
    host.show()
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    widget.view.setFocus()
    qtbot.waitUntil(widget.view.hasFocus)
    graph.add_node(Node("a", "A"))
    assert widget.history.has_pending
    qtbot.wait(1)
    assert widget.history.has_pending is False
    assert widget.history.can_undo

    view = widget.view
    qtbot.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not graph.has_node("a")
    qtbot.keyClick(view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert graph.has_node("a")
    qtbot.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not graph.has_node("a")
    qtbot.keyClick(
        view,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert graph.has_node("a")


def test_undo_shortcut_ignores_a_focused_editor(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(parent=host, frame_on_show=False)
    widget.resize(480, 320)
    qtbot.addWidget(widget)
    host.show()
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    widget.graph.add_node(Node("a", "A"))
    widget.history.seal()
    edit = QLineEdit(widget)
    qtbot.addWidget(edit)
    edit.show()
    edit.setFocus()
    qtbot.waitUntil(edit.hasFocus)
    qtbot.keyClick(widget.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert widget.graph.has_node("a")
    assert widget.undo()
    assert not widget.graph.has_node("a")


def test_left_drag_arms_join_moves_and_pan_does_not(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(parent=host, frame_on_show=False)
    widget.resize(400, 300)
    qtbot.addWidget(widget)
    host.show()
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    viewport = widget.view.viewport()

    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
    assert widget.history.join_moves is True
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(50, 40))
    assert widget.history.join_moves is False

    qtbot.mousePress(viewport, Qt.MouseButton.MiddleButton, pos=QPoint(30, 30))
    assert widget.history.join_moves is False
    qtbot.mouseRelease(viewport, Qt.MouseButton.MiddleButton, pos=QPoint(80, 60))
    assert widget.history.join_moves is False

    qtbot.mousePress(
        viewport,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        pos=QPoint(30, 30),
    )
    assert widget.history.join_moves is False
    qtbot.mouseRelease(
        viewport,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        pos=QPoint(80, 60),
    )
    assert widget.history.join_moves is False

    widget.view.setFocus()
    qtbot.waitUntil(widget.view.hasFocus)
    qtbot.keyPress(viewport, Qt.Key.Key_Space)
    assert widget.view.space_down
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
    assert widget.history.join_moves is False
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(70, 50))
    qtbot.keyRelease(viewport, Qt.Key.Key_Space)
