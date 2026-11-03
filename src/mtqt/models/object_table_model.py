"""Table model wrapping a list of objects with named columns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, is_dataclass

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

type _ModelIndex = QModelIndex | QPersistentModelIndex


class ObjectTableModel(QAbstractTableModel):
    """``QAbstractTableModel`` over objects, one column per field name.

    ``setData`` writes back onto the Python object so the source list stays
    the system of record. Infer columns from dataclass fields when
    ``columns`` is omitted and at least one row is a dataclass.

    Args:
        rows: Initial objects. Copied into an internal list.
        columns: Attribute names used as columns. Required if rows are empty
            and not dataclasses.
        parent: Qt parent ``QObject``.
    """

    def __init__(
        self,
        rows: Sequence[object] | None = None,
        columns: Sequence[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[object] = list(rows) if rows is not None else []
        self._columns: list[str] = list(columns) if columns is not None else []
        if not self._columns:
            self._columns = self._infer_columns()

    def _infer_columns(self) -> list[str]:
        if not self._rows:
            return []
        first = self._rows[0]
        if is_dataclass(first) and not isinstance(first, type):
            return [item.name for item in fields(first)]
        return []

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(
            self._columns
        ):
            return self._columns[section]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(
        self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        obj = self._rows[index.row()]
        name = self._columns[index.column()]
        value = getattr(obj, name)
        if role == Qt.ItemDataRole.DisplayRole:
            return str(value)
        return value

    def setData(
        self,
        index: _ModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        obj = self._rows[index.row()]
        name = self._columns[index.column()]
        current = getattr(obj, name)
        coerced: object = value
        if isinstance(current, bool):
            coerced = str(value).strip().lower() in {"1", "true", "yes"}
        elif isinstance(current, int) and not isinstance(current, bool):
            coerced = int(value)  # type: ignore[arg-type]
        elif isinstance(current, float):
            coerced = float(value)  # type: ignore[arg-type]
        else:
            coerced = str(value)
        setattr(obj, name, coerced)
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def flags(self, index: _ModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def row_at(self, row: int) -> object:
        """Return the Python object at ``row``."""
        return self._rows[row]

    def append(self, row: object) -> None:
        """Append ``row`` and notify attached views."""
        index = len(self._rows)
        self.beginInsertRows(QModelIndex(), index, index)
        self._rows.append(row)
        self.endInsertRows()
        if not self._columns:
            self._columns = self._infer_columns()
            self.headerDataChanged.emit(
                Qt.Orientation.Horizontal,
                0,
                max(len(self._columns) - 1, 0),
            )

    def remove_row(self, row: int) -> None:
        """Remove the object at ``row`` and notify attached views."""
        if not 0 <= row < len(self._rows):
            raise IndexError(row)
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._rows[row]
        self.endRemoveRows()
