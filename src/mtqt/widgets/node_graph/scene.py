"""Scene that mirrors a :class:`~mtqt.widgets.node_graph.graph.NodeGraph`."""

from __future__ import annotations

from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QPointF, Signal
from PySide6.QtWidgets import QGraphicsScene, QWidget

from mtqt.widgets.node_graph.graph import (
    GraphChange,
    GraphEvent,
    Node,
    NodeGraph,
    NodeGraphError,
    PortDirection,
)
from mtqt.widgets.node_graph.items import EdgeItem, NodeItem, PortItem

NodeContentFactory = Callable[[Node, QWidget | None], QWidget]


class NodeGraphScene(QGraphicsScene):
    """Graphics scene bound to one :class:`NodeGraph`.

    The scene subscribes to the graph and creates a :class:`NodeItem` per node.
    Each item embeds the widget returned by ``content_factory``. The factory
    must not mutate the graph.

    Args:
        graph: DAG to display. Existing nodes and edges are drawn immediately.
        content_factory: Builds the widget embedded in a node.
        parent: Qt owner.
    """

    connectionRejected = Signal(str)

    def __init__(
        self,
        graph: NodeGraph,
        content_factory: NodeContentFactory,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._graph = graph
        self._factory = content_factory
        self._nodes: dict[str, NodeItem] = {}
        self._edges: dict[str, EdgeItem] = {}
        self._draft: EdgeItem | None = None
        self._draft_port: PortItem | None = None
        self._from_item = False
        self.setSceneRect(-4000, -4000, 8000, 8000)
        self._populate()
        self._graph.subscribe(self.apply_event)

    @property
    def graph(self) -> NodeGraph:
        """Graph this scene is displaying."""
        return self._graph

    def node_item(self, node_id: str) -> NodeItem | None:
        """Graphics item for ``node_id``, or ``None`` if it is not drawn."""
        return self._nodes.get(node_id)

    def edge_item(self, edge_id: str) -> EdgeItem | None:
        """Graphics item for ``edge_id``, or ``None`` if it is not drawn."""
        return self._edges.get(edge_id)

    def content_widget(self, node_id: str) -> QWidget | None:
        """Widget embedded in ``node_id``."""
        item = self._nodes.get(node_id)
        if item is None:
            return None
        return item.content_widget()

    def set_content_factory(self, factory: NodeContentFactory) -> None:
        """Replace the factory and rebuild every embedded widget."""
        self._factory = factory
        for node_id, item in self._nodes.items():
            item.set_content(factory(self._graph.node(node_id), None))
            self._refresh_incident_edges(node_id)

    def bind(self, graph: NodeGraph) -> None:
        """Drop the current drawing and display ``graph``."""
        self._graph.unsubscribe(self.apply_event)
        self._graph = graph
        self._clear_items()
        self._populate()
        self._graph.subscribe(self.apply_event)

    def apply_event(self, event: GraphEvent) -> None:
        """Apply one graph notification to the scene."""
        if not shiboken6.isValid(self):
            return
        if event.kind is GraphChange.NODE_ADDED:
            self._add_node_item(self._graph.node(event.node_id))
            return
        if event.kind is GraphChange.NODE_REMOVED:
            self._cancel_draft()
            self._remove_node_item(event.node_id)
            return
        if event.kind is GraphChange.NODE_MOVED:
            item = self._nodes.get(event.node_id)
            if item is None:
                return
            if not self._from_item:
                position = self._graph.node(event.node_id).position
                item.apply_position(QPointF(*position))
            self._refresh_incident_edges(event.node_id)
            return
        if event.kind is GraphChange.NODE_CHANGED:
            self._rebuild_node(event.node_id)
            return
        if event.kind is GraphChange.EDGE_ADDED:
            self._add_edge_item(event.edge_id)
            return
        if event.kind is GraphChange.EDGE_REMOVED:
            self._remove_edge_item(event.edge_id)

    def begin_draft(self, port: PortItem) -> None:
        """Start a connection drag from an output port."""
        if port.direction is not PortDirection.OUT:
            return
        self._cancel_draft()
        self._draft_port = port
        draft = EdgeItem("")
        draft.set_draft(True)
        self.addItem(draft)
        origin = port.scenePos()
        draft.set_endpoints(origin, origin)
        self._draft = draft

    def update_draft(self, pos: QPointF) -> None:
        """Move the free end of the connection drag to ``pos``."""
        if self._draft is None or self._draft_port is None:
            return
        self._draft.set_endpoints(self._draft_port.scenePos(), pos)
        target = self._port_at(pos)
        rejected = False
        if target is not None and target.direction is PortDirection.IN:
            rejected = not self._graph.can_connect(
                self._draft_port.node_id,
                self._draft_port.port_name,
                target.node_id,
                target.port_name,
            )
        self._draft.set_rejected(rejected)

    def finish_draft(self, pos: QPointF) -> None:
        """Commit the connection drag, or cancel it when the drop is invalid."""
        source = self._draft_port
        self._cancel_draft()
        if source is None:
            return
        target = self._port_at(pos)
        if target is None or target.direction is not PortDirection.IN:
            return
        try:
            self._graph.connect(
                source.node_id,
                source.port_name,
                target.node_id,
                target.port_name,
            )
        except NodeGraphError as exc:
            self.connectionRejected.emit(str(exc))

    def node_moved(self, node_id: str, position: tuple[float, float]) -> None:
        """Write a dragged item position back to the graph."""
        self._from_item = True
        try:
            self._graph.move_node(node_id, position)
        finally:
            self._from_item = False

    def delete_selected(self) -> None:
        """Remove selected edges, then selected nodes, through the graph."""
        node_ids: list[str] = []
        edge_ids: list[str] = []
        for item in self.selectedItems():
            if isinstance(item, NodeItem):
                node_ids.append(item.node_id)
            elif isinstance(item, EdgeItem) and item.edge_id:
                edge_ids.append(item.edge_id)
        for edge_id in edge_ids:
            if self._graph.has_edge(edge_id):
                self._graph.disconnect(edge_id)
        for node_id in node_ids:
            if self._graph.has_node(node_id):
                self._graph.remove_node(node_id)

    def _populate(self) -> None:
        for node in self._graph.nodes():
            self._add_node_item(node)
        for edge in self._graph.edges():
            self._add_edge_item(edge.id)

    def _add_node_item(self, node: Node) -> None:
        if node.id in self._nodes:
            return
        item = NodeItem(node.id, node.title)
        self.addItem(item)
        item.set_ports(
            tuple((port.name, port.data_type) for port in node.inputs),
            tuple((port.name, port.data_type) for port in node.outputs),
        )
        item.set_content(self._factory(node, None))
        item.apply_position(QPointF(*node.position))
        self._nodes[node.id] = item

    def _remove_node_item(self, node_id: str) -> None:
        item = self._nodes.pop(node_id, None)
        if item is None:
            return
        self.removeItem(item)
        item.deleteLater()

    def _rebuild_node(self, node_id: str) -> None:
        item = self._nodes.get(node_id)
        if item is None:
            return
        node = self._graph.node(node_id)
        item.set_title(node.title)
        item.set_ports(
            tuple((port.name, port.data_type) for port in node.inputs),
            tuple((port.name, port.data_type) for port in node.outputs),
        )
        item.set_content(self._factory(node, None))
        self._refresh_incident_edges(node_id)

    def _add_edge_item(self, edge_id: str) -> None:
        if edge_id in self._edges or not self._graph.has_edge(edge_id):
            return
        item = EdgeItem(edge_id)
        self.addItem(item)
        self._edges[edge_id] = item
        self._refresh_edge(edge_id)

    def _remove_edge_item(self, edge_id: str) -> None:
        item = self._edges.pop(edge_id, None)
        if item is None:
            return
        self.removeItem(item)

    def _refresh_incident_edges(self, node_id: str) -> None:
        if not self._graph.has_node(node_id):
            return
        for edge in (*self._graph.incoming(node_id), *self._graph.outgoing(node_id)):
            self._refresh_edge(edge.id)

    def _refresh_edge(self, edge_id: str) -> None:
        item = self._edges.get(edge_id)
        if item is None or not self._graph.has_edge(edge_id):
            return
        edge = self._graph.edge(edge_id)
        source = self._nodes.get(edge.source_id)
        target = self._nodes.get(edge.target_id)
        if source is None or target is None:
            return
        item.set_endpoints(
            source.anchor(edge.source_port, PortDirection.OUT),
            target.anchor(edge.target_port, PortDirection.IN),
        )

    def _port_at(self, pos: QPointF) -> PortItem | None:
        for item in self.items(pos):
            if isinstance(item, PortItem):
                return item
            if isinstance(item, NodeItem):
                for child in item.childItems():
                    rect = child.sceneBoundingRect()
                    if isinstance(child, PortItem) and rect.contains(pos):
                        return child
        return None

    def _cancel_draft(self) -> None:
        if self._draft is not None:
            self.removeItem(self._draft)
        self._draft = None
        self._draft_port = None

    def _clear_items(self) -> None:
        self._cancel_draft()
        self._nodes.clear()
        self._edges.clear()
        self.clear()
