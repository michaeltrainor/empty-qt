"""DAG rules and the node-graph widget that embeds custom widgets."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QContextMenuEvent
from PySide6.QtWidgets import QGraphicsSceneHoverEvent, QLabel, QWidget

from mtqt import NodeGraph, NodeGraphWidget
from mtqt.widgets.node_graph import (
    CycleError,
    Node,
    NodeGraphError,
    Port,
    PortDirection,
)
from mtqt.widgets.node_graph.view import NodeGraphView


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


def test_rewire_moves_edge_and_drop_on_empty_disconnects(qtbot) -> None:
    graph = _chain()
    graph.add_node(Node("d", "D", inputs=(_port_in(),), position=(30, 0)))
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    source_in = _port(widget, "b", PortDirection.IN)
    free_in = _port(widget, "d", PortDirection.IN)

    assert widget.scene.begin_rewire_from_input(source_in)
    widget.scene.finish_draft(free_in.scenePos())
    assert graph.incoming_edge("b", "in") is None
    moved = graph.incoming_edge("d", "in")
    assert moved is not None
    assert moved.source_id == "a"

    edge_item = widget.scene.edge_item(moved.id)
    assert edge_item is not None
    assert widget.scene.begin_rewire_from_edge(edge_item)
    widget.scene.finish_draft(QPointF(-800, -800))
    assert graph.incoming_edge("d", "in") is None
    assert graph.incoming_edge("c", "in") is not None


def test_rewire_drop_on_same_port_keeps_edge(qtbot) -> None:
    graph = _chain()
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    inlet = _port(widget, "b", PortDirection.IN)
    original = graph.incoming_edge("b", "in")
    assert original is not None

    assert widget.scene.begin_rewire_from_input(inlet)
    widget.scene.finish_draft(inlet.scenePos())
    kept = graph.incoming_edge("b", "in")
    assert kept is not None
    assert kept.id == original.id
    item = widget.scene.edge_item(kept.id)
    assert item is not None
    assert item.isVisible()


def test_rewire_rejects_cycle_and_restores_edge(qtbot) -> None:
    graph = _chain()
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    inlet = _port(widget, "c", PortDirection.IN)
    occupied = _port(widget, "a", PortDirection.IN)
    assert not widget.scene.begin_rewire_from_input(occupied)

    with qtbot.waitSignal(widget.connectionRejected) as blocker:
        assert widget.scene.begin_rewire_from_input(inlet)
        widget.scene.finish_draft(occupied.scenePos())
    assert "cycle" in blocker.args[0]
    restored = graph.incoming_edge("c", "in")
    assert restored is not None
    assert restored.source_id == "b"
    assert restored.id == "edge-2"
    item = widget.scene.edge_item("edge-2")
    assert item is not None
    assert item.isVisible()


def test_add_node_at_uses_factory_and_default_ports(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(parent=host, frame_on_show=False)
    qtbot.addWidget(widget)

    created = widget.add_node_at((5, 6))
    assert created.id == "node-1"
    assert created.position == (5, 6)
    assert tuple(port.name for port in created.inputs) == ("in",)
    assert tuple(port.name for port in created.outputs) == ("out",)
    assert widget.current_node_id == "node-1"
    assert widget.scene.node_item("node-1") is not None

    again = widget.add_node_at((1, 2))
    assert again.id == "node-2"

    def factory(position: tuple[float, float]) -> Node:
        return Node("made", "Made", position=position)

    widget.set_node_factory(factory)
    custom = widget.add_node_at((8, 9))
    assert custom.id == "made"
    assert custom.position == (8, 9)
    assert widget.current_node_id == "made"


def test_background_menu_adds_node_and_item_click_does_not(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(parent=host, frame_on_show=False)
    widget.resize(640, 480)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    view = widget.view
    opened: list[tuple[float, float]] = []

    def record(_global_pos: QPoint, scene_position: tuple[float, float]) -> None:
        opened.append(scene_position)

    view._show_background_menu = record  # type: ignore[method-assign]
    _context_menu(view, QPoint(8, 8))
    assert len(opened) == 1
    widget.add_node_at(opened[0])
    assert [node.id for node in widget.graph.nodes()] == ["node-1"]

    node_item = widget.scene.node_item("node-1")
    assert node_item is not None
    on_node = view.mapFromScene(node_item.sceneBoundingRect().center())
    _context_menu(view, on_node)
    assert opened == [opened[0]]


def test_dragging_an_input_port_rewires_the_edge(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A", outputs=(_port_out(),), position=(0, 80)))
    graph.add_node(Node("b", "B", inputs=(_port_in(),), position=(360, 0)))
    graph.add_node(Node("c", "C", inputs=(_port_in(),), position=(360, 220)))
    graph.connect("a", "out", "b", "in")
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    widget.resize(900, 600)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    view = widget.view
    view.set_zoom(1)
    start = _port(widget, "b", PortDirection.IN).sceneBoundingRect().center()
    end = _port(widget, "c", PortDirection.IN).sceneBoundingRect().center()
    view.centerOn(start)
    viewport = view.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(start))
    qtbot.mouseMove(viewport, view.mapFromScene(end))
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(end))
    moved = graph.incoming_edge("c", "in")
    assert moved is not None
    assert moved.source_id == "a"
    assert graph.incoming_edge("b", "in") is None


def test_hover_glows_the_node_and_brightens_the_edge(qtbot) -> None:
    graph = _chain()
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    node = widget.scene.node_item("b")
    edge = widget.scene.edge_item("edge-1")
    assert node is not None
    assert edge is not None
    resting = edge.pen().color().name()

    node.hoverEnterEvent(_hover(QEvent.Type.GraphicsSceneHoverEnter, QPointF(0, 0)))
    assert node.hovered
    inside = node.mapToScene(node.boundingRect().center())
    node.hoverLeaveEvent(_hover(QEvent.Type.GraphicsSceneHoverLeave, inside))
    assert node.hovered
    outside = QPointF(-8000, -8000)
    node.hoverLeaveEvent(_hover(QEvent.Type.GraphicsSceneHoverLeave, outside))
    assert not node.hovered

    edge.hoverEnterEvent(_hover(QEvent.Type.GraphicsSceneHoverEnter, QPointF(0, 0)))
    assert edge.hovered
    assert edge.pen().color().name() != resting
    assert edge.pen().color().lightness() > QColor(resting).lightness()
    edge.hoverLeaveEvent(_hover(QEvent.Type.GraphicsSceneHoverLeave, QPointF(0, 0)))
    assert not edge.hovered
    assert edge.pen().color().name() == resting

    node.hoverEnterEvent(_hover(QEvent.Type.GraphicsSceneHoverEnter, QPointF(0, 0)))
    widget.resize(640, 480)
    widget.show()
    qtbot.waitExposed(widget)
    assert not widget.grab().isNull()


def test_f_frames_the_selection(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A", outputs=(_port_out(),), position=(0, 0)))
    graph.add_node(Node("b", "B", inputs=(_port_in(),), position=(2400, 1600)))
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    widget.resize(640, 480)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    view = widget.view
    qtbot.waitUntil(lambda: view.viewport().width() > 400)
    view.set_zoom(1)
    node_a = widget.scene.node_item("a")
    node_b = widget.scene.node_item("b")
    assert node_a is not None
    assert node_b is not None

    bounds = widget.scene.itemsBoundingRect()
    qtbot.keyClick(view, Qt.Key.Key_F)
    framed_all = _view_center(view)
    assert abs(framed_all.x() - bounds.center().x()) < 30
    assert abs(framed_all.y() - bounds.center().y()) < 30

    node_b.setSelected(True)
    qtbot.keyClick(view, Qt.Key.Key_F)
    center = _view_center(view)
    target = node_b.sceneBoundingRect().center()
    other = node_a.sceneBoundingRect().center()
    assert abs(center.x() - target.x()) < 30
    assert abs(center.y() - target.y()) < 30
    assert abs(center.x() - other.x()) > 400

    framed = _view_center(view)
    qtbot.keyClick(view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    held = _view_center(view)
    assert abs(framed.x() - held.x()) < 2
    assert abs(framed.y() - held.y()) < 2

    node_a.setSelected(True)
    qtbot.keyClick(view, Qt.Key.Key_F)
    both = _view_center(view)
    midpoint = (target.x() + other.x()) / 2
    assert abs(both.x() - midpoint) < abs(both.x() - target.x())


def _view_center(view: NodeGraphView) -> QPointF:
    return view.mapToScene(view.viewport().rect().center())


def _hover(kind: QEvent.Type, scene_pos: QPointF) -> QGraphicsSceneHoverEvent:
    event = QGraphicsSceneHoverEvent(kind)
    event.setScenePos(scene_pos)
    event.setPos(scene_pos)
    return event


def _context_menu(view: NodeGraphView, viewport_pos: QPoint) -> None:
    global_pos = view.viewport().mapToGlobal(viewport_pos)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, viewport_pos, global_pos)
    view.contextMenuEvent(event)


def _port(widget: NodeGraphWidget, node_id: str, direction: PortDirection):
    item = widget.scene.node_item(node_id)
    assert item is not None
    port = item.port_item("in" if direction is PortDirection.IN else "out", direction)
    assert port is not None
    return port
