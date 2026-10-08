"""Port names drawn beside sockets on a node."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from mtqt.widgets.node_graph import (
    Node,
    NodeGraph,
    NodeGraphWidget,
    Port,
    PortDirection,
)


def _widget(qtbot, graph: NodeGraph) -> tuple[QWidget, NodeGraphWidget]:
    host = QWidget()
    qtbot.addWidget(host)
    widget = NodeGraphWidget(graph, host, frame_on_show=False)
    qtbot.addWidget(widget)
    return host, widget


def test_title_and_opposite_ports_keep_their_padding(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(
        Node(
            "grade",
            "Grade",
            inputs=(Port("path", PortDirection.IN),),
            outputs=(Port("image", PortDirection.OUT),),
        )
    )
    graph.add_node(Node("ingest", "Ingest", outputs=(Port("path", PortDirection.OUT),)))
    graph.add_node(Node("short", "In", outputs=(Port("path", PortDirection.OUT),)))
    graph.add_node(
        Node("image-only", "Grade", outputs=(Port("image", PortDirection.OUT),))
    )
    _host, widget = _widget(qtbot, graph)
    grade = widget.scene.node_item("grade")
    ingest = widget.scene.node_item("ingest")
    short = widget.scene.node_item("short")
    image_only = widget.scene.node_item("image-only")
    assert grade is not None
    assert ingest is not None
    assert short is not None
    assert image_only is not None
    assert ingest.boundingRect().width() == short.boundingRect().width()
    assert ingest.boundingRect().width() >= 180
    assert grade.boundingRect().width() >= 180
    assert image_only.boundingRect().width() >= 180


def test_long_input_name_widens_the_node(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(
        Node(
            "long",
            "Node",
            inputs=(Port("upstream_plate_exposure", PortDirection.IN),),
        )
    )
    graph.add_node(Node("short", "Node", inputs=(Port("a", PortDirection.IN),)))
    _host, widget = _widget(qtbot, graph)
    long_item = widget.scene.node_item("long")
    short_item = widget.scene.node_item("short")
    assert long_item is not None
    assert short_item is not None
    assert long_item.boundingRect().width() > short_item.boundingRect().width()
    port = long_item.port_item("upstream_plate_exposure", PortDirection.IN)
    assert port is not None
    assert port.pos().x() == 0.0
    assert port.toolTip() == "in: upstream_plate_exposure (any)"

    widget.resize(640, 480)
    widget.show()
    qtbot.waitExposed(widget)
    assert not widget.grab().isNull()


def test_long_output_name_keeps_the_socket_on_the_right_edge(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(
        Node(
            "long",
            "Node",
            outputs=(Port("rendered_output_image", PortDirection.OUT),),
        )
    )
    graph.add_node(Node("short", "Node", outputs=(Port("o", PortDirection.OUT),)))
    _host, widget = _widget(qtbot, graph)
    long_item = widget.scene.node_item("long")
    short_item = widget.scene.node_item("short")
    assert long_item is not None
    assert short_item is not None
    long_port = long_item.port_item("rendered_output_image", PortDirection.OUT)
    short_port = short_item.port_item("o", PortDirection.OUT)
    assert long_port is not None
    assert short_port is not None
    assert long_item.boundingRect().width() > short_item.boundingRect().width()
    assert long_port.pos().x() > short_port.pos().x()
    rect = long_item.boundingRect()
    assert long_port.pos().x() == rect.left() + rect.right()


def test_set_ports_with_a_longer_name_grows_the_node(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(Node("n", "Node", inputs=(Port("a", PortDirection.IN),)))
    _host, widget = _widget(qtbot, graph)
    item = widget.scene.node_item("n")
    assert item is not None
    before = item.boundingRect().width()
    item.set_ports((("upstream_plate_exposure", "float"),), ())
    item.relayout()
    assert item.boundingRect().width() > before
    port = item.port_item("upstream_plate_exposure", PortDirection.IN)
    assert port is not None
    assert port.toolTip() == "in: upstream_plate_exposure (float)"
