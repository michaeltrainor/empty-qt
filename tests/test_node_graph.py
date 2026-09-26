"""DAG rules and the node-graph widget that embeds custom widgets."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from mtqt import NodeGraph, NodeGraphWidget
from mtqt.widgets.node_graph import (
    CycleError,
    Node,
    NodeGraphError,
    Port,
    PortDirection,
)


def _port_in(name: str = "in", data_type: str = "any") -> Port:
    return Port(name, PortDirection.IN, data_type)


def _port_out(name: str = "out", data_type: str = "any") -> Port:
    return Port(name, PortDirection.OUT, data_type)


def _chain() -> NodeGraph:
    graph = NodeGraph()
    graph.add_node(
        Node("a", "A", inputs=(_port_in(),), outputs=(_port_out(),), position=(0, 0))
    )
    graph.add_node(
        Node("b", "B", inputs=(_port_in(),), outputs=(_port_out(),), position=(10, 0))
    )
    graph.add_node(
        Node("c", "C", inputs=(_port_in(),), outputs=(_port_out(),), position=(20, 0))
    )
    graph.connect("a", "out", "b", "in")
    graph.connect("b", "out", "c", "in")
    return graph


def test_topological_order_and_queries() -> None:
    graph = _chain()
    assert graph.topological_order() == ("a", "b", "c")
    assert graph.upstream("c") == ("a", "b")
    assert graph.downstream("a") == ("b", "c")
    assert graph.predecessors("b") == ("a",)
    assert graph.successors("b") == ("c",)
    assert len(graph.incoming("c")) == 1
    assert graph.outgoing("a")[0].target_id == "b"


def test_connect_rejects_cycle_occupied_input_and_type_mismatch() -> None:
    graph = _chain()
    with pytest.raises(CycleError):
        graph.connect("c", "out", "a", "in")
    with pytest.raises(CycleError):
        graph.connect("b", "out", "b", "in")
    with pytest.raises(NodeGraphError, match="already connected"):
        graph.connect("a", "out", "b", "in", edge_id="again")

    typed = NodeGraph()
    typed.add_node(Node("src", "Src", outputs=(_port_out("out", "image"),)))
    typed.add_node(Node("dst", "Dst", inputs=(_port_in("in", "mesh"),)))
    with pytest.raises(NodeGraphError, match="not compatible"):
        typed.connect("src", "out", "dst", "in")
    assert not typed.can_connect("src", "out", "dst", "in")

    loose = NodeGraph(type_compatible=lambda _source, _target: True)
    loose.add_node(Node("src", "Src", outputs=(_port_out("out", "image"),)))
    loose.add_node(Node("dst", "Dst", inputs=(_port_in("in", "mesh"),)))
    edge = loose.connect("src", "out", "dst", "in")
    assert edge.id == "edge-1"


def test_any_type_matches_and_outputs_fan_out() -> None:
    graph = NodeGraph()
    graph.add_node(Node("src", "Src", outputs=(_port_out("out", "image"),)))
    graph.add_node(Node("left", "Left", inputs=(_port_in("in", "any"),)))
    graph.add_node(Node("right", "Right", inputs=(_port_in("in", "image"),)))
    graph.connect("src", "out", "left", "in")
    graph.connect("src", "out", "right", "in")
    assert graph.successors("src") == ("left", "right")


def test_remove_node_drops_incident_edges() -> None:
    graph = _chain()
    removed: list[str] = []
    graph.subscribe(lambda event: removed.append(event.edge_id or event.node_id))
    graph.remove_node("b")
    assert not graph.has_node("b")
    assert graph.edges() == ()
    assert graph.topological_order() == ("a", "c")
    assert "b" in removed


def test_add_node_rejects_duplicate_and_bad_ports() -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A"))
    with pytest.raises(NodeGraphError, match="duplicate"):
        graph.add_node(Node("a", "Again"))
    with pytest.raises(NodeGraphError, match="direction"):
        graph.add_node(Node("b", "B", inputs=(_port_out(),)))
    with pytest.raises(NodeGraphError, match="duplicate"):
        graph.add_node(Node("c", "C", inputs=(_port_in("in"), _port_in("in"))))
    with pytest.raises(ValueError, match="port name"):
        Port("  ", PortDirection.IN)


def test_arrange_and_round_trip() -> None:
    graph = _chain()
    graph.arrange(origin=(0, 0), x_gap=100, y_gap=50)
    assert graph.node("a").position == (0, 0)
    assert graph.node("b").position == (100, 0)
    assert graph.node("c").position == (200, 0)

    restored = NodeGraph.from_dict(graph.to_dict())
    assert restored.topological_order() == ("a", "b", "c")
    assert restored.node("b").position == (100, 0)
    assert restored.edge("edge-1").source_id == "a"

    with pytest.raises(NodeGraphError, match="mapping"):
        NodeGraph.from_dict([])  # type: ignore[arg-type]


def test_change_node_notifies_listeners() -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A", payload=1))
    kinds: list[str] = []
    graph.subscribe(lambda event: kinds.append(event.kind.value))
    graph.change_node("a", title="Alpha", payload=2, position=(4, 5))
    assert graph.node("a").title == "Alpha"
    assert graph.node("a").payload == 2
    assert graph.node("a").position == (4, 5)
    assert "node_changed" in kinds
    assert "node_moved" in kinds
    with pytest.raises(NodeGraphError, match="unknown node"):
        graph.node("missing")
    with pytest.raises(NodeGraphError, match="unknown edge"):
        graph.disconnect("missing")


def test_widget_embeds_factory_and_follows_graph(qtbot) -> None:
    graph = _chain()
    seen: list[str] = []

    def factory(node: Node, parent: QWidget | None) -> QLabel:
        seen.append(node.id)
        label = QLabel(node.title, parent)
        label.setObjectName(f"content-{node.id}")
        return label

    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, content_factory=factory, frame_on_show=False)
    qtbot.addWidget(widget)
    assert widget.objectName() == "NodeGraphWidget"
    assert seen == ["a", "b", "c"]
    content = widget.content_widget("b")
    assert isinstance(content, QLabel)
    assert content.objectName() == "content-b"
    assert widget.scene.edge_item("edge-1") is not None

    graph.add_node(Node("d", "D", inputs=(_port_in(),)))
    graph.connect("c", "out", "d", "in")
    assert widget.scene.node_item("d") is not None
    assert widget.graph.has_edge("edge-3")

    graph.move_node("a", (30, 40))
    item = widget.scene.node_item("a")
    assert item is not None
    assert item.pos().x() == pytest.approx(30)
    assert item.pos().y() == pytest.approx(40)


def test_widget_rejects_cycle_from_a_port_drop(qtbot) -> None:
    graph = _chain()
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    source = widget.scene.node_item("c")
    target = widget.scene.node_item("a")
    assert source is not None and target is not None
    output = source.port_item("out", PortDirection.OUT)
    inlet = target.port_item("in", PortDirection.IN)
    assert output is not None and inlet is not None

    with qtbot.waitSignal(widget.connectionRejected) as blocker:
        widget.scene.begin_draft(output)
        widget.scene.finish_draft(inlet.scenePos())
    assert "cycle" in blocker.args[0]
    assert len(graph.edges()) == 2


def test_widget_delete_and_selection(qtbot) -> None:
    graph = _chain()
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    item = widget.scene.node_item("b")
    assert item is not None
    with qtbot.waitSignal(widget.currentNodeChanged) as blocker:
        item.setSelected(True)
    assert blocker.args == ["b"]
    assert widget.current_node_id == "b"

    with qtbot.waitSignal(widget.graphChanged):
        qtbot.keyClick(widget.view, Qt.Key.Key_Delete)
    assert not graph.has_node("b")
    assert widget.scene.node_item("b") is None
    assert graph.edges() == ()


def test_widget_set_graph_and_factory(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(parent=host, frame_on_show=False)
    qtbot.addWidget(widget)
    assert widget.graph.nodes() == ()

    replacement = NodeGraph()
    replacement.add_node(Node("only", "Only", payload="plate"))
    widget.set_graph(replacement)

    def factory(node: Node, parent: QWidget | None) -> QLabel:
        return QLabel(str(node.payload), parent)

    widget.set_content_factory(factory)
    content = widget.content_widget("only")
    assert content is not None
    assert content.text() == "plate"  # type: ignore[attr-defined]
    widget.view.set_zoom(4)
    assert widget.view.zoom == pytest.approx(2.5)
    widget.view.set_zoom(0.01)
    assert widget.view.zoom == pytest.approx(0.25)
