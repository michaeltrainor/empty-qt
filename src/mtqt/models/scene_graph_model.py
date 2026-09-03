"""Tree model wrapping a nested list-of-lists scene graph."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

type _ModelIndex = QModelIndex | QPersistentModelIndex


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


class SceneGraphModel(QAbstractItemModel):
    """``QAbstractItemModel`` over a nested list-of-lists scene graph.

    Each node is a list: the first element is the name (UID), remaining
    elements are child nodes. Leaves are a one-element list. The top-level
    value is a forest (a list of root nodes). The model copies that structure
    into :class:`SceneNode` objects and does not own any view.

    Args:
        nodes: Forest of nested name lists. Copied into an internal tree.
        parent: Qt parent ``QObject``.
    """

    def __init__(
        self,
        nodes: Sequence[object] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = _make_root(nodes)

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

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._node_from_index(parent).children)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 1

    def hasChildren(self, parent: _ModelIndex = QModelIndex()) -> bool:  # noqa: N802
        return bool(self._node_from_index(parent).children)

    def index(  # noqa: N802
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

    def headerData(  # noqa: N802
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
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def node_at(self, index: _ModelIndex) -> SceneNode:
        """Return the :class:`SceneNode` for ``index``."""
        if not index.isValid():
            raise ValueError("invalid index")
        return self._node_from_index(index)

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
