"""Scene that mirrors a :class:`~mtqt.widgets.node_graph.graph.NodeGraph`."""

from __future__ import annotations

from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QPointF, Signal
from PySide6.QtWidgets import QGraphicsScene, QGraphicsSceneMouseEvent, QWidget

from mtqt.widgets.node_graph.graph import (
    GraphChange,
    GraphEvent,
    Node,
    NodeGraph,
    NodeGraphError,
    Port,
    PortDirection,
)
from mtqt.widgets.node_graph.items import EdgeItem, NodeItem, PortItem

NodeContentFactory = Callable[[Node, QWidget | None], QWidget]
NodeFactory = Callable[[tuple[float, float]], Node]


def default_node_at(graph: NodeGraph, position: tuple[float, float]) -> Node:
    """A node with one ``any`` input and one ``any`` output at ``position``."""
    index = 1
    while graph.has_node(f"node-{index}"):
        index += 1
    return Node(
        id=f"node-{index}",
        title="Node",
        inputs=(Port("in", PortDirection.IN),),
        outputs=(Port("out", PortDirection.OUT),),
        position=position,
    )


class NodeGraphScene(QGraphicsScene):
    """Graphics scene bound to one :class:`NodeGraph`.

    The scene subscribes to the graph and creates a :class:`NodeItem` per node.
    With no ``content_factory``, a node is a title and its ports. A factory
    may embed a widget in the body and must not mutate the graph.

    Args:
        graph: DAG to display. Existing nodes and edges are drawn immediately.
        content_factory: Builds the widget embedded in a node. ``None`` leaves
            the body empty.
        parent: Qt owner.
        node_factory: Builds the node inserted by :meth:`add_node_at`.
            Defaults to :func:`default_node_at`.
    """

    connectionRejected = Signal(str)

    def __init__(
        self,
        graph: NodeGraph,
        content_factory: NodeContentFactory | None = None,
        parent: QWidget | None = None,
        *,
        node_factory: NodeFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._graph = graph
        self._factory = content_factory
        self._node_factory = node_factory
        self._nodes: dict[str, NodeItem] = {}
        self._edges: dict[str, EdgeItem] = {}
        self._draft: EdgeItem | None = None
        self._draft_port: PortItem | None = None
        self._rewire_edge_id = ""
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

    def set_content_factory(self, factory: NodeContentFactory | None) -> None:
        """Replace the factory and rebuild every node body.

        ``None`` clears the embedded widgets.
        """
        self._factory = factory
        for node_id, item in self._nodes.items():
            self._apply_content(item, self._graph.node(node_id))
            self._refresh_incident_edges(node_id)

    def set_node_factory(self, factory: NodeFactory | None) -> None:
        """Replace the factory used by :meth:`add_node_at`.

        ``None`` restores :func:`default_node_at`.
        """
        self._node_factory = factory

    def add_node_at(self, position: tuple[float, float]) -> Node:
        """Insert a node at a scene position and select it.

        Uses ``node_factory`` when one is set. The factory receives
        ``(x, y)`` and must return a node whose id is free. The default
        factory names nodes ``node-1``, ``node-2``, and so on, each with
        an ``in`` port and an ``out`` port of type ``any``.
        """
        if self._node_factory is not None:
            node = self._node_factory(position)
        else:
            node = default_node_at(self._graph, position)
        stored = self._graph.add_node(node)
        item = self._nodes.get(stored.id)
        if item is not None:
            self.clearSelection()
            item.setSelected(True)
        return stored

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
        self._start_rubber_band(port, rewire_edge_id="")

    def begin_rewire_from_input(self, port: PortItem) -> bool:
        """Lift the edge feeding ``port`` so it can be dropped elsewhere.

        Returns ``False`` when ``port`` is not a connected input. The edge
        stays in the graph until :meth:`finish_draft`.
        """
        if port.direction is not PortDirection.IN:
            return False
        edge = self._graph.incoming_edge(port.node_id, port.port_name)
        if edge is None:
            return False
        return self._begin_rewire(edge.id)

    def begin_rewire_from_edge(self, edge_item: EdgeItem) -> bool:
        """Lift ``edge_item`` so its free end can drop on another input.

        Grabs the mouse on the rubber-band curve. Returns ``False`` when
        the item is not a live edge.
        """
        if not edge_item.edge_id or not self._graph.has_edge(edge_item.edge_id):
            return False
        started = self._begin_rewire(edge_item.edge_id)
        if started and self._draft is not None:
            self._draft.grabMouse()
        return started

    def update_draft(self, pos: QPointF) -> None:
        """Move the free end of the connection drag to ``pos``."""
        if self._draft is None or self._draft_port is None:
            return
        self._draft.set_endpoints(self._draft_port.scenePos(), pos)
        target = self._port_at(pos)
        rejected = (
            target is not None
            and target.direction is PortDirection.IN
            and not self._drop_acceptable(self._draft_port, target)
        )
        self._draft.set_rejected(rejected)

    def finish_draft(self, pos: QPointF) -> None:
        """Commit the connection drag.

        A new drag connects when it lands on a compatible input. A rewire
        dropped on empty space disconnects. A rewire dropped on an
        incompatible port keeps the original edge and emits
        ``connectionRejected``.
        """
        source = self._draft_port
        rewire_id = self._rewire_edge_id
        self._discard_draft(restore_edge=False)
        if source is None:
            if rewire_id:
                self._show_edge(rewire_id)
                self._refresh_edge(rewire_id)
            return
        target = self._port_at(pos)
        if rewire_id and self._graph.has_edge(rewire_id):
            self._finish_rewire(source, rewire_id, target)
            return
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

    def title_editor_has_focus(self) -> bool:
        """Whether an open header editor owns Delete and Backspace."""
        return any(item.title_editor_has_focus() for item in self._nodes.values())

    def commit_renames(self, scene_pos: QPointF | None = None) -> None:
        """Commit header editors that do not contain ``scene_pos``.

        ``None`` commits every open editor. A blank title keeps the previous
        name. The editor under ``scene_pos`` stays open.
        """
        for item in list(self._nodes.values()):
            if item.title_editor() is None:
                continue
            if scene_pos is not None and item._title_editor_contains(scene_pos):
                continue
            item._commit_rename()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Commit a header rename when the press lands outside its editor."""
        self.commit_renames(event.scenePos())
        super().mousePressEvent(event)

    def rename_node(self, node_id: str, title: str) -> None:
        """Set the header text of ``node_id``.

        A blank title is ignored. The graph emits ``NODE_CHANGED`` when the
        text differs, so undo records the rename with other edits.
        """
        cleaned = title.strip()
        if not cleaned or not self._graph.has_node(node_id):
            return
        self._graph.change_node(node_id, title=cleaned)

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
        self._apply_content(item, node)
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
        self._apply_content(item, node)
        self._refresh_incident_edges(node_id)

    def _apply_content(self, item: NodeItem, node: Node) -> None:
        if self._factory is None:
            item.set_content(None)
            return
        item.set_content(self._factory(node, None))

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

    def _start_rubber_band(self, port: PortItem, *, rewire_edge_id: str) -> None:
        self._draft_port = port
        self._rewire_edge_id = rewire_edge_id
        draft = EdgeItem("")
        draft.set_draft(True)
        self.addItem(draft)
        origin = port.scenePos()
        draft.set_endpoints(origin, origin)
        self._draft = draft

    def _begin_rewire(self, edge_id: str) -> bool:
        edge = self._graph.edge(edge_id)
        source_item = self._nodes.get(edge.source_id)
        if source_item is None:
            return False
        output = source_item.port_item(edge.source_port, PortDirection.OUT)
        if output is None:
            return False
        self._cancel_draft()
        self._start_rubber_band(output, rewire_edge_id=edge_id)
        original = self._edges.get(edge_id)
        if original is not None:
            original.setVisible(False)
        return True

    def _drop_acceptable(self, source: PortItem, target: PortItem) -> bool:
        if self._rewire_edge_id and self._graph.has_edge(self._rewire_edge_id):
            edge = self._graph.edge(self._rewire_edge_id)
            if (
                target.node_id == edge.target_id
                and target.port_name == edge.target_port
                and source.node_id == edge.source_id
                and source.port_name == edge.source_port
            ):
                return True
        return self._graph.can_connect(
            source.node_id,
            source.port_name,
            target.node_id,
            target.port_name,
        )

    def _finish_rewire(
        self,
        source: PortItem,
        rewire_id: str,
        target: PortItem | None,
    ) -> None:
        edge = self._graph.edge(rewire_id)
        if target is None or target.direction is not PortDirection.IN:
            self._graph.disconnect(rewire_id)
            return
        if target.node_id == edge.target_id and target.port_name == edge.target_port:
            self._show_edge(rewire_id)
            self._refresh_edge(rewire_id)
            return
        origin = (
            edge.source_id,
            edge.source_port,
            edge.target_id,
            edge.target_port,
            edge.id,
        )
        self._graph.disconnect(rewire_id)
        try:
            self._graph.connect(
                source.node_id,
                source.port_name,
                target.node_id,
                target.port_name,
            )
        except NodeGraphError as exc:
            self._graph.connect(
                origin[0],
                origin[1],
                origin[2],
                origin[3],
                edge_id=origin[4],
            )
            self.connectionRejected.emit(str(exc))

    def _show_edge(self, edge_id: str) -> None:
        item = self._edges.get(edge_id)
        if item is not None:
            item.setVisible(True)

    def _discard_draft(self, *, restore_edge: bool) -> None:
        draft = self._draft
        rewire_id = self._rewire_edge_id
        self._draft = None
        self._draft_port = None
        self._rewire_edge_id = ""
        if draft is not None:
            if self.mouseGrabberItem() is draft:
                draft.ungrabMouse()
            if draft.scene() is not None:
                self.removeItem(draft)
        if restore_edge and rewire_id:
            self._show_edge(rewire_id)
            self._refresh_edge(rewire_id)

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
        self._discard_draft(restore_edge=True)

    def _clear_items(self) -> None:
        self._cancel_draft()
        self._nodes.clear()
        self._edges.clear()
        self.clear()
