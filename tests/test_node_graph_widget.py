"""Public NodeGraphWidget surface that interaction tests do not cover."""

from __future__ import annotations

from mtqt import NodeGraph, NodeGraphWidget


def test_node_graph_widget_defaults(qtbot) -> None:
    widget = NodeGraphWidget()
    qtbot.addWidget(widget)
    assert widget.objectName() == "NodeGraphWidget"
    assert widget.accessibleName() == "Node graph"
    minimum = widget.minimumSize()
    assert minimum.width() >= 320
    assert minimum.height() >= 240
    assert widget.graph.nodes() == ()
    assert widget.graph.edges() == ()
    assert widget.history.undo() is False
    assert widget.history.redo() is False


def test_node_graph_widget_set_graph_swaps_graph(qtbot) -> None:
    widget = NodeGraphWidget()
    qtbot.addWidget(widget)
    original = widget.graph
    replacement = NodeGraph()
    widget.set_graph(replacement)
    assert widget.graph is replacement
    assert original is not replacement


def test_node_graph_widget_frames_empty_graph(qtbot) -> None:
    widget = NodeGraphWidget()
    widget.resize(640, 480)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    widget.frame_all()
    widget.frame_selection()
