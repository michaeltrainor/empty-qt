"""Tree model wrapping a nested list-of-lists scene graph."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    QAbstractItemModel,
    QByteArray,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
)

type _ModelIndex = QModelIndex | QPersistentModelIndex

type MoveFilter = Callable[[list[str], str, int], bool]
"""Host veto for a node move: ``(names, parent, row) -> accept``.

``names`` are the top-most nodes being moved, in tree order. ``parent`` is
the destination parent's UID, or ``""`` for the forest root. ``row`` is the
destination row among that parent's current children; it equals the child
count when appending.
"""

SCENE_GRAPH_MIME_TYPE = "application/x-mtqt-scene-graph-nodes"
"""MIME type of a drag started from a :class:`SceneGraphModel`."""


@dataclass
class SceneNode:
    """One node in a scene graph. ``name`` is the stable UID.

    ``parent`` is omitted from equality so two independently parsed trees
    with the same names still compare equal.
    """

    name: str
    children: list[SceneNode] = field(default_factory=list)
    parent: SceneNode | None = field(default=None, repr=False, compare=False)


def _as_node_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    msg = f"scene node must be a list, not {type(value).__name__}"
    raise TypeError(msg)


def _parse_node(raw: object) -> SceneNode:
    items = _as_node_sequence(raw)
    if not items:
        raise ValueError("scene node must start with a name")
    name = items[0]
    if not isinstance(name, str):
        msg = f"scene node name must be str, not {type(name).__name__}"
        raise TypeError(msg)
    node = SceneNode(name=name)
    for child_raw in items[1:]:
        if isinstance(child_raw, str):
            msg = "scene node children must be lists, not strings"
            raise TypeError(msg)
        child = _parse_node(child_raw)
        child.parent = node
        node.children.append(child)
    return node


def _parse_forest(raw: object) -> list[SceneNode]:
    items = _as_node_sequence(raw)
    if items and isinstance(items[0], str):
        msg = "scene graph must be a forest of nodes; wrap a single root in a list"
        raise TypeError(msg)
    return [_parse_node(item) for item in items]


def _node_to_list(node: SceneNode) -> list[object]:
    return [node.name, *(_node_to_list(child) for child in node.children)]


def _make_root(nodes: Sequence[object] | None) -> SceneNode:
    root = SceneNode(name="")
    if nodes is None:
        return root
    for child in _parse_forest(nodes):
        child.parent = root
        root.children.append(child)
    return root


def _has_ancestor_in(node: SceneNode, ids: set[int]) -> bool:
    current = node.parent
    while current is not None:
        if id(current) in ids:
            return True
        current = current.parent
    return False


class SceneGraphModel(QAbstractItemModel):
    """``QAbstractItemModel`` over a nested list-of-lists scene graph.

    Each node is a list: the first element is the name (UID), remaining
    elements are child nodes. Leaves are a one-element list. The top-level
    value is a forest (a list of root nodes). The model copies that structure
    into :class:`SceneNode` objects and does not own any view.

    Nodes can be moved with :meth:`move_nodes`, and the model implements the
    Qt drag-and-drop protocol (``MoveAction`` only, MIME type
    :data:`SCENE_GRAPH_MIME_TYPE`) so a view in ``DragDrop`` mode reparents
    and reorders rows. :attr:`nodesMoved` emits ``(names, parent, row)`` after
    a move: the top-most moved UIDs in their new order, the new parent's UID
    (``""`` for the root), and the row of the first moved node. A host can
    veto moves with :meth:`set_move_filter`.

    Args:
        nodes: Forest of nested name lists. Copied into an internal tree.
        parent: Qt parent ``QObject``.
    """

    nodesMoved = Signal(list, str, int)

    def __init__(
        self,
        nodes: Sequence[object] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = _make_root(nodes)
        self._move_filter: MoveFilter | None = None

    @classmethod
    def from_json(cls, path: Path, parent: QObject | None = None) -> SceneGraphModel:
        """Build a model from a JSON forest of nested name lists.

        Args:
            path: JSON file path.
            parent: Qt parent ``QObject``.
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(payload, parent=parent)

    def _node_from_index(self, index: _ModelIndex) -> SceneNode:
        if not index.isValid():
            return self._root
        pointer = index.internalPointer()
        if not isinstance(pointer, SceneNode):
            return self._root
        return pointer

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        return len(self._node_from_index(parent).children)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        return 1

    def hasChildren(self, parent: _ModelIndex = QModelIndex()) -> bool:
        return bool(self._node_from_index(parent).children)

    def index(
        self,
        row: int,
        column: int,
        parent: _ModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        child = self._node_from_index(parent).children[row]
        return self.createIndex(row, column, child)

    def parent(self, index: _ModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node = self._node_from_index(index)
        parent_node = node.parent
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        grandparent = parent_node.parent
        if grandparent is None:
            return QModelIndex()
        row = grandparent.children.index(parent_node)
        return self.createIndex(row, 0, parent_node)

    def data(
        self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid():
            return None
        node = self._node_from_index(index)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return node.name
        if role == Qt.ItemDataRole.UserRole:
            return node
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and section == 0:
            return "Name"
        return None

    def flags(self, index: _ModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            # The invisible root accepts drops so rows can become roots.
            return Qt.ItemFlag.ItemIsDropEnabled
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [SCENE_GRAPH_MIME_TYPE]

    def mimeData(self, indexes: Iterable[QModelIndex]) -> QMimeData:
        names: list[str] = []
        for index in indexes:
            if not index.isValid() or index.column() != 0:
                continue
            name = self._node_from_index(index).name
            if name not in names:
                names.append(name)
        payload = json.dumps({"model": id(self), "names": names})
        data = QMimeData()
        data.setData(SCENE_GRAPH_MIME_TYPE, QByteArray(payload.encode("utf-8")))
        return data

    def canDropMimeData(
        self,
        data: QMimeData | None,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: _ModelIndex,
    ) -> bool:
        names = self._names_from_mime(data)
        if names is None or action != Qt.DropAction.MoveAction:
            return False
        return self.can_move_nodes(names, self._node_from_index(parent).name, row)

    def dropMimeData(
        self,
        data: QMimeData | None,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: _ModelIndex,
    ) -> bool:
        names = self._names_from_mime(data)
        if names is None or action != Qt.DropAction.MoveAction:
            return False
        return self.move_nodes(names, self._node_from_index(parent).name, row)

    def removeRows(
        self, row: int, count: int, parent: _ModelIndex = QModelIndex()
    ) -> bool:
        # After a MoveAction drop, the view asks the model to remove the dragged
        # rows. dropMimeData already moved them in place, so refuse: removing
        # here would delete the nodes that were just dropped.
        return False

    def _names_from_mime(self, data: QMimeData | None) -> list[str] | None:
        if data is None or not data.hasFormat(SCENE_GRAPH_MIME_TYPE):
            return None
        try:
            payload = json.loads(bytes(data.data(SCENE_GRAPH_MIME_TYPE).data()))
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("model") != id(self):
            return None
        names = payload.get("names")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return None
        return names

    def set_move_filter(self, accept: MoveFilter | None) -> None:
        """Install a host veto consulted before every move.

        The filter runs during drag hover, so it should be cheap. It receives
        the top-most moved UIDs in tree order, the destination parent's UID
        (``""`` for the root), and the destination row among that parent's
        current children. Returning ``False`` rejects the move; the view then
        shows a forbidden cursor. ``None`` removes the filter.

        Args:
            accept: ``(names, parent, row) -> bool``, or ``None``.
        """
        self._move_filter = accept

    def _index_for_node(self, node: SceneNode) -> QModelIndex:
        parent_node = node.parent
        if node is self._root or parent_node is None:
            return QModelIndex()
        return self.createIndex(parent_node.children.index(node), 0, node)

    def _resolve_move(
        self, names: Sequence[str], parent: str
    ) -> tuple[list[SceneNode], SceneNode, int] | None:
        """Return the top-most nodes to move, the destination, and the row.

        ``None`` when a name is unknown, the parent is missing, the set is
        empty, or the destination is one of the nodes or inside one.
        """
        wanted = set(names)
        if not wanted:
            return None
        found: dict[str, SceneNode] = {}
        ordered: list[SceneNode] = []

        def walk(node: SceneNode) -> None:
            for child in node.children:
                if child.name in wanted and child.name not in found:
                    found[child.name] = child
                    ordered.append(child)
                elif child.name == parent and parent not in found:
                    found[child.name] = child
                walk(child)

        walk(self._root)
        if len(ordered) != len(wanted):
            return None
        parent_node = self._root if not parent else found.get(parent)
        if parent_node is None:
            return None
        ids = {id(node) for node in ordered}
        top = [node for node in ordered if not _has_ancestor_in(node, ids)]
        if id(parent_node) in ids or _has_ancestor_in(parent_node, ids):
            return None
        return top, parent_node, len(parent_node.children)

    def can_move_nodes(self, names: Sequence[str], parent: str, row: int = -1) -> bool:
        """Return whether :meth:`move_nodes` would accept this move.

        Args:
            names: UIDs to move. Nodes whose ancestor is also listed move
                with that ancestor and are dropped from the set.
            parent: Destination parent UID, or ``""`` for the forest root.
            row: Destination row among the parent's current children.
                ``-1`` or an out-of-range row appends.
        """
        return self._check_move(names, parent, row) is not None

    def _check_move(
        self, names: Sequence[str], parent: str, row: int
    ) -> tuple[list[SceneNode], SceneNode, int] | None:
        """Resolve a move and consult the filter; return nodes, parent, row."""
        resolved = self._resolve_move(names, parent)
        if resolved is None:
            return None
        top, parent_node, count = resolved
        if row < 0 or row > count:
            row = count
        if self._move_filter is not None and not self._move_filter(
            [node.name for node in top], parent_node.name, row
        ):
            return None
        return top, parent_node, row

    def move_nodes(self, names: Sequence[str], parent: str, row: int = -1) -> bool:
        """Move nodes under ``parent`` before ``row``, keeping their order.

        Emits proper ``rowsMoved`` notifications so attached views and
        persistent indexes follow, then :attr:`nodesMoved`. A node can't be
        moved into itself or a descendant, and a node whose ancestor is also
        listed moves with that ancestor.

        Args:
            names: UIDs to move.
            parent: Destination parent UID, or ``""`` for the forest root.
            row: Destination row among the parent's current children.
                ``-1`` or an out-of-range row appends.

        Returns:
            ``True`` when the tree changed. ``False`` when the move was
            invalid, vetoed by the move filter, or already in place.
        """
        checked = self._check_move(names, parent, row)
        if checked is None:
            return False
        top, parent_node, cursor = checked
        placed = 0  # Moved nodes occupy rows [cursor - placed, cursor).
        changed = False
        for node in top:
            source = node.parent
            if source is None:
                continue
            source_row = source.children.index(node)
            # Earlier moves in this loop shift rows, so both parent indexes
            # must be rebuilt for every move; a cached one segfaults in Qt.
            parent_index = self._index_for_node(parent_node)
            if source is parent_node:
                if source_row >= cursor:
                    if source_row != cursor:
                        if not self.beginMoveRows(
                            parent_index, source_row, source_row, parent_index, cursor
                        ):
                            break
                        source.children.pop(source_row)
                        source.children.insert(cursor, node)
                        self.endMoveRows()
                        changed = True
                    cursor += 1
                elif placed == 0 and source_row == cursor - 1:
                    pass  # Already right before the insertion point.
                else:
                    if not self.beginMoveRows(
                        parent_index, source_row, source_row, parent_index, cursor
                    ):
                        break
                    source.children.pop(source_row)
                    source.children.insert(cursor - 1, node)
                    self.endMoveRows()
                    changed = True
            else:
                source_index = self._index_for_node(source)
                if not self.beginMoveRows(
                    source_index, source_row, source_row, parent_index, cursor
                ):
                    break
                source.children.pop(source_row)
                node.parent = parent_node
                parent_node.children.insert(cursor, node)
                self.endMoveRows()
                changed = True
                cursor += 1
            placed += 1
        if not changed:
            return False
        self.nodesMoved.emit(
            [node.name for node in top], parent_node.name, cursor - placed
        )
        return True

    def node_at(self, index: _ModelIndex) -> SceneNode:
        """Return the :class:`SceneNode` for ``index``."""
        if not index.isValid():
            raise ValueError("invalid index")
        return self._node_from_index(index)

    def index_for_name(self, name: str) -> QModelIndex:
        """Return the first index whose node name equals ``name``.

        Args:
            name: Scene node UID to find.

        Returns:
            A valid ``QModelIndex``, or an invalid index when ``name`` is
            missing.
        """

        def walk(parent: QModelIndex) -> QModelIndex:
            for row in range(self.rowCount(parent)):
                index = self.index(row, 0, parent)
                if self._node_from_index(index).name == name:
                    return index
                found = walk(index)
                if found.isValid():
                    return found
            return QModelIndex()

        return walk(QModelIndex())

    def path_at(self, index: _ModelIndex) -> str:
        """Return a ``/``-separated path for ``index``, such as ``/world/geo``."""
        node = self.node_at(index)
        parts: list[str] = []
        current: SceneNode | None = node
        while current is not None and current is not self._root:
            parts.append(current.name)
            current = current.parent
        return "/" + "/".join(reversed(parts))

    def to_lists(self) -> list[object]:
        """Serialize the tree back to a nested list-of-lists forest."""
        return [_node_to_list(child) for child in self._root.children]

    def set_nodes(self, nodes: Sequence[object]) -> None:
        """Replace the forest and notify attached views."""
        self.beginResetModel()
        self._root = _make_root(nodes)
        self.endResetModel()

    def append_child(self, parent: _ModelIndex, name: str) -> QModelIndex:
        """Append a named child under ``parent`` and notify attached views."""
        parent_node = self._node_from_index(parent)
        parent_index = (
            self.createIndex(parent.row(), parent.column(), parent_node)
            if parent.isValid()
            else QModelIndex()
        )
        row = len(parent_node.children)
        self.beginInsertRows(parent_index, row, row)
        child = SceneNode(name=name, parent=parent_node)
        parent_node.children.append(child)
        self.endInsertRows()
        return self.index(row, 0, parent_index)

    def remove_row(self, index: _ModelIndex) -> None:
        """Remove ``index`` and notify attached views."""
        if not index.isValid():
            raise ValueError("invalid index")
        node = self._node_from_index(index)
        parent_node = node.parent
        if parent_node is None:
            raise ValueError("cannot remove the invisible root")
        row = index.row()
        parent_index = self.parent(index)
        self.beginRemoveRows(parent_index, row, row)
        del parent_node.children[row]
        node.parent = None
        self.endRemoveRows()
