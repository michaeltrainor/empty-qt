"""Outliner: a usdview-style prim browser over the stage.

:class:`PrimModel` mirrors the stage's prims with name, type, and computed
visibility columns, colours prims the way usdview does (instances,
prototypes, composition arcs, inactive prims), and honours a
:class:`PrimFilter`. :class:`Outliner` adds a search box, a filter menu, a
visibility toggle column, and a context menu whose view overrides go to
the session layer.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pxr import Sdf, Usd, UsdGeom
from PySide6.QtCore import (
    QAbstractItemModel,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from stage_editor.document import SESSION_ROOT, StageDocument

_ModelIndex = QModelIndex | QPersistentModelIndex

#: Drag-and-drop payload: newline-separated prim paths.
MIME_TYPE = "application/x-stage-editor-prim-paths"

#: usdview's prim colours.
NORMAL_COLOR = QColor(227, 227, 227)
ARCS_COLOR = QColor(222, 158, 46)
INSTANCE_COLOR = QColor(135, 206, 250)
INSTANCE_PROXY_COLOR = QColor(170, 196, 220)
PROTOTYPE_COLOR = QColor(118, 136, 217)
INACTIVE_COLOR = QColor(128, 128, 128)


@dataclass
class PrimFilter:
    """Which prims the outliner lists, as in usdview's Show menu.

    Attributes:
        inactive: List deactivated prims (greyed).
        undefined: List prims that are only ``over`` opinions.
        abstract: List ``class`` prims.
        prototypes: List the stage's instancing prototypes as extra roots.
    """

    inactive: bool = True
    undefined: bool = False
    abstract: bool = False
    prototypes: bool = False

    def admits(self, prim: Usd.Prim) -> bool:
        """Return whether ``prim`` passes the filter.

        Args:
            prim: A prim reached through instance proxies.
        """
        if not self.inactive and not prim.IsActive():
            return False
        if not self.undefined and not prim.IsDefined():
            return False
        return self.abstract or not prim.IsAbstract()


@dataclass
class _Node:
    path: str
    name: str
    type_name: str
    parent: _Node | None = field(default=None, repr=False)
    children: list[_Node] = field(default_factory=list)
    row: int = 0
    active: bool = True
    defined: bool = True
    abstract: bool = False
    instance: bool = False
    instance_proxy: bool = False
    in_prototype: bool = False
    has_arcs: bool = False
    unloaded: bool = False


def _node_for(prim: Usd.Prim, parent: _Node) -> _Node:
    return _Node(
        str(prim.GetPath()),
        prim.GetName(),
        prim.GetTypeName(),
        parent,
        active=prim.IsActive(),
        defined=prim.IsDefined(),
        abstract=prim.IsAbstract(),
        instance=prim.IsInstance(),
        instance_proxy=prim.IsInstanceProxy(),
        in_prototype=prim.IsInPrototype() or prim.IsPrototype(),
        has_arcs=prim.HasAuthoredReferences()
        or prim.HasAuthoredPayloads()
        or prim.HasAuthoredInherits()
        or prim.HasAuthoredSpecializes()
        or prim.HasVariantSets(),
        unloaded=prim.HasAuthoredPayloads() and not prim.IsLoaded(),
    )


class PrimModel(QAbstractItemModel):
    """Tree of the document's prims: name, type, and visibility.

    Editing a name or dropping rows only *requests* the change through
    :attr:`renameRequested` and :attr:`reparentRequested`; the owner applies
    it to the stage, and the resulting resync rebuilds the model.

    The tree traverses instance proxies, so instanced scenes expand, and
    lists the prims :attr:`filter` admits. The editor's session helper
    prims under ``/_StageEditor`` are never listed. Any prim resync in
    :attr:`StageDocument.changed` rebuilds the model; property and
    info-only changes refresh their rows.

    Signals:
        renameRequested(str, str): Rename the prim at a path to a name.
        reparentRequested(list, str): Move prim paths under a parent path.

    Args:
        document: The document to mirror.
        parent: Owning ``QObject``.
    """

    renameRequested = Signal(str, str)
    reparentRequested = Signal(list, str)

    #: ``data`` role holding the prim path as a string.
    PathRole = Qt.ItemDataRole.UserRole + 1
    HEADERS = ("Name", "Type", "Vis")
    NAME, TYPE, VIS = range(3)

    def __init__(self, document: StageDocument, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._filter = PrimFilter()
        self._root = _Node("/", "", "")
        self._by_path: dict[str, _Node] = {}
        document.stageReplaced.connect(self._rebuild)
        document.changed.connect(self._on_changed)
        self._rebuild()

    # -- structure ------------------------------------------------------------

    @property
    def filter(self) -> PrimFilter:
        """A copy of the current filter."""
        return dataclasses.replace(self._filter)

    def set_filter(self, prim_filter: PrimFilter) -> None:
        """List the prims ``prim_filter`` admits.

        Args:
            prim_filter: The new filter; the model keeps a copy.
        """
        self._filter = dataclasses.replace(prim_filter)
        self._rebuild()

    def _rebuild(self) -> None:
        self.beginResetModel()
        self._root = _Node("/", "", "")
        self._by_path = {}
        stage = self._document.stage
        predicate = Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)
        roots = [stage.GetPseudoRoot()]
        stack: list[tuple[Usd.Prim, _Node]] = []
        for prim in roots:
            self._add_children(prim, self._root, predicate, stack)
        if self._filter.prototypes:
            for prototype in stage.GetPrototypes():
                self._append(prototype, self._root, stack)
        while stack:
            prim, node = stack.pop()
            self._add_children(prim, node, predicate, stack)
        self.endResetModel()

    def _add_children(
        self,
        prim: Usd.Prim,
        node: _Node,
        predicate: object,
        stack: list[tuple[Usd.Prim, _Node]],
    ) -> None:
        for child in prim.GetFilteredChildren(predicate):
            if child.GetPath() == SESSION_ROOT or not self._filter.admits(child):
                continue
            self._append(child, node, stack)

    def _append(
        self, prim: Usd.Prim, node: _Node, stack: list[tuple[Usd.Prim, _Node]]
    ) -> None:
        child_node = _node_for(prim, node)
        child_node.row = len(node.children)
        node.children.append(child_node)
        self._by_path[child_node.path] = child_node
        stack.append((prim, child_node))

    def _on_changed(self, resynced: list[str], info: list[str]) -> None:
        if any(not Sdf.Path(path).IsPropertyPath() for path in resynced):
            self._rebuild()
            return
        for path in info + [p for p in resynced if Sdf.Path(p).IsPropertyPath()]:
            prim_path = Sdf.Path(path).GetPrimPath().pathString
            index = self.index_for_path(prim_path)
            if index.isValid():
                self.dataChanged.emit(index, index.siblingAtColumn(self.VIS))

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

    def is_visible(self, path: str) -> bool:
        """Return the computed visibility of the prim at ``path`` now.

        Args:
            path: Prim path as a string; non-imageable prims count as visible.
        """
        prim = self._document.stage.GetPrimAtPath(path)
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            return True
        time = Usd.TimeCode(self._document.time)
        return imageable.ComputeVisibility(time) != UsdGeom.Tokens.invisible

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
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == self.NAME:
                return node.name
            if column == self.TYPE:
                return node.type_name
            if column == self.VIS:
                prim = self._document.stage.GetPrimAtPath(node.path)
                if not prim or not UsdGeom.Imageable(prim):
                    return ""
                return "V" if self.is_visible(node.path) else "I"
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            return QBrush(self._color(node))
        if role == Qt.ItemDataRole.FontRole:
            font = QFont()
            font.setItalic(not node.defined or node.abstract or node.unloaded)
            font.setBold(node.instance)
            return font
        if role == Qt.ItemDataRole.TextAlignmentRole and column == self.VIS:
            return Qt.AlignmentFlag.AlignCenter
        if role == self.PathRole:
            return node.path
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node)
        return None

    def _color(self, node: _Node) -> QColor:
        if not node.active:
            return INACTIVE_COLOR
        if node.in_prototype:
            return PROTOTYPE_COLOR
        if node.instance:
            return INSTANCE_COLOR
        if node.instance_proxy:
            return INSTANCE_PROXY_COLOR
        if node.has_arcs:
            return ARCS_COLOR
        return NORMAL_COLOR

    def _tooltip(self, node: _Node) -> str:
        notes = [
            label
            for flag, label in (
                (not node.active, "inactive"),
                (not node.defined, "over"),
                (node.abstract, "class"),
                (node.instance, "instance"),
                (node.instance_proxy, "instance proxy"),
                (node.in_prototype, "prototype"),
                (node.has_arcs, "has composition arcs"),
                (node.unloaded, "payload unloaded"),
            )
            if flag
        ]
        return node.path + (f"\n{', '.join(notes)}" if notes else "")

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

    # -- editing: rename and drag to reparent -----------------------------------

    def flags(self, index: _ModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index) | Qt.ItemFlag.ItemIsDropEnabled
        if not index.isValid():
            return flags
        node = self._node(index)
        if not node.instance_proxy and not node.in_prototype:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
            if index.column() == self.NAME:
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(
        self, index: _ModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or index.column() != self.NAME:
            return False
        node = self._node(index)
        name = str(value).strip()
        if name and name != node.name:
            path = node.path
            # The rename rebuilds this model; let the editor close first.
            QTimer.singleShot(0, lambda: self.renameRequested.emit(path, name))
        return False

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [MIME_TYPE]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        paths = list(dict.fromkeys(self.path_at(i) for i in indexes if i.isValid()))
        data = QMimeData()
        data.setData(MIME_TYPE, "\n".join(paths).encode("utf-8"))
        return data

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: _ModelIndex,
    ) -> bool:
        if not data.hasFormat(MIME_TYPE):
            return False
        text = bytes(data.data(MIME_TYPE).data()).decode("utf-8")
        paths = [p for p in text.split("\n") if p]
        target = self.path_at(parent) or "/"
        if paths:
            # Reparenting rebuilds the model; never inside the drop event.
            QTimer.singleShot(0, lambda: self.reparentRequested.emit(paths, target))
        # False: the view must not remove the dragged rows itself.
        return False


class _SearchProxy(QSortFilterProxyModel):
    """Keep rows whose name contains the search text, with their ancestors."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(PrimModel.NAME)


class Outliner(QWidget):
    """Prim browser: search, filters, a visibility column, and a context menu.

    Clicking the Vis column toggles a session-layer visibility override.
    The context menu offers framing, look-through for cameras, visibility,
    activation, payload loading, and resetting session overrides.
    Expansion and selection survive model rebuilds.

    Signals:
        selectedPathsChanged(list): The selected prim paths, as strings,
            after a user selection change. Programmatic changes through
            :meth:`set_selected_paths` do not emit.
        frameRequested(list): Frame these prim paths in the view.
        cameraRequested(str): Look through the camera at this path.
        renameRequested(str, str): Rename a prim (F2 or double-click).
        reparentRequested(list, str): Move prims under a parent (drag).

    Args:
        document: The document to show.
        parent: Owning widget.
    """

    selectedPathsChanged = Signal(list)
    frameRequested = Signal(list)
    cameraRequested = Signal(str)
    renameRequested = Signal(str, str)
    reparentRequested = Signal(list, str)

    def __init__(self, document: StageDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._model = PrimModel(document, self)
        self._proxy = _SearchProxy(self)
        self._proxy.setSourceModel(self._model)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Find prim")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        self._filter_button = QToolButton(self)
        self._filter_button.setText("Show")
        self._filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._filter_menu = QMenu(self._filter_button)
        self._filter_actions: dict[str, QAction] = {}
        for name, label in (
            ("inactive", "Inactive Prims"),
            ("undefined", "Undefined Prims (overs)"),
            ("abstract", "Abstract Prims (classes)"),
            ("prototypes", "Prototypes"),
        ):
            action = self._filter_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(getattr(self._model.filter, name))
            action.toggled.connect(lambda on, n=name: self._on_filter(n, on))
            self._filter_actions[name] = action
        self._filter_button.setMenu(self._filter_menu)

        self._view = QTreeView(self)
        self._view.setModel(self._proxy)
        self._view.setHeaderHidden(False)
        self._view.setUniformRowHeights(True)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._view.setExpandsOnDoubleClick(True)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.clicked.connect(self._on_clicked)
        self._view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._view.setDropIndicatorShown(True)
        self._view.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._model.renameRequested.connect(self.renameRequested)
        self._model.reparentRequested.connect(self.reparentRequested)
        #: Called with the menu and selected paths to add editing actions.
        self.menu_hook: Callable[[QMenu, list[str]], None] | None = None
        header = self._view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(PrimModel.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(PrimModel.TYPE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PrimModel.VIS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(PrimModel.TYPE, 90)
        header.resizeSection(PrimModel.VIS, 32)
        self._updating = False
        self._saved_expanded: set[str] = set()
        self._saved_selection: list[str] = []

        top = QHBoxLayout()
        top.setContentsMargins(4, 4, 4, 0)
        top.addWidget(self._search, 1)
        top.addWidget(self._filter_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._view)
        selection = self._view.selectionModel()
        assert selection is not None
        selection.selectionChanged.connect(self._on_selection_changed)
        self._model.modelAboutToBeReset.connect(self._remember_state)
        self._model.modelReset.connect(self._restore_state)
        document.stageReplaced.connect(self._forget_state)
        # Computed visibility inherits and varies with time: repaint on both.
        document.timeChanged.connect(self._repaint)
        document.changed.connect(self._repaint)
        self._expand_top()

    @property
    def model(self) -> PrimModel:
        """The prim model (unfiltered by search)."""
        return self._model

    @property
    def view(self) -> QTreeView:
        """The tree view, for tests and hosts that tune it. Its model is a proxy."""
        return self._view

    def focus_search(self) -> None:
        """Put the keyboard focus in the search box."""
        self._search.setFocus()
        self._search.selectAll()

    def set_search(self, text: str) -> None:
        """Show only prims whose name contains ``text`` and their ancestors.

        Args:
            text: Case-insensitive text; empty shows everything.
        """
        self._search.setText(text)

    def copy_paths(self) -> None:
        """Copy the selected prim paths to the clipboard, one per line."""
        paths = self.selected_paths()
        clipboard = QGuiApplication.clipboard()
        if paths and clipboard is not None:
            clipboard.setText("\n".join(paths))

    def selected_paths(self) -> list[str]:
        """Return the selected prim paths in view order."""
        selection = self._view.selectionModel()
        assert selection is not None
        return [
            self._model.path_at(self._proxy.mapToSource(index))
            for index in selection.selectedRows()
        ]

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
                index = self._proxy.mapFromSource(self._model.index_for_path(path))
                if not index.isValid():
                    continue
                selection.select(index, flags)
                if not first.isValid():
                    first = index
            if first.isValid():
                self._view.scrollTo(first)
                selection.setCurrentIndex(
                    first, QItemSelectionModel.SelectionFlag.NoUpdate
                )
        finally:
            self._updating = False

    # -- state across rebuilds --------------------------------------------------

    def _remember_state(self) -> None:
        self._saved_selection = self.selected_paths()
        expanded: set[str] = set()
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self._proxy.rowCount(parent)):
                index = self._proxy.index(row, 0, parent)
                if self._view.isExpanded(index):
                    expanded.add(self._model.path_at(self._proxy.mapToSource(index)))
                    stack.append(index)
        self._saved_expanded = expanded

    def _restore_state(self) -> None:
        if not self._saved_expanded:
            self._expand_top()
        for path in self._saved_expanded:
            index = self._proxy.mapFromSource(self._model.index_for_path(path))
            if index.isValid():
                self._view.setExpanded(index, True)
        if self._saved_selection:
            self.set_selected_paths(self._saved_selection)

    def _forget_state(self) -> None:
        self._saved_expanded = set()
        self._saved_selection = []
        self._expand_top()

    def _expand_top(self) -> None:
        self._view.expandToDepth(0)

    # -- slots ----------------------------------------------------------------

    def _repaint(self, *_args: object) -> None:
        viewport = self._view.viewport()
        if viewport is not None:
            viewport.update()

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        if text:
            self._view.expandAll()

    def _on_filter(self, name: str, on: bool) -> None:
        prim_filter = self._model.filter
        setattr(prim_filter, name, on)
        self._model.set_filter(prim_filter)

    def _on_clicked(self, index: QModelIndex) -> None:
        if index.column() != PrimModel.VIS:
            return
        path = self._model.path_at(self._proxy.mapToSource(index))
        if not path or not index.data():
            return
        self._document.set_visible([path], not self._model.is_visible(path))

    def edit_current_name(self) -> None:
        """Start editing the name of the current row (F2)."""
        index = self._view.currentIndex()
        if index.isValid():
            self._view.edit(index.siblingAtColumn(PrimModel.NAME))

    def _on_context_menu(self, position: QPoint) -> None:
        paths = self.selected_paths()
        menu = QMenu(self)
        viewport = self._view.viewport()
        assert viewport is not None
        if self.menu_hook is not None:
            self.menu_hook(menu, paths)
        if not paths:
            if not menu.isEmpty():
                menu.exec(viewport.mapToGlobal(position))
            return
        menu.addSeparator()
        stage = self._document.stage
        prims = [stage.GetPrimAtPath(p) for p in paths]
        menu.addAction("Frame", lambda: self.frameRequested.emit(paths))
        if len(prims) == 1 and prims[0].IsA(UsdGeom.Camera):
            menu.addAction("Look Through", lambda: self.cameraRequested.emit(paths[0]))
        menu.addAction("Copy Prim Path", self.copy_paths)
        menu.addSeparator()
        menu.addAction("Make Visible", lambda: self._document.set_visible(paths, True))
        menu.addAction(
            "Make Invisible", lambda: self._document.set_visible(paths, False)
        )
        menu.addAction("Vis Only", lambda: self._document.show_only(paths))
        menu.addSeparator()
        menu.addAction("Activate", lambda: self._document.set_active(paths, True))
        menu.addAction("Deactivate", lambda: self._document.set_active(paths, False))
        if any(p.HasAuthoredPayloads() or p.GetChildren() for p in prims):
            menu.addAction("Load", lambda: self._document.load(paths, True))
            menu.addAction("Unload", lambda: self._document.load(paths, False))
        menu.addSeparator()
        menu.addAction(
            "Reset Session Overrides",
            lambda: self._document.clear_session_overrides(paths),
        )
        menu.addSeparator()
        menu.addAction("Expand All", self._view.expandAll)
        menu.addAction("Collapse All", self._view.collapseAll)
        menu.exec(viewport.mapToGlobal(position))

    def _on_selection_changed(
        self, _selected: QItemSelection, _deselected: QItemSelection
    ) -> None:
        if not self._updating:
            self.selectedPathsChanged.emit(self.selected_paths())
