"""Composite tree view over a nested list-of-lists scene graph."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
)
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QTreeView, QVBoxLayout, QWidget

from mtqt.models.scene_graph_model import SceneGraphModel

# Keys the tree moves through rows with. Claimed before the host's window
# shortcuts so, for example, a host's Home shortcut doesn't steal them.
_NAVIGATION_KEYS = frozenset(
    {
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
    }
)


class SceneGraphTree(QWidget):
    """A ``QTreeView`` bound to a :class:`~mtqt.models.SceneGraphModel`.

    Compose this widget instead of subclassing ``QTreeView``. Names are
    UIDs. :attr:`currentNameChanged` emits the current name;
    :attr:`selectedNamesChanged` emits the full multi-selection as a list.
    While the tree has focus, its navigation keys (arrows, Home, End, Page
    Up/Down, alone or with Shift) win over the host window's shortcuts.

    Args:
        nodes: Forest of nested name lists. Ignored when ``path`` is set.
        parent: Qt parent. Pass the owning window or layout widget so
            lifetime follows Qt ownership. ``None`` means Python owns this
            widget until it is parented or goes out of scope.
        path: JSON file of nested name lists. When set, loaded instead of
            ``nodes``.
    """

    currentNameChanged = Signal(str)
    selectedNamesChanged = Signal(list)

    def __init__(
        self,
        nodes: Sequence[object] | None = None,
        parent: QWidget | None = None,
        *,
        path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SceneGraphTree")
        self.setAccessibleName("Scene graph")

        if path is not None:
            self._model = SceneGraphModel.from_json(path, parent=self)
        else:
            self._model = SceneGraphModel(nodes, parent=self)

        self._view = QTreeView(self)
        self._view.setObjectName("SceneGraphTreeView")
        self._view.setModel(self._model)
        self._view.setUniformRowHeights(True)
        self._view.setHeaderHidden(True)
        self._view.setIndentation(16)
        self._view.setAnimated(True)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._view.expandAll()
        self._view.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._view)

        selection = self._view.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._on_current_changed)
            selection.selectionChanged.connect(self._on_selection_changed)

    @property
    def model(self) -> SceneGraphModel:
        """Item model driving the tree."""
        return self._model

    @property
    def view(self) -> QTreeView:
        """Inner ``QTreeView``."""
        return self._view

    @property
    def current_name(self) -> str:
        """UID of the current item, or ``""`` when none."""
        index = self._view.currentIndex()
        if not index.isValid():
            return ""
        return self._model.node_at(index).name

    @property
    def selected_names(self) -> list[str]:
        """UIDs of all selected rows, in view order."""
        selection = self._view.selectionModel()
        if selection is None:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for index in selection.selectedRows():
            name = self._model.node_at(index).name
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names

    def set_selected_names(self, names: Sequence[str]) -> None:
        """Replace the selection with the named nodes.

        Unknown names are skipped. The last valid name becomes current.
        An empty sequence clears the selection.

        Args:
            names: Scene node UIDs to select.
        """
        selection_model = self._view.selectionModel()
        if selection_model is None:
            return

        item_selection = QItemSelection()
        current = QModelIndex()
        for name in names:
            index = self._model.index_for_name(name)
            if not index.isValid():
                continue
            item_selection.select(index, index)
            current = index

        selection_model.select(
            item_selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        if current.isValid():
            selection_model.setCurrentIndex(
                current,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        else:
            self._view.setCurrentIndex(QModelIndex())

    def load_json(self, path: Path) -> None:
        """Replace the forest from a JSON file and expand all rows."""
        self._model.set_nodes(json.loads(path.read_text(encoding="utf-8")))
        self._view.expandAll()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self._view
            and event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and _is_navigation_key(event)
        ):
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            self.currentNameChanged.emit("")
            return
        self.currentNameChanged.emit(self._model.node_at(current).name)

    def _on_selection_changed(
        self,
        _selected: QItemSelection,
        _deselected: QItemSelection,
    ) -> None:
        self.selectedNamesChanged.emit(self.selected_names)


def _is_navigation_key(event: QKeyEvent) -> bool:
    """Return whether ``event`` is a plain or Shift row-navigation key."""
    modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
    if modifiers not in (
        Qt.KeyboardModifier.NoModifier,
        Qt.KeyboardModifier.ShiftModifier,
    ):
        return False
    return Qt.Key(event.key()) in _NAVIGATION_KEYS
