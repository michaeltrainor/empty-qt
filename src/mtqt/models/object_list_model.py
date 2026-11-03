"""List model wrapping a Python list of objects."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

type _ModelIndex = QModelIndex | QPersistentModelIndex


class ObjectListModel(QAbstractListModel):
    """``QAbstractListModel`` over a list of arbitrary Python objects.

    The model owns the list reference you pass in. Insert and remove through
    :meth:`append` / :meth:`remove_row` so views receive ``beginInsertRows``
    (and friends). The model does not own any view.

    Args:
        rows: Initial objects. Copied into an internal list.
        parent: Qt parent ``QObject``.
        display_attr: Attribute used for ``DisplayRole``. When omitted,
            ``str(row)`` is used.
    """

    def __init__(
        self,
        rows: Sequence[object] | None = None,
        parent: QObject | None = None,
        *,
        display_attr: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[object] = list(rows) if rows is not None else []
        self._display_attr = display_attr

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(
        self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if self._display_attr:
                return getattr(row, self._display_attr)
            return str(row)
        if role == Qt.ItemDataRole.UserRole:
            return row
        return None

    def flags(self, index: _ModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def row_at(self, row: int) -> object:
        """Return the Python object at ``row``."""
        return self._rows[row]

    def append(self, row: object) -> None:
        """Append ``row`` and notify attached views."""
        index = len(self._rows)
        self.beginInsertRows(QModelIndex(), index, index)
        self._rows.append(row)
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        """Remove the object at ``row`` and notify attached views."""
        if not 0 <= row < len(self._rows):
            raise IndexError(row)
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._rows[row]
        self.endRemoveRows()

    def set_rows(self, rows: Sequence[object]) -> None:
        """Replace all rows."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()
