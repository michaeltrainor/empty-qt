"""Inspector: usdview's attribute, metadata, layer stack, and composition views.

All four tabs follow one prim, set with :meth:`Inspector.set_prim_path`,
and refresh when the document changes or time moves. Attribute values
and variant selections are editable; edits go to the current edit target
and can be undone.
"""

from __future__ import annotations

from collections.abc import Callable

from pxr import Sdf, Usd
from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stage_editor.document import StageDocument
from stage_editor.inspection import (
    PropertyRow,
    composition_rows,
    format_value,
    metadata_rows,
    prim_index_text,
    prim_stack_rows,
    property_details,
    property_rows,
    property_value,
    spec_text,
    variant_rows,
)
from stage_editor.undo import parse_value

_ModelIndex = QModelIndex | QPersistentModelIndex

#: usdview's value-source colours.
SOURCE_COLORS: dict[str, QColor] = {
    "time samples": QColor(177, 207, 153),
    "default": QColor(135, 206, 250),
    "fallback": QColor(222, 158, 46),
    "value clips": QColor(230, 150, 230),
    "spline": QColor(177, 207, 153),
    "none": QColor(140, 140, 140),
    "targets": QColor(227, 227, 227),
    "computed": QColor(200, 200, 160),
}


def _monospace() -> QFont:
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


class PropertyModel(QAbstractTableModel):
    """Properties of one prim with their values and value sources at stage time.

    Values are cached per row and cleared when time moves or the prim's
    properties change, so painting a large mesh does not re-read arrays.
    Editing a Value cell writes the attribute to the edit target through
    :meth:`StageDocument.set_attribute`, which is undoable.

    Signals:
        editMessage(str): Why an edit was refused or had no visible effect.

    Args:
        document: The document whose prims and time are shown.
        parent: Owning ``QObject``.
    """

    editMessage = Signal(str)

    HEADERS = ("Name", "Type", "Value", "Source")
    NAME, TYPE, VALUE, SOURCE = range(4)

    def __init__(self, document: StageDocument, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._path = ""
        self._rows: list[PropertyRow] = []
        self._values: dict[int, tuple[str, str]] = {}
        document.timeChanged.connect(self._on_time)

    @property
    def prim_path(self) -> str:
        """The shown prim's path, or ``""``."""
        return self._path

    def prim(self) -> Usd.Prim | None:
        """Return the shown prim, or ``None`` when there is none or it is gone."""
        if not self._path:
            return None
        prim = self._document.stage.GetPrimAtPath(self._path)
        return prim if prim and prim.IsValid() else None

    def set_prim_path(self, path: str) -> None:
        """Show the properties of the prim at ``path``; ``""`` clears.

        Args:
            path: Prim path as a string.
        """
        self.beginResetModel()
        self._path = path
        prim = self.prim()
        self._rows = property_rows(prim) if prim is not None else []
        self._values = {}
        self.endResetModel()

    def row_at(self, row: int) -> PropertyRow | None:
        """Return the :class:`PropertyRow` at ``row``, or ``None``."""
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def refresh_values(self) -> None:
        """Re-read every value, for example after an info-only change."""
        self._values = {}
        if self._rows:
            self.dataChanged.emit(
                self.index(0, self.VALUE), self.index(len(self._rows) - 1, self.SOURCE)
            )

    def _on_time(self, _time: float) -> None:
        self.refresh_values()

    def _value(self, row: int) -> tuple[str, str]:
        cached = self._values.get(row)
        if cached is None:
            prim = self.prim()
            if prim is None:
                return ("", "none")
            cached = property_value(prim, self._rows[row], self._document.time)
            self._values[row] = cached
        return cached

    # -- QAbstractTableModel ----------------------------------------------------

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(
        self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if column == self.NAME:
                return row.name
            if column == self.TYPE:
                return row.type_name
            value, source = self._value(index.row())
            return value if column == self.VALUE else source
        if role == Qt.ItemDataRole.ForegroundRole and column in (
            self.VALUE,
            self.SOURCE,
        ):
            _value, source = self._value(index.row())
            return QBrush(SOURCE_COLORS.get(source, SOURCE_COLORS["none"]))
        if role == Qt.ItemDataRole.FontRole and column == self.NAME:
            font = QFont()
            font.setItalic(row.custom or row.kind == "computed")
            return font
        if role == Qt.ItemDataRole.EditRole and column == self.VALUE:
            prim = self.prim()
            if prim is None:
                return ""
            value = prim.GetAttribute(row.name).Get(Usd.TimeCode(self._document.time))
            return format_value(value, limit=0)
        return None

    def is_editable(self, row: int) -> bool:
        """Return whether the Value cell of ``row`` accepts text edits.

        Scalar, string, vector, colour, and quaternion attributes are
        editable; arrays, matrices, relationships, computed rows, and
        instance proxies are not.
        """
        prim = self.prim()
        info = self.row_at(row)
        if prim is None or info is None or info.kind != "attribute":
            return False
        if prim.IsInstanceProxy():
            return False
        type_name = prim.GetAttribute(info.name).GetTypeName()
        try:
            parse_value(type_name, format_value(type_name.defaultValue, limit=0))
        except ValueError:
            return False
        return True

    def flags(self, index: _ModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if (
            index.isValid()
            and index.column() == self.VALUE
            and self.is_editable(index.row())
        ):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(
        self, index: _ModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or not self.is_editable(index.row()):
            return False
        prim = self.prim()
        row = self.row_at(index.row())
        if prim is None or row is None:
            return False
        attr = prim.GetAttribute(row.name)
        try:
            parsed = parse_value(attr.GetTypeName(), str(value))
        except ValueError as exc:
            self.editMessage.emit(str(exc))
            return False
        if not self._document.set_attribute(str(attr.GetPath()), parsed):
            self.editMessage.emit(
                f"{row.name} was written to "
                f"{self._document.edit_target_layer.GetDisplayName()}, "
                "but a stronger layer's opinion still wins"
            )
        self.refresh_values()
        return True

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


def _tree(headers: tuple[str, ...], parent: QWidget) -> QTreeWidget:
    tree = QTreeWidget(parent)
    tree.setHeaderLabels(list(headers))
    tree.setRootIsDecorated(False)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(False)
    header = tree.header()
    header.setStretchLastSection(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return tree


def _text_pane(parent: QWidget) -> QPlainTextEdit:
    pane = QPlainTextEdit(parent)
    pane.setReadOnly(True)
    pane.setFont(_monospace())
    pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return pane


class Inspector(QWidget):
    """Tabs describing one prim: Properties, Metadata, Layer Stack, Composition.

    * **Properties**: attributes, relationships, and computed world
      transform, bound, and material, with each value's source (time
      samples, default, fallback, none). Selecting a row shows the whole
      value, its time samples, and its authored metadata.
    * **Metadata**: composed prim metadata and the variant sets, whose
      selections can be changed.
    * **Layer Stack**: every spec contributing to the prim, strongest
      first; selecting one shows it as ``usda``.
    * **Composition**: the composition arcs, and the Pcp prim index dump.

    Signals:
        message(str): A status message, such as why an edit was refused.

    Args:
        document: The document to inspect.
        parent: Owning widget.
    """

    message = Signal(str)

    def __init__(self, document: StageDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._path = ""
        self._refresh_pending = False

        self._title = QLabel("No prim selected", self)
        self._title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._tabs = QTabWidget(self)

        # Properties
        self._properties = PropertyModel(document, self)
        self._properties.editMessage.connect(self.message)
        self._table = QTableView(self)
        self._table.setModel(self._properties)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)
        self._table.verticalHeader().hide()
        self._table.verticalHeader().setDefaultSectionSize(22)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(
            PropertyModel.NAME, QHeaderView.ResizeMode.Interactive
        )
        header.setSectionResizeMode(PropertyModel.VALUE, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(PropertyModel.NAME, 170)
        header.resizeSection(PropertyModel.TYPE, 80)
        header.resizeSection(PropertyModel.SOURCE, 90)
        self._details = _text_pane(self)
        selection = self._table.selectionModel()
        assert selection is not None
        selection.selectionChanged.connect(self._on_property_selected)
        self._properties.modelReset.connect(self._details.clear)
        props = QSplitter(Qt.Orientation.Vertical, self)
        props.addWidget(self._table)
        props.addWidget(self._details)
        props.setSizes([500, 160])
        self._tabs.addTab(props, "Properties")

        # Metadata
        meta = QWidget(self)
        self._variants = QGroupBox("Variants", meta)
        self._variant_form = QFormLayout(self._variants)
        self._metadata = _tree(("Field", "Value"), meta)
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(0, 4, 0, 0)
        meta_layout.addWidget(self._variants)
        meta_layout.addWidget(self._metadata, 1)
        self._tabs.addTab(meta, "Metadata")

        # Layer stack
        self._stack = _tree(("Layer", "Path", "Specifier", "Offset"), self)
        self._stack.itemSelectionChanged.connect(self._on_spec_selected)
        self._spec = _text_pane(self)
        stack = QSplitter(Qt.Orientation.Vertical, self)
        stack.addWidget(self._stack)
        stack.addWidget(self._spec)
        stack.setSizes([260, 400])
        self._tabs.addTab(stack, "Layer Stack")

        # Composition
        comp = QWidget(self)
        self._arcs = _tree(
            ("Arc", "Target Layer", "Target Path", "Introduced In", "Notes"), comp
        )
        self._index_button = QPushButton("Show Prim Index", comp)
        self._index_button.clicked.connect(self._show_prim_index)
        self._index = _text_pane(comp)
        comp_split = QSplitter(Qt.Orientation.Vertical, comp)
        comp_split.addWidget(self._arcs)
        comp_split.addWidget(self._index)
        comp_split.setSizes([260, 400])
        comp_layout = QVBoxLayout(comp)
        comp_layout.setContentsMargins(0, 4, 0, 0)
        comp_layout.addWidget(self._index_button, 0, Qt.AlignmentFlag.AlignLeft)
        comp_layout.addWidget(comp_split, 1)
        self._tabs.addTab(comp, "Composition")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._title)
        layout.addWidget(self._tabs, 1)

        document.changed.connect(self._on_changed)
        document.stageReplaced.connect(lambda: self.set_prim_path(""))
        document.timeChanged.connect(self._on_time)

    # -- public ---------------------------------------------------------------

    @property
    def prim_path(self) -> str:
        """The inspected prim's path, or ``""``."""
        return self._path

    @property
    def properties(self) -> PropertyModel:
        """The Properties tab model, for tests."""
        return self._properties

    def set_prim_path(self, path: str) -> None:
        """Inspect the prim at ``path``; ``""`` clears every tab.

        Args:
            path: Prim path as a string.
        """
        self._path = path
        self._rebuild()

    # -- refresh --------------------------------------------------------------

    def _prim(self) -> Usd.Prim | None:
        if not self._path:
            return None
        prim = self._document.stage.GetPrimAtPath(self._path)
        return prim if prim and prim.IsValid() else None

    def _rebuild(self) -> None:
        self._refresh_pending = False
        prim = self._prim()
        if prim is None:
            self._path = ""
        self._title.setText(self._path or "No prim selected")
        self._properties.set_prim_path(self._path)
        self._fill_metadata(prim)
        self._fill_stack(prim)
        self._fill_composition(prim)

    def _schedule_rebuild(self) -> None:
        # A variant combo or a context-menu action may be mid-signal; rebuild
        # after it returns so no emitting widget is deleted under it.
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._rebuild)

    def _on_changed(self, resynced: list[str], info: list[str]) -> None:
        if not self._path:
            return
        mine = Sdf.Path(self._path)
        for path in resynced:
            changed = Sdf.Path(path)
            if mine.HasPrefix(changed.GetPrimPath()) or changed.GetPrimPath() == mine:
                self._schedule_rebuild()
                return
        if info:
            # Computed rows depend on ancestors, so any info change may matter.
            self._properties.refresh_values()
            self._refresh_details()

    def _on_time(self, _time: float) -> None:
        self._refresh_details()

    # -- properties -------------------------------------------------------------

    def _selected_property(self) -> PropertyRow | None:
        selection = self._table.selectionModel()
        assert selection is not None
        rows = selection.selectedRows()
        return self._properties.row_at(rows[0].row()) if rows else None

    def _on_property_selected(
        self, _sel: QItemSelection, _desel: QItemSelection
    ) -> None:
        self._refresh_details()

    def _refresh_details(self) -> None:
        row = self._selected_property()
        prim = self._prim()
        if row is None or prim is None:
            self._details.clear()
            return
        self._details.setPlainText(property_details(prim, row, self._document.time))

    # -- metadata ---------------------------------------------------------------

    def _fill_metadata(self, prim: Usd.Prim | None) -> None:
        while self._variant_form.rowCount():
            self._variant_form.removeRow(0)
        self._metadata.clear()
        if prim is None:
            self._variants.hide()
            return
        variants = variant_rows(prim)
        self._variants.setVisible(bool(variants))
        for name, choices, selection in variants:
            combo = QComboBox(self._variants)
            combo.addItem("")
            combo.addItems(choices)
            combo.setCurrentText(selection)
            combo.currentTextChanged.connect(self._variant_setter(name))
            self._variant_form.addRow(name, combo)
        items = [QTreeWidgetItem([key, value]) for key, value in metadata_rows(prim)]
        for item in items:
            item.setToolTip(1, item.text(1))
        self._metadata.addTopLevelItems(items)
        self._metadata.resizeColumnToContents(0)

    def _variant_setter(self, variant_set: str) -> Callable[[str], None]:
        path = self._path

        def apply(selection: str) -> None:
            # Defer: the edit resyncs the prim, which rebuilds these combos.
            QTimer.singleShot(
                0, lambda: self._document.set_variant(path, variant_set, selection)
            )

        return apply

    # -- layer stack ------------------------------------------------------------

    def _fill_stack(self, prim: Usd.Prim | None) -> None:
        self._stack.clear()
        self._spec.clear()
        if prim is None:
            return
        for row in prim_stack_rows(prim):
            item = QTreeWidgetItem([row.layer, row.path, row.specifier, row.offset])
            item.setToolTip(0, row.identifier)
            item.setData(0, Qt.ItemDataRole.UserRole, row.identifier)
            self._stack.addTopLevelItem(item)
        self._stack.resizeColumnToContents(0)
        self._stack.resizeColumnToContents(1)

    def _on_spec_selected(self) -> None:
        items = self._stack.selectedItems()
        if not items:
            self._spec.clear()
            return
        item = items[0]
        identifier = item.data(0, Qt.ItemDataRole.UserRole)
        self._spec.setPlainText(spec_text(identifier, item.text(1)))

    # -- composition --------------------------------------------------------------

    def _fill_composition(self, prim: Usd.Prim | None) -> None:
        self._arcs.clear()
        self._index.clear()
        self._index_button.setEnabled(prim is not None)
        if prim is None:
            return
        for arc in composition_rows(prim):
            self._arcs.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        arc.arc,
                        arc.target_layer,
                        arc.target_path,
                        arc.introduced_in,
                        arc.notes,
                    ]
                )
            )
        for column in range(4):
            self._arcs.resizeColumnToContents(column)

    def _show_prim_index(self) -> None:
        prim = self._prim()
        self._index.setPlainText(prim_index_text(prim) if prim is not None else "")
