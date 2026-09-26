"""Directed acyclic graph used by :class:`~mtqt.widgets.node_graph.NodeGraphWidget`.

The graph does not import Qt. Build and evaluate it in tool code, then hand
the same object to the widget.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

type GraphListener = Callable[["GraphEvent"], None]
type TypeCompatible = Callable[[str, str], bool]

_UNSET = object()


class PortDirection(StrEnum):
    """Which side of a node a port sits on."""

    IN = "in"
    OUT = "out"


class GraphChange(StrEnum):
    """Kind of mutation delivered to :data:`GraphListener` callbacks."""

    NODE_ADDED = "node_added"
    NODE_REMOVED = "node_removed"
    NODE_MOVED = "node_moved"
    NODE_CHANGED = "node_changed"
    EDGE_ADDED = "edge_added"
    EDGE_REMOVED = "edge_removed"


class NodeGraphError(ValueError):
    """A graph mutation violated a structural rule."""


class CycleError(NodeGraphError):
    """Connecting these nodes would introduce a directed cycle."""

    def __init__(self, source_id: str, target_id: str) -> None:
        self.source_id = source_id
        self.target_id = target_id
        super().__init__(
            f"connecting {source_id!r} to {target_id!r} would introduce a cycle"
        )


def ports_compatible(source_type: str, target_type: str) -> bool:
    """Return whether an output type may feed an input type.

    ``any`` matches every type. Otherwise the strings must be equal.
    """
    return source_type == "any" or target_type == "any" or source_type == target_type


@dataclass(frozen=True, slots=True)
class Port:
    """A named socket on a node.

    Args:
        name: Stable port name, unique among ports of the same direction.
        direction: ``in`` or ``out``.
        data_type: Type tag used by :func:`ports_compatible`. Defaults to ``any``.
    """

    name: str
    direction: PortDirection
    data_type: str = "any"

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            msg = "port name must be a non-empty string"
            raise ValueError(msg)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "direction", PortDirection(self.direction))
        data_type = self.data_type.strip()
        if not data_type:
            msg = "port data_type must be a non-empty string"
            raise ValueError(msg)
        object.__setattr__(self, "data_type", data_type)


@dataclass(slots=True)
class Node:
    """One vertex in a :class:`NodeGraph`.

    The graph copies this object on :meth:`NodeGraph.add_node`. Mutate the
    stored instance only through the graph so a view can follow along.

    Args:
        id: Stable node id.
        title: Label drawn in the node header.
        inputs: Incoming ports. Each input accepts one edge.
        outputs: Outgoing ports. Each output may fan out.
        payload: Tool-specific data. The widget factory may read this.
        position: Scene position ``(x, y)`` in pixels.
    """

    id: str
    title: str
    inputs: tuple[Port, ...] = ()
    outputs: tuple[Port, ...] = ()
    payload: object = None
    position: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed connection from an output port to an input port."""

    id: str
    source_id: str
    source_port: str
    target_id: str
    target_port: str


@dataclass(frozen=True, slots=True)
class GraphEvent:
    """Notification passed to graph listeners.

    ``node_id`` is set for node changes. ``edge_id`` is set for edge changes.
    """

    kind: GraphChange
    node_id: str = ""
    edge_id: str = ""


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{label} must be a non-empty string"
        raise NodeGraphError(msg)
    return value.strip()


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        msg = f"{label} must be a mapping"
        raise NodeGraphError(msg)
    return value


def _as_sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    msg = f"{label} must be a sequence"
    raise NodeGraphError(msg)


class NodeGraph:
    """Directed acyclic graph of :class:`Node` and :class:`Edge` objects.

    Outputs may connect to many inputs. Each input accepts one edge. Adding
    an edge that would loop raises :class:`CycleError`. Listeners run on the
    calling thread; attach a view only from the GUI thread.

    Args:
        type_compatible: Predicate ``(source_type, target_type) -> bool``.
            Defaults to :func:`ports_compatible`.
    """

    def __init__(self, type_compatible: TypeCompatible | None = None) -> None:
        self._compatible = type_compatible or ports_compatible
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}
        self._listeners: list[GraphListener] = []
        self._edge_seq = 0

    def subscribe(self, listener: GraphListener) -> None:
        """Register ``listener``. Duplicate registration is ignored."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: GraphListener) -> None:
        """Remove ``listener`` if it is registered."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            return

    def has_node(self, node_id: str) -> bool:
        """Return whether ``node_id`` is in the graph."""
        return node_id in self._nodes

    def has_edge(self, edge_id: str) -> bool:
        """Return whether ``edge_id`` is in the graph."""
        return edge_id in self._edges

    def node(self, node_id: str) -> Node:
        """Return the stored node.

        Raises:
            NodeGraphError: If ``node_id`` is unknown.
        """
        return self._require_node(node_id)

    def nodes(self) -> tuple[Node, ...]:
        """Nodes in insertion order."""
        return tuple(self._nodes.values())

    def edge(self, edge_id: str) -> Edge:
        """Return the stored edge.

        Raises:
            NodeGraphError: If ``edge_id`` is unknown.
        """
        try:
            return self._edges[edge_id]
        except KeyError as exc:
            msg = f"unknown edge {edge_id!r}"
            raise NodeGraphError(msg) from exc

    def edges(self) -> tuple[Edge, ...]:
        """Edges in insertion order."""
        return tuple(self._edges.values())

    def add_node(self, node: Node) -> Node:
        """Copy ``node`` into the graph and return the stored instance.

        Raises:
            NodeGraphError: If the id is empty, already used, or ports are invalid.
        """
        node_id = _require_text(node.id, "node id")
        if node_id in self._nodes:
            msg = f"duplicate node id {node_id!r}"
            raise NodeGraphError(msg)
        stored = Node(
            id=node_id,
            title=node.title,
            inputs=self._copy_ports(node.inputs, PortDirection.IN),
            outputs=self._copy_ports(node.outputs, PortDirection.OUT),
            payload=node.payload,
            position=self._copy_position(node.position),
        )
        self._nodes[stored.id] = stored
        self._emit(GraphChange.NODE_ADDED, node_id=stored.id)
        return stored

    def remove_node(self, node_id: str) -> None:
        """Remove a node and every edge that touches it.

        Raises:
            NodeGraphError: If ``node_id`` is unknown.
        """
        self._require_node(node_id)
        incident = [
            edge.id
            for edge in self._edges.values()
            if edge.source_id == node_id or edge.target_id == node_id
        ]
        for edge_id in incident:
            self._remove_edge(edge_id)
        del self._nodes[node_id]
        self._emit(GraphChange.NODE_REMOVED, node_id=node_id)

    def move_node(self, node_id: str, position: tuple[float, float]) -> None:
        """Set a node's scene position.

        Raises:
            NodeGraphError: If ``node_id`` is unknown.
        """
        node = self._require_node(node_id)
        point = self._copy_position(position)
        if point == node.position:
            return
        node.position = point
        self._emit(GraphChange.NODE_MOVED, node_id=node_id)

    def change_node(
        self,
        node_id: str,
        *,
        title: str | None = None,
        payload: object = _UNSET,
        position: tuple[float, float] | None = None,
    ) -> None:
        """Update title, payload, or position.

        Title and payload changes emit :attr:`GraphChange.NODE_CHANGED`.
        Position changes emit :attr:`GraphChange.NODE_MOVED`.

        Raises:
            NodeGraphError: If ``node_id`` is unknown.
        """
        node = self._require_node(node_id)
        changed = False
        if title is not None and title != node.title:
            node.title = title
            changed = True
        if payload is not _UNSET and payload is not node.payload:
            node.payload = payload
            changed = True
        if position is not None:
            self.move_node(node_id, position)
        if changed:
            self._emit(GraphChange.NODE_CHANGED, node_id=node_id)

    def connect(
        self,
        source_id: str,
        source_port: str,
        target_id: str,
        target_port: str,
        *,
        edge_id: str | None = None,
    ) -> Edge:
        """Connect an output port to an input port.

        Raises:
            CycleError: If the edge would loop, including a self-connection.
            NodeGraphError: If a node or port is missing, types disagree, the
                input is already connected, or ``edge_id`` is already used.
        """
        output, inlet = self._validate_connection(
            source_id, source_port, target_id, target_port
        )
        resolved_id = edge_id.strip() if edge_id is not None else self._next_edge_id()
        if not resolved_id:
            msg = "edge id must be a non-empty string"
            raise NodeGraphError(msg)
        if resolved_id in self._edges:
            msg = f"duplicate edge id {resolved_id!r}"
            raise NodeGraphError(msg)
        edge = Edge(
            id=resolved_id,
            source_id=source_id,
            source_port=output.name,
            target_id=target_id,
            target_port=inlet.name,
        )
        self._edges[edge.id] = edge
        self._emit(GraphChange.EDGE_ADDED, edge_id=edge.id)
        return edge

    def disconnect(self, edge_id: str) -> None:
        """Remove an edge.

        Raises:
            NodeGraphError: If ``edge_id`` is unknown.
        """
        if edge_id not in self._edges:
            msg = f"unknown edge {edge_id!r}"
            raise NodeGraphError(msg)
        self._remove_edge(edge_id)

    def can_connect(
        self,
        source_id: str,
        source_port: str,
        target_id: str,
        target_port: str,
    ) -> bool:
        """Return whether :meth:`connect` would accept this edge."""
        try:
            self._validate_connection(source_id, source_port, target_id, target_port)
        except NodeGraphError:
            return False
        return True

    def incoming_edge(self, node_id: str, port_name: str) -> Edge | None:
        """Return the edge feeding an input, or ``None``."""
        self._require_node(node_id)
        for edge in self._edges.values():
            if edge.target_id == node_id and edge.target_port == port_name:
                return edge
        return None

    def incoming(self, node_id: str) -> tuple[Edge, ...]:
        """Edges whose target is ``node_id``, in insertion order."""
        self._require_node(node_id)
        return tuple(edge for edge in self._edges.values() if edge.target_id == node_id)

    def outgoing(self, node_id: str) -> tuple[Edge, ...]:
        """Edges whose source is ``node_id``, in insertion order."""
        self._require_node(node_id)
        return tuple(edge for edge in self._edges.values() if edge.source_id == node_id)

    def predecessors(self, node_id: str) -> tuple[str, ...]:
        """Unique upstream node ids, in edge insertion order."""
        seen: list[str] = []
        for edge in self.incoming(node_id):
            if edge.source_id not in seen:
                seen.append(edge.source_id)
        return tuple(seen)

    def successors(self, node_id: str) -> tuple[str, ...]:
        """Unique downstream node ids, in edge insertion order."""
        seen: list[str] = []
        for edge in self.outgoing(node_id):
            if edge.target_id not in seen:
                seen.append(edge.target_id)
        return tuple(seen)

    def would_cycle(self, source_id: str, target_id: str) -> bool:
        """Return whether an edge from ``source_id`` to ``target_id`` would loop.

        Raises:
            NodeGraphError: If either id is unknown.
        """
        self._require_node(source_id)
        self._require_node(target_id)
        if source_id == target_id:
            return True
        return self._reaches(target_id, source_id)

    def topological_order(self) -> tuple[str, ...]:
        """Node ids in an order safe for evaluation, roots first."""
        indegree = {node_id: len(self.predecessors(node_id)) for node_id in self._nodes}
        ready = [node_id for node_id in self._nodes if indegree[node_id] == 0]
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for successor in self.successors(current):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
        if len(order) != len(self._nodes):
            msg = "graph contains a cycle"
            raise NodeGraphError(msg)
        return tuple(order)

    def upstream(self, node_id: str) -> tuple[str, ...]:
        """Ancestors of ``node_id`` in topological order, excluding itself."""
        self._require_node(node_id)
        ancestors = self._collect(node_id, self.predecessors)
        order = self.topological_order()
        return tuple(node_id for node_id in order if node_id in ancestors)

    def downstream(self, node_id: str) -> tuple[str, ...]:
        """Descendants of ``node_id`` in topological order, excluding itself."""
        self._require_node(node_id)
        descendants = self._collect(node_id, self.successors)
        return tuple(
            node_id for node_id in self.topological_order() if node_id in descendants
        )

    def arrange(
        self,
        *,
        origin: tuple[float, float] = (48.0, 48.0),
        x_gap: float = 260.0,
        y_gap: float = 160.0,
    ) -> None:
        """Place nodes in topological columns, left to right.

        Rank is the longest path from a root. Nodes that share a rank stack
        vertically in insertion order.
        """
        if not self._nodes:
            return
        rank = {node_id: 0 for node_id in self._nodes}
        for node_id in self.topological_order():
            for successor in self.successors(node_id):
                rank[successor] = max(rank[successor], rank[node_id] + 1)
        columns: dict[int, list[str]] = {}
        for node_id in self._nodes:
            columns.setdefault(rank[node_id], []).append(node_id)
        origin_x, origin_y = origin
        for column, node_ids in columns.items():
            for row, node_id in enumerate(node_ids):
                self.move_node(
                    node_id,
                    (origin_x + column * x_gap, origin_y + row * y_gap),
                )

    def to_dict(self) -> dict[str, object]:
        """Return a Python snapshot. ``payload`` is stored as-is."""
        return {
            "nodes": [
                {
                    "id": node.id,
                    "title": node.title,
                    "inputs": [_port_to_dict(port) for port in node.inputs],
                    "outputs": [_port_to_dict(port) for port in node.outputs],
                    "position": [node.position[0], node.position[1]],
                    "payload": node.payload,
                }
                for node in self._nodes.values()
            ],
            "edges": [
                {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "source_port": edge.source_port,
                    "target_id": edge.target_id,
                    "target_port": edge.target_port,
                }
                for edge in self._edges.values()
            ],
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, object],
        *,
        type_compatible: TypeCompatible | None = None,
    ) -> NodeGraph:
        """Build a graph from a :meth:`to_dict` snapshot.

        Raises:
            NodeGraphError: If the snapshot is malformed or not a DAG.
        """
        payload = _as_mapping(data, "graph")
        graph = cls(type_compatible=type_compatible)
        for raw_node in _as_sequence(payload.get("nodes", ()), "nodes"):
            graph.add_node(_node_from_dict(raw_node))
        for raw_edge in _as_sequence(payload.get("edges", ()), "edges"):
            edge = _as_mapping(raw_edge, "edge")
            graph.connect(
                _require_text(edge.get("source_id"), "edge source_id"),
                _require_text(edge.get("source_port"), "edge source_port"),
                _require_text(edge.get("target_id"), "edge target_id"),
                _require_text(edge.get("target_port"), "edge target_port"),
                edge_id=_require_text(edge.get("id"), "edge id"),
            )
        return graph

    def _require_node(self, node_id: str) -> Node:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            msg = f"unknown node {node_id!r}"
            raise NodeGraphError(msg) from exc

    def _validate_connection(
        self,
        source_id: str,
        source_port: str,
        target_id: str,
        target_port: str,
    ) -> tuple[Port, Port]:
        source = self._require_node(source_id)
        target = self._require_node(target_id)
        output = self._require_port(source, source_port, PortDirection.OUT)
        inlet = self._require_port(target, target_port, PortDirection.IN)
        if source_id == target_id or self.would_cycle(source_id, target_id):
            raise CycleError(source_id, target_id)
        if not self._compatible(output.data_type, inlet.data_type):
            msg = (
                f"port type {output.data_type!r} is not compatible with "
                f"{inlet.data_type!r}"
            )
            raise NodeGraphError(msg)
        if self.incoming_edge(target_id, target_port) is not None:
            msg = f"input {target_id!r}.{target_port!r} is already connected"
            raise NodeGraphError(msg)
        return output, inlet

    def _require_port(self, node: Node, name: str, direction: PortDirection) -> Port:
        ports = node.inputs if direction is PortDirection.IN else node.outputs
        for port in ports:
            if port.name == name:
                return port
        side = "input" if direction is PortDirection.IN else "output"
        msg = f"unknown {side} {node.id!r}.{name!r}"
        raise NodeGraphError(msg)

    def _copy_ports(
        self,
        ports: Sequence[Port],
        direction: PortDirection,
    ) -> tuple[Port, ...]:
        copied: list[Port] = []
        seen: set[str] = set()
        for port in ports:
            if port.direction is not direction:
                side = "inputs" if direction is PortDirection.IN else "outputs"
                msg = (
                    f"port {port.name!r} in {side} must use direction "
                    f"{direction.value!r}"
                )
                raise NodeGraphError(msg)
            if port.name in seen:
                msg = f"duplicate {direction.value} port {port.name!r}"
                raise NodeGraphError(msg)
            seen.add(port.name)
            copied.append(port)
        return tuple(copied)

    def _copy_position(self, position: tuple[float, float]) -> tuple[float, float]:
        try:
            x, y = position
        except (TypeError, ValueError) as exc:
            msg = "position must be a pair of numbers"
            raise NodeGraphError(msg) from exc
        return (float(x), float(y))

    def _next_edge_id(self) -> str:
        while True:
            self._edge_seq += 1
            candidate = f"edge-{self._edge_seq}"
            if candidate not in self._edges:
                return candidate

    def _remove_edge(self, edge_id: str) -> None:
        del self._edges[edge_id]
        self._emit(GraphChange.EDGE_REMOVED, edge_id=edge_id)

    def _reaches(self, start: str, goal: str) -> bool:
        stack = [start]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == goal:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.successors(current))
        return False

    def _collect(self, start: str, step: Callable[[str], tuple[str, ...]]) -> set[str]:
        found: set[str] = set()
        stack = list(step(start))
        while stack:
            current = stack.pop()
            if current in found:
                continue
            found.add(current)
            stack.extend(step(current))
        return found

    def _emit(self, kind: GraphChange, *, node_id: str = "", edge_id: str = "") -> None:
        event = GraphEvent(kind, node_id, edge_id)
        for listener in tuple(self._listeners):
            listener(event)


def _port_to_dict(port: Port) -> dict[str, str]:
    return {"name": port.name, "data_type": port.data_type}


def _port_from_dict(raw: object, direction: PortDirection) -> Port:
    payload = _as_mapping(raw, "port")
    data_type = payload.get("data_type", "any")
    if not isinstance(data_type, str):
        msg = "port data_type must be a string"
        raise NodeGraphError(msg)
    return Port(
        _require_text(payload.get("name"), "port name"),
        direction,
        data_type,
    )


def _node_from_dict(raw: object) -> Node:
    payload = _as_mapping(raw, "node")
    position_raw = payload.get("position", (0.0, 0.0))
    position_items = _as_sequence(position_raw, "position")
    if len(position_items) != 2:
        msg = "position must be a pair of numbers"
        raise NodeGraphError(msg)
    x_raw, y_raw = position_items
    if not isinstance(x_raw, (int, float)) or not isinstance(y_raw, (int, float)):
        msg = "position must be a pair of numbers"
        raise NodeGraphError(msg)
    inputs = tuple(
        _port_from_dict(item, PortDirection.IN)
        for item in _as_sequence(payload.get("inputs", ()), "inputs")
    )
    outputs = tuple(
        _port_from_dict(item, PortDirection.OUT)
        for item in _as_sequence(payload.get("outputs", ()), "outputs")
    )
    title = payload.get("title", "")
    if not isinstance(title, str):
        msg = "node title must be a string"
        raise NodeGraphError(msg)
    return Node(
        id=_require_text(payload.get("id"), "node id"),
        title=title,
        inputs=inputs,
        outputs=outputs,
        payload=payload.get("payload"),
        position=(float(x_raw), float(y_raw)),
    )
