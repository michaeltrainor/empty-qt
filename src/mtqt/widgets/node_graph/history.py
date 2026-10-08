"""Undo and redo for a :class:`~mtqt.widgets.node_graph.graph.NodeGraph`.

The graph stays free of undo policy. A :class:`GraphHistory` listens, remembers
enough state to invert each notification, and buffers those inverses until
:meth:`GraphHistory.seal`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mtqt.widgets.node_graph.graph import Edge, GraphChange, GraphEvent, Node, NodeGraph


@dataclass(frozen=True, slots=True)
class _RemoveNode:
    node_id: str


@dataclass(frozen=True, slots=True)
class _RestoreNode:
    node: Node


@dataclass(frozen=True, slots=True)
class _DropEdge:
    edge_id: str


@dataclass(frozen=True, slots=True)
class _RestoreEdge:
    edge: Edge


@dataclass(frozen=True, slots=True)
class _MoveNode:
    node_id: str
    position: tuple[float, float]


@dataclass(frozen=True, slots=True)
class _EditNode:
    node_id: str
    title: str
    payload: object


type _Action = (
    _RemoveNode | _RestoreNode | _DropEdge | _RestoreEdge | _MoveNode | _EditNode
)


@dataclass(slots=True)
class _Step:
    undo: list[_Action]
    redo: list[_Action]


def _copy_title(title: str) -> str:
    return str(title)


def _copy_position(position: tuple[float, float]) -> tuple[float, float]:
    x, y = position
    return (float(x), float(y))


def _snapshot(node: Node) -> Node:
    """Copy the fields needed to restore ``node`` without deepcopying it."""
    return Node(
        id=node.id,
        title=_copy_title(node.title),
        inputs=node.inputs,
        outputs=node.outputs,
        payload=node.payload,
        position=_copy_position(node.position),
    )


def _moves_only(step: _Step) -> bool:
    return bool(step.undo) and all(
        isinstance(action, _MoveNode) for action in step.undo
    )


def _collapse_moves(step: _Step) -> _Step:
    """Keep the earliest undo position and the latest redo position per node."""
    undo_at: dict[str, tuple[float, float]] = {}
    redo_at: dict[str, tuple[float, float]] = {}
    order: list[str] = []
    for action in step.undo:
        if not isinstance(action, _MoveNode):
            return step
        if action.node_id not in undo_at:
            undo_at[action.node_id] = action.position
            order.append(action.node_id)
    for action in step.redo:
        if not isinstance(action, _MoveNode):
            return step
        redo_at[action.node_id] = action.position
    return _Step(
        undo=[_MoveNode(node_id, undo_at[node_id]) for node_id in order],
        redo=[_MoveNode(node_id, redo_at[node_id]) for node_id in order],
    )


def _merge_moves(previous: _Step, new: _Step) -> _Step:
    return _collapse_moves(
        _Step(undo=[*previous.undo, *new.undo], redo=[*previous.redo, *new.redo])
    )


class GraphHistory:
    """Invert user edits on one :class:`NodeGraph`.

    Each listener notification is stored until :meth:`seal`. Call ``seal``
    once per user gesture: deleting a node emits one edge removal per wire
    and then the node removal, and a rewire emits a removal then an addition.
    Those belong in a single undo step. A sealed step clears the redo stack.

    ``join_moves`` merges a sealed step into the previous one when both
    contain only position changes. Undo keeps the position from before the
    first merged move. Redo uses the latest position. Mouse-press on the
    canvas arms a one-shot break so a new drag does not merge into the
    previous drag; that break is internal to the widget.

    The history mirrors nodes and edges because :class:`GraphEvent` has no
    before-values. Titles and positions are copied. Payload is stored by
    reference, and frozen ports are reused. No deepcopy.

    While undo or redo runs, graph events refresh the mirror and are not
    recorded.

    Args:
        graph: Graph to watch. Nodes and edges already in it are mirrored
            and are not an undo step.
    """

    def __init__(self, graph: NodeGraph) -> None:
        self._join_moves = False
        self._move_join_break = False
        self._applying = False
        self._graph = graph
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._pending: list[tuple[_Action, _Action]] = []
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}
        self.bind(graph)

    @property
    def join_moves(self) -> bool:
        """Whether the next move-only step merges into the previous one."""
        return self._join_moves

    @join_moves.setter
    def join_moves(self, value: bool) -> None:
        self._join_moves = bool(value)

    @property
    def can_undo(self) -> bool:
        """Whether :meth:`undo` would restore a previous gesture."""
        return bool(self._undo or self._pending)

    @property
    def can_redo(self) -> bool:
        """Whether :meth:`redo` would reapply an undone gesture."""
        return bool(self._redo) and not self._pending

    @property
    def has_pending(self) -> bool:
        """Whether events are buffered and not yet sealed."""
        return bool(self._pending)

    def bind(self, graph: NodeGraph) -> None:
        """Watch ``graph`` instead of the previous one.

        Stacks and the open buffer are cleared. Current nodes and edges are
        mirrored and do not become undo steps.

        Args:
            graph: Graph to watch.
        """
        self._unlisten()
        self._graph = graph
        self.clear()
        self._mirror()
        graph.subscribe(self._on_event)

    def clear(self) -> None:
        """Drop sealed steps, the redo stack, and any buffered events."""
        self._undo.clear()
        self._redo.clear()
        self._pending.clear()

    def seal(self) -> None:
        """Turn buffered events into one undo step.

        Does nothing when the buffer is empty. When :attr:`join_moves` is
        true and both this step and the previous one contain only position
        changes, the steps merge, unless a new drag has armed a one-shot
        break. A sealed step clears the redo stack.
        """
        if not self._pending:
            return
        pairs = self._pending
        self._pending = []
        step = _Step(
            undo=[undo for undo, _redo in pairs],
            redo=[redo for _undo, redo in pairs],
        )
        if _moves_only(step):
            step = _collapse_moves(step)
        break_join = self._move_join_break
        self._move_join_break = False
        if (
            self._join_moves
            and not break_join
            and _moves_only(step)
            and self._undo
            and _moves_only(self._undo[-1])
        ):
            self._undo[-1] = _merge_moves(self._undo[-1], step)
        else:
            self._undo.append(step)
        self._redo.clear()

    def undo(self) -> bool:
        """Revert the last sealed gesture.

        Seals a pending buffer first. Returns ``False`` when there is
        nothing to undo.
        """
        self.seal()
        if not self._undo:
            return False
        step = self._undo.pop()
        self._apply(reversed(step.undo))
        self._redo.append(step)
        return True

    def redo(self) -> bool:
        """Reapply the last undone gesture.

        Seals a pending buffer first, which clears the redo stack when the
        buffer is not empty. Returns ``False`` when there is nothing to redo.
        """
        self.seal()
        if not self._redo:
            return False
        step = self._redo.pop()
        self._apply(step.redo)
        self._undo.append(step)
        return True

    def _begin_move_group(self) -> None:
        """Start a drag. The first sealed move step will not join an older one."""
        self._move_join_break = True
        self.join_moves = True

    def _end_move_group(self) -> None:
        """Seal the open drag, then stop joining later moves."""
        # Seal while join_moves is still set so the drag's tail stays in it.
        self.seal()
        self.join_moves = False
        self._move_join_break = False

    def _unlisten(self) -> None:
        self._graph.unsubscribe(self._on_event)

    def _mirror(self) -> None:
        self._nodes = {node.id: _snapshot(node) for node in self._graph.nodes()}
        self._edges = {edge.id: edge for edge in self._graph.edges()}

    def _on_event(self, event: GraphEvent) -> None:
        pair = self._capture(event)
        if pair is None or self._applying:
            return
        self._pending.append(pair)

    def _capture(self, event: GraphEvent) -> tuple[_Action, _Action] | None:
        kind = event.kind
        if kind is GraphChange.NODE_ADDED:
            return self._capture_added(event.node_id)
        if kind is GraphChange.NODE_REMOVED:
            return self._capture_removed(event.node_id)
        if kind is GraphChange.EDGE_ADDED:
            return self._capture_edge_added(event.edge_id)
        if kind is GraphChange.EDGE_REMOVED:
            return self._capture_edge_removed(event.edge_id)
        if kind is GraphChange.NODE_MOVED:
            return self._capture_moved(event.node_id)
        if kind is GraphChange.NODE_CHANGED:
            return self._capture_changed(event.node_id)
        return None

    def _capture_added(self, node_id: str) -> tuple[_Action, _Action]:
        live = self._graph.node(node_id)
        self._nodes[node_id] = _snapshot(live)
        return (_RemoveNode(node_id), _RestoreNode(_snapshot(live)))

    def _capture_removed(self, node_id: str) -> tuple[_Action, _Action] | None:
        mirrored = self._nodes.pop(node_id, None)
        if mirrored is None:
            return None
        return (_RestoreNode(_snapshot(mirrored)), _RemoveNode(node_id))

    def _capture_edge_added(self, edge_id: str) -> tuple[_Action, _Action]:
        edge = self._graph.edge(edge_id)
        self._edges[edge_id] = edge
        return (_DropEdge(edge_id), _RestoreEdge(edge))

    def _capture_edge_removed(self, edge_id: str) -> tuple[_Action, _Action] | None:
        edge = self._edges.pop(edge_id, None)
        if edge is None:
            return None
        return (_RestoreEdge(edge), _DropEdge(edge_id))

    def _capture_moved(self, node_id: str) -> tuple[_Action, _Action]:
        mirrored = self._nodes[node_id]
        old = mirrored.position
        new = _copy_position(self._graph.node(node_id).position)
        mirrored.position = new
        return (_MoveNode(node_id, old), _MoveNode(node_id, new))

    def _capture_changed(self, node_id: str) -> tuple[_Action, _Action]:
        mirrored = self._nodes[node_id]
        live = self._graph.node(node_id)
        old_title = mirrored.title
        old_payload = mirrored.payload
        new_title = _copy_title(live.title)
        new_payload = live.payload
        mirrored.title = new_title
        mirrored.payload = new_payload
        return (
            _EditNode(node_id, old_title, old_payload),
            _EditNode(node_id, new_title, new_payload),
        )

    def _apply(self, actions: Iterable[_Action]) -> None:
        self._applying = True
        try:
            for action in actions:
                self._dispatch(action)
        finally:
            self._applying = False

    def _dispatch(self, action: _Action) -> None:
        if isinstance(action, _RemoveNode):
            self._graph.remove_node(action.node_id)
            return
        if isinstance(action, _RestoreNode):
            self._graph.add_node(action.node)
            return
        if isinstance(action, _DropEdge):
            self._graph.disconnect(action.edge_id)
            return
        if isinstance(action, _RestoreEdge):
            edge = action.edge
            self._graph.connect(
                edge.source_id,
                edge.source_port,
                edge.target_id,
                edge.target_port,
                edge_id=edge.id,
            )
            return
        if isinstance(action, _MoveNode):
            self._graph.move_node(action.node_id, action.position)
            return
        if isinstance(action, _EditNode):
            self._graph.change_node(
                action.node_id,
                title=action.title,
                payload=action.payload,
            )
            return
        msg = f"unknown history action {type(action)!r}"
        raise TypeError(msg)
