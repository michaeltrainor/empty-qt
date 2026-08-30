from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt

from mtqt.models import ObjectListModel, ObjectTableModel


@dataclass
class Item:
    name: str
    count: int


def test_list_model_append_and_user_role() -> None:
    model = ObjectListModel([Item("a", 1)], display_attr="name")
    assert model.rowCount() == 1
    model.append(Item("b", 2))
    assert model.rowCount() == 2
    index = model.index(1, 0)
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "b"
    assert model.data(index, Qt.ItemDataRole.UserRole).name == "b"


def test_list_model_remove_row() -> None:
    model = ObjectListModel([Item("a", 1), Item("b", 2)], display_attr="name")
    model.remove_row(0)
    assert model.rowCount() == 1
    assert model.row_at(0).name == "b"


def test_table_model_set_data_updates_object() -> None:
    item = Item("a", 1)
    model = ObjectTableModel([item], columns=("name", "count"))
    index = model.index(0, 1)
    assert model.setData(index, "7", Qt.ItemDataRole.EditRole)
    assert item.count == 7
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "7"
