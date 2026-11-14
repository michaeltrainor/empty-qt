"""Outliner: an item model over the stage's prims, and a tree widget on it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pxr import Sdf, Usd
from PySide6.QtCore import (
    QAbstractItemModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtWidgets import QAbstractItemView, QTreeView, QVBoxLayout, QWidget

from stage_editor.document import StageDocument

_ModelIndex = QModelIndex | QPersistentModelIndex


@dataclass
class _Node:
    path: str
    name: str
    type_name: str
    parent: _Node | None = field(default=None, repr=False)
    children: list[_Node] = field(default_factory=list)
    row: int = 0


class PrimModel(QAbstractItemModel):
    """Read-only tree of the document's prims: name and type columns.

    The tree holds the prims that ``Usd.PrimDefaultPredicate`` admits
    (active, defined, loaded, concrete) and traverses instance proxies, so
    instanced scenes expand. Any resync in :attr:`StageDocument.changed`
    rebuilds the model; info-only changes refresh their rows.

    Args:
        document: The document to mirror.
        parent: Owning ``QObject``.
    """

    #: ``data`` role holding the prim path as a string.
    PathRole = Qt.ItemDataRole.UserRole + 1
    HEADERS = ("Name", "Type")

    def __init__(self, document: StageDocument, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._root = _Node("/", "", "")
        self._by_path: dict[str, _Node] = {}
        document.stageReplaced.connect(self._rebuild)
        document.changed.connect(self._on_changed)
        self._rebuild()

    # -- structure ------------------------------------------------------------

    def _rebuild(self) -> None:
        self.beginResetModel()
        self._root = _Node("/", "", "")
        self._by_path = {}
        predicate = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)
        stack = [(self._document.stage.GetPseudoRoot(), self._root)]
        while stack:
            prim, node = stack.pop()
            for child in prim.GetFilteredChildren(predicate):
                child_node = _Node(
                    str(child.GetPath()), child.GetName(), child.GetTypeName(), node
                )
                child_node.row = len(node.children)
                node.children.append(child_node)
                self._by_path[child_node.path] = child_node
                stack.append((child, child_node))
        self.endResetModel()

    def _on_changed(self, resynced: list[str], info: list[str]) -> None:
        if any(not Sdf.Path(path).IsPropertyPath() for path in resynced):
            self._rebuild()
            return
        for path in info + [p for p in resynced if Sdf.Path(p).IsPropertyPath()]:
            path = Sdf.Path(path).GetPrimPath().pathString
            index = self.index_for_path(path)
            if index.isValid():
                self.dataChanged.emit(index, index.siblingAtColumn(1))

    @property
    def prim_count(self) -> int:
        """Number of prims the model shows."""
        return len(self._by_path)

    def _node(self, index: _ModelIndex) -> _Node:
        if index.isValid():
            node = index.internalPointer()
            if isinstance(node, _Node):
                return node
        return self._root

    def index_for_path(self, path: str) -> QModelIndex:
        """Return the index of the prim at ``path``, or an invalid index.

        Args:
            path: Prim path as a string.
        """
        node = self._by_path.get(path)
        if node is None:
            return QModelIndex()
        return self.createIndex(node.row, 0, node)

    def path_at(self, index: _ModelIndex) -> str:
        """Return the prim path at ``index``, or ``""`` for the root.

        Args:
            index: Any column of the row.
        """
        node = self._node(index)
        return node.path if node is not self._root else ""

    # -- QAbstractItemModel ---------------------------------------------------

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self._node(parent).children)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        return len(self.HEADERS)

    def index(
        self, row: int, column: int, parent: _ModelIndex = QModelIndex()
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, self._node(parent).children[row])

    def parent(self, index: _ModelIndex) -> QModelIndex:  # type: ignore[override]
        node = self._node(index)
        parent = node.parent
        if parent is None or parent is self._root:
            return QModelIndex()
        return self.createIndex(parent.row, 0, parent)

    def data(
        self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid():
            return None
        node = self._node(index)
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name if index.column() == 0 else node.type_name
        if role == self.PathRole:
            return node.path
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.path
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.HEADERS[section]
        return None


class Outliner(QWidget):
    """Tree view over :class:`PrimModel` with path-based selection.

    Signals:
        selectedPathsChanged(list): The selected prim paths, as strings,
            after a user selection change. Programmatic changes through
            :meth:`set_selected_paths` do not emit.

    Args:
        document: The document to show.
        parent: Owning widget.
    """

    selectedPathsChanged = Signal(list)

    def __init__(self, document: StageDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = PrimModel(document, self)
        self._view = QTreeView(self)
        self._view.setModel(self._model)
        self._view.setHeaderHidden(False)
        self._view.setUniformRowHeights(True)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._view.setExpandsOnDoubleClick(True)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        selection = self._view.selectionModel()
        assert selection is not None
        selection.selectionChanged.connect(self._on_selection_changed)
        self._model.modelReset.connect(self._expand_top)
        self._expand_top()

    @property
    def model(self) -> PrimModel:
        """The prim model."""
        return self._model

    @property
    def view(self) -> QTreeView:
        """The tree view, for tests and hosts that tune it."""
        return self._view

    def selected_paths(self) -> list[str]:
        """Return the selected prim paths in view order."""
        selection = self._view.selectionModel()
        assert selection is not None
        return [self._model.path_at(index) for index in selection.selectedRows()]

    def set_selected_paths(self, paths: Sequence[str]) -> None:
        """Select ``paths`` (those present in the model) without emitting.

        Args:
            paths: Prim paths as strings.
        """
        selection = self._view.selectionModel()
        assert selection is not None
        self._updating = True
        try:
            selection.clearSelection()
            flags = (
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows
            )
            first = QModelIndex()
            for path in paths:
                index = self._model.index_for_path(path)
                if not index.isValid():
                    continue
                selection.select(index, flags)
                if not first.isValid():
                    first = index
            if first.isValid():
                self._view.scrollTo(first)
                self._view.setCurrentIndex(first)
        finally:
            self._updating = False

    def _expand_top(self) -> None:
        self._view.expandToDepth(0)
        self._view.resizeColumnToContents(0)

    def _on_selection_changed(
        self, _selected: QItemSelection, _deselected: QItemSelection
    ) -> None:
        if not self._updating:
            self.selectedPathsChanged.emit(self.selected_paths())
