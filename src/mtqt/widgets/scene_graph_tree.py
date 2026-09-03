"""Composite tree view over a nested list-of-lists scene graph."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QModelIndex, Signal
from PySide6.QtWidgets import QTreeView, QVBoxLayout, QWidget

from mtqt.models.scene_graph_model import SceneGraphModel


class SceneGraphTree(QWidget):
    """A ``QTreeView`` bound to a :class:`~mtqt.models.SceneGraphModel`.

    Compose this widget instead of subclassing ``QTreeView``. Names are
    UIDs; :attr:`currentNameChanged` emits the selected name as ``str``.

    Args:
        nodes: Forest of nested name lists. Ignored when ``path`` is set.
        parent: Qt parent. Pass the owning window or layout widget so
            lifetime follows Qt ownership. ``None`` means Python owns this
            widget until it is parented or goes out of scope.
        path: JSON file of nested name lists. When set, loaded instead of
            ``nodes``.
    """

    currentNameChanged = Signal(str)

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
        self._view.expandAll()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        selection = self._view.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._on_current_changed)

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
        """UID of the current selection, or ``""`` when none."""
        index = self._view.currentIndex()
        if not index.isValid():
            return ""
        return self._model.node_at(index).name

    def load_json(self, path: Path) -> None:
        """Replace the forest from a JSON file and expand all rows."""
        self._model.set_nodes(json.loads(path.read_text(encoding="utf-8")))
        self._view.expandAll()

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            self.currentNameChanged.emit("")
            return
        self.currentNameChanged.emit(self._model.node_at(current).name)
