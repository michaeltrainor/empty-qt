from __future__ import annotations

from dataclasses import dataclass

import pytest
from PySide6.QtCore import Qt

from mtqt.models import ObjectListModel, ObjectTableModel, SceneGraphModel


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


FOREST = [
    [
        "world",
        ["persp"],
        ["geo", ["cube"], ["sphere"]],
    ]
]


@pytest.mark.parametrize(
    "items",
    [[], [Item("a", 1), Item("b", 2)]],
    ids=["empty", "populated"],
)
def test_list_model_contract(qtmodeltester, items: list[Item]) -> None:
    qtmodeltester.check(ObjectListModel(items, display_attr="name"))


@pytest.mark.parametrize(
    "items",
    [[], [Item("a", 1), Item("b", 2)]],
    ids=["empty", "populated"],
)
def test_table_model_contract(qtmodeltester, items: list[Item]) -> None:
    qtmodeltester.check(ObjectTableModel(items, columns=("name", "count")))


@pytest.mark.parametrize("forest", [[], FOREST], ids=["empty", "populated"])
def test_scene_graph_model_contract(qtmodeltester, forest: list) -> None:
    qtmodeltester.check(SceneGraphModel(forest))


def test_scene_graph_parent_child_and_user_role() -> None:
    model = SceneGraphModel(FOREST)
    assert model.columnCount() == 1
    assert model.rowCount() == 1
    world = model.index(0, 0)
    assert model.data(world, Qt.ItemDataRole.DisplayRole) == "world"
    assert model.data(world, Qt.ItemDataRole.UserRole).name == "world"
    assert model.rowCount(world) == 2
    assert model.hasChildren(world)
    geo = model.index(1, 0, world)
    assert model.node_at(geo).name == "geo"
    assert model.parent(geo) == world
    cube = model.index(0, 0, geo)
    assert model.path_at(cube) == "/world/geo/cube"
    assert not model.parent(world).isValid()


def test_scene_graph_append_and_remove() -> None:
    model = SceneGraphModel(FOREST)
    world = model.index(0, 0)
    added = model.append_child(world, "lgt")
    assert model.rowCount(world) == 3
    assert model.data(added, Qt.ItemDataRole.DisplayRole) == "lgt"
    model.remove_row(added)
    assert model.rowCount(world) == 2
    assert model.to_lists() == FOREST


def test_scene_graph_from_json_roundtrip(tmp_path) -> None:
    path = tmp_path / "scene.json"
    path.write_text('[["world", ["geo"]]]', encoding="utf-8")
    model = SceneGraphModel.from_json(path)
    assert model.to_lists() == [["world", ["geo"]]]
    model.set_nodes([["root", ["child"]]])
    assert model.path_at(model.index(0, 0, model.index(0, 0))) == "/root/child"


def test_scene_graph_rejects_string_children() -> None:
    with pytest.raises(TypeError, match="not strings"):
        SceneGraphModel([["world", "geo"]])
    with pytest.raises(TypeError, match="forest"):
        SceneGraphModel(["world", ["geo"]])
