"""Layer panel: the stage's layer stack, muting, edit target, and layer text.

:func:`layer_rows` lists the session layer stack, the root layer stack
(sublayers nested, muted ones included), and every other layer the stage
uses through references and payloads. :class:`LayerPanel` shows the rows
and acts on them through the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pxr import Sdf, Usd
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stage_editor.document import USD_SUFFIXES, StageDocument

#: Layers larger than this many characters of usda are cut short in the viewer.
TEXT_LIMIT = 4_000_000


@dataclass(frozen=True)
class LayerRow:
    """One layer as the panel lists it.

    Attributes:
        identifier: The layer identifier (or the unresolved sublayer path
            of a muted layer that is not open).
        name: Display name.
        group: ``session``, ``root``, or ``used`` (referenced or payload).
        depth: Nesting depth under its group's top layer.
        muted: The stage mutes this layer.
        dirty: The layer has unsaved edits.
        edit_target: Edits currently go to this layer.
        editable: The layer is in the local layer stack, so it can be the
            edit target.
        anonymous: The layer has no file on disk.
        offset: Sublayer offset and scale, ``""`` if identity.
    """

    identifier: str
    name: str
    group: str
    depth: int
    muted: bool = False
    dirty: bool = False
    edit_target: bool = False
    editable: bool = False
    anonymous: bool = False
    offset: str = ""


def layer_rows(stage: Usd.Stage) -> list[LayerRow]:
    """Return the session stack, root stack, then other used layers.

    Args:
        stage: The stage to describe.
    """
    target = stage.GetEditTarget().GetLayer()
    muted = set(stage.GetMutedLayers())
    rows: list[LayerRow] = []
    seen: set[str] = set()

    def walk(layer: Sdf.Layer, group: str, depth: int, offset: str = "") -> None:
        seen.add(layer.identifier)
        rows.append(
            LayerRow(
                layer.identifier,
                layer.GetDisplayName() or layer.identifier,
                group,
                depth,
                muted=layer.identifier in muted,
                dirty=layer.dirty,
                edit_target=layer == target,
                editable=True,
                anonymous=layer.anonymous,
                offset=offset,
            )
        )
        offsets = layer.subLayerOffsets
        for i, sub_path in enumerate(layer.subLayerPaths):
            resolved = Sdf.ComputeAssetPathRelativeToLayer(layer, sub_path)
            layer_offset = offsets[i] if i < len(offsets) else Sdf.LayerOffset()
            text = (
                ""
                if layer_offset.IsIdentity()
                else f"offset {layer_offset.offset:g} scale {layer_offset.scale:g}"
            )
            sub = Sdf.Layer.Find(resolved)
            if sub is None or resolved in muted or sub.identifier in muted:
                rows.append(
                    LayerRow(
                        resolved,
                        Path(sub_path).name,
                        group,
                        depth + 1,
                        muted=True,
                        offset=text,
                    )
                )
                seen.add(resolved)
                continue
            walk(sub, group, depth + 1, text)

    walk(stage.GetSessionLayer(), "session", 0)
    walk(stage.GetRootLayer(), "root", 0)
    used = [layer for layer in stage.GetUsedLayers() if layer.identifier not in seen]
    for layer in sorted(used, key=lambda lyr: lyr.GetDisplayName().lower()):
        rows.append(
            LayerRow(
                layer.identifier,
                layer.GetDisplayName() or layer.identifier,
                "used",
                0,
                muted=layer.identifier in muted,
                dirty=layer.dirty,
                anonymous=layer.anonymous,
            )
        )
    for identifier in sorted(muted - {row.identifier for row in rows}):
        rows.append(LayerRow(identifier, Path(identifier).name, "used", 0, muted=True))
    return rows


def show_text(parent: QWidget, title: str, text: str) -> None:
    """Show read-only text, such as a layer's usda, in a modeless dialog.

    Args:
        parent: Owner of the dialog.
        title: Window title.
        text: The text; very long text is cut short at :data:`TEXT_LIMIT`.
    """
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT] + f"\n# ... cut at {TEXT_LIMIT} characters"
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.resize(900, 700)
    pane = QPlainTextEdit(dialog)
    pane.setReadOnly(True)
    pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    pane.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    pane.setPlainText(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
    buttons.rejected.connect(dialog.close)
    layout = QVBoxLayout(dialog)
    layout.addWidget(pane)
    layout.addWidget(buttons)
    dialog.show()


GROUP_TITLES = {
    "session": "Session layer stack",
    "root": "Root layer stack",
    "used": "Referenced layers",
}


class LayerPanel(QWidget):
    """Tree of the stage's layers with edit target, mute, and text views.

    Double-click a local layer to make it the edit target. The context menu
    mutes and unmutes, reloads, saves a dirty sublayer, shows a layer as
    ``usda``, and adds an existing or new sublayer to a local layer.

    Args:
        document: The document whose stage is shown.
        parent: Owning widget.
    """

    HEADERS = ("Layer", "Target", "Muted", "Dirty", "Offset")

    def __init__(self, document: StageDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._pending = False
        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(list(self.HEADERS))
        self._tree.setUniformRowHeights(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, 52)
        header.resizeSection(len(self.HEADERS) - 1, 110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)
        document.stageReplaced.connect(self.refresh)
        document.layersChanged.connect(self._schedule)
        document.changed.connect(self._on_changed)
        self.refresh()

    @property
    def tree(self) -> QTreeWidget:
        """The tree widget, for tests."""
        return self._tree

    def refresh(self) -> None:
        """Rebuild the tree from the stage."""
        self._pending = False
        self._tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        parents: list[QTreeWidgetItem] = []
        for row in layer_rows(self._document.stage):
            group = groups.get(row.group)
            if group is None:
                group = QTreeWidgetItem([GROUP_TITLES[row.group]])
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
                self._tree.addTopLevelItem(group)
                groups[row.group] = group
                parents = [group]
            item = QTreeWidgetItem(
                [
                    row.name,
                    "edit" if row.edit_target else "",
                    "muted" if row.muted else "",
                    "*" if row.dirty else "",
                    row.offset,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            item.setToolTip(0, row.identifier)
            if row.muted:
                item.setForeground(0, Qt.GlobalColor.gray)
            if row.edit_target:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            del parents[row.depth + 1 :]
            parents[-1].addChild(item)
            parents.append(item)
        self._tree.expandAll()

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self.refresh)

    def _on_changed(self, resynced: list[str], _info: list[str]) -> None:
        # Sublayer edits and muting resync the whole stage.
        if "/" in resynced:
            self._schedule()

    def _row(self, item: QTreeWidgetItem | None) -> LayerRow | None:
        if item is None:
            return None
        row = item.data(0, Qt.ItemDataRole.UserRole)
        return row if isinstance(row, LayerRow) else None

    def _layer(self, row: LayerRow) -> Sdf.Layer | None:
        return Sdf.Layer.Find(row.identifier)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        row = self._row(item)
        if row is not None and row.editable and not row.muted:
            self._set_target(row)

    def _set_target(self, row: LayerRow) -> None:
        layer = self._layer(row)
        if layer is not None:
            self._document.set_edit_target(layer)

    def _on_context_menu(self, position: QPoint) -> None:
        row = self._row(self._tree.itemAt(position))
        if row is None:
            return
        menu = QMenu(self)
        stage = self._document.stage
        fixed = row.identifier in (
            stage.GetRootLayer().identifier,
            stage.GetSessionLayer().identifier,
        )
        if row.editable and not row.muted and not row.edit_target:
            menu.addAction("Set as Edit Target", lambda: self._set_target(row))
        if not fixed:
            label = "Unmute" if row.muted else "Mute"
            menu.addAction(label, lambda: self._set_muted(row, not row.muted))
        if not row.muted:
            menu.addAction("Show as usda", lambda: self._show_text(row))
            if not row.anonymous:
                menu.addAction("Reload", lambda: self._reload(row))
            if row.dirty and not row.anonymous and row.group != "session":
                menu.addAction("Save Layer", lambda: self._save(row))
            if row.editable:
                menu.addSeparator()
                menu.addAction(
                    "Add Sublayer...", lambda: self._add_sublayer(row, False)
                )
                menu.addAction("New Sublayer...", lambda: self._add_sublayer(row, True))
        viewport = self._tree.viewport()
        assert viewport is not None
        menu.exec(viewport.mapToGlobal(position))

    def _set_muted(self, row: LayerRow, muted: bool) -> None:
        layer = self._layer(row)
        stage = self._document.stage
        if layer is not None:
            self._document.set_layer_muted(layer, muted)
        elif not muted:
            # A muted sublayer that never opened is known only by identifier.
            stage.UnmuteLayer(row.identifier)
            self._schedule()

    def _show_text(self, row: LayerRow) -> None:
        layer = self._layer(row)
        if layer is not None:
            show_text(self, row.name, layer.ExportToString())

    def _reload(self, row: LayerRow) -> None:
        layer = self._layer(row)
        if layer is None:
            return
        if layer.dirty:
            answer = QMessageBox.question(
                self,
                "Reload",
                f"{row.name} has unsaved edits. Discard them and reload?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        layer.Reload(force=True)
        self._schedule()

    def _save(self, row: LayerRow) -> None:
        layer = self._layer(row)
        if layer is not None and not layer.Save():
            QMessageBox.warning(self, "Save Layer", f"cannot save {row.identifier}")
        self._schedule()

    def _add_sublayer(self, row: LayerRow, create: bool) -> None:
        parent = self._layer(row)
        if parent is None:
            return
        file_filter = "USD files (" + " ".join(f"*{s}" for s in USD_SUFFIXES) + ")"
        if create:
            name, _ = QFileDialog.getSaveFileName(self, "New Sublayer", "", file_filter)
        else:
            name, _ = QFileDialog.getOpenFileName(self, "Add Sublayer", "", file_filter)
        if not name:
            return
        path = Path(name)
        if create:
            if not path.suffix:
                path = path.with_suffix(".usda")
            layer = Sdf.Layer.CreateNew(str(path))
            if layer is None:
                QMessageBox.warning(self, "New Sublayer", f"cannot create {path}")
                return
        parent.subLayerPaths.insert(0, str(path))
