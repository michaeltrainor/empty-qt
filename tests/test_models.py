from __future__ import annotations

from dataclasses import dataclass

import pytest
from PySide6.QtCore import QByteArray, QMimeData, QModelIndex, QPersistentModelIndex, Qt

from mtqt.models import (
    SCENE_GRAPH_MIME_TYPE,
    ObjectListModel,
    ObjectTableModel,
    SceneGraphModel,
)


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


MOVE_FOREST = [["world", ["geo", ["a"], ["b"], ["c"]], ["lgt", ["key"]]]]


def test_scene_graph_move_reparents_and_reorders(qtmodeltester) -> None:
    model = SceneGraphModel(MOVE_FOREST)
    qtmodeltester.check(model)
    moves: list[tuple[list[str], str, int]] = []
    model.nodesMoved.connect(
        lambda names, parent, row: moves.append((names, parent, row))
    )
    a = QPersistentModelIndex(model.index_for_name("a"))

    assert model.move_nodes(["a"], "lgt")
    assert model.to_lists() == [
        ["world", ["geo", ["b"], ["c"]], ["lgt", ["key"], ["a"]]]
    ]
    assert moves[-1] == (["a"], "lgt", 1)
    assert a.isValid()
    assert model.node_at(a).name == "a"
    assert model.path_at(a) == "/world/lgt/a"

    # Reorder within one parent, forwards then backwards.
    assert model.move_nodes(["key"], "lgt", 2)
    assert model.to_lists()[0][2] == ["lgt", ["a"], ["key"]]
    assert moves[-1] == (["key"], "lgt", 1)
    assert model.move_nodes(["key"], "lgt", 0)
    assert model.to_lists()[0][2] == ["lgt", ["key"], ["a"]]
    assert moves[-1] == (["key"], "lgt", 0)

    # Already in place: nothing moves and nothing is emitted.
    count = len(moves)
    assert not model.move_nodes(["key"], "lgt", 0)
    assert not model.move_nodes(["key"], "lgt", 1)
    assert not model.move_nodes(["a"], "lgt", -1)
    assert len(moves) == count


def test_scene_graph_move_many_keeps_tree_order() -> None:
    model = SceneGraphModel([["r", ["a"], ["b"], ["c"], ["d"]], ["s", ["x"]]])
    moves: list[tuple[list[str], str, int]] = []
    model.nodesMoved.connect(
        lambda names, parent, row: moves.append((names, parent, row))
    )

    # Names arrive in selection order; the move keeps tree order.
    assert model.move_nodes(["c", "a"], "r", 4)
    assert model.to_lists()[0] == ["r", ["b"], ["d"], ["a"], ["c"]]
    assert moves[-1] == (["a", "c"], "r", 2)

    assert model.move_nodes(["x", "d"], "r", 0)
    assert model.to_lists() == [["r", ["d"], ["x"], ["b"], ["a"], ["c"]], ["s"]]
    assert moves[-1] == (["d", "x"], "r", 0)

    # A node and its descendant: only the ancestor moves, to the root.
    assert model.move_nodes(["b", "r"], "", 2)
    assert model.to_lists() == [["s"], ["r", ["d"], ["x"], ["b"], ["a"], ["c"]]]
    assert moves[-1] == (["r"], "", 1)


def test_scene_graph_move_rejects_invalid_targets() -> None:
    model = SceneGraphModel(MOVE_FOREST)
    assert not model.can_move_nodes(["geo"], "a")  # into own descendant
    assert not model.can_move_nodes(["geo"], "geo")  # onto itself
    assert not model.can_move_nodes(["missing"], "lgt")
    assert not model.can_move_nodes(["a"], "missing")
    assert not model.can_move_nodes([], "lgt")
    assert not model.move_nodes(["geo"], "a")
    assert model.to_lists() == MOVE_FOREST


def test_scene_graph_move_filter_vetoes() -> None:
    model = SceneGraphModel(MOVE_FOREST)
    calls: list[tuple[list[str], str, int]] = []

    def accept(names: list[str], parent: str, row: int) -> bool:
        calls.append((names, parent, row))
        return parent != "lgt"

    model.set_move_filter(accept)
    # The filter sees top-most names only and a concrete row for appends.
    assert not model.can_move_nodes(["a", "geo"], "lgt")
    assert calls[-1] == (["geo"], "lgt", 1)
    assert not model.move_nodes(["a"], "lgt")
    assert model.to_lists() == MOVE_FOREST
    assert model.move_nodes(["key"], "geo", 0)
    assert calls[-1] == (["key"], "geo", 0)
    model.set_move_filter(None)
    assert model.can_move_nodes(["a"], "lgt")


def test_scene_graph_mime_drop_protocol() -> None:
    model = SceneGraphModel(MOVE_FOREST)
    assert model.supportedDropActions() == Qt.DropAction.MoveAction
    assert model.mimeTypes() == [SCENE_GRAPH_MIME_TYPE]
    a = model.index_for_name("a")
    lgt = model.index_for_name("lgt")
    assert model.flags(a) & Qt.ItemFlag.ItemIsDragEnabled
    assert model.flags(lgt) & Qt.ItemFlag.ItemIsDropEnabled
    assert model.flags(QModelIndex()) == Qt.ItemFlag.ItemIsDropEnabled

    data = model.mimeData([a, a])
    assert data.hasFormat(SCENE_GRAPH_MIME_TYPE)
    move = Qt.DropAction.MoveAction
    assert model.canDropMimeData(data, move, -1, 0, lgt)
    assert not model.canDropMimeData(data, Qt.DropAction.CopyAction, -1, 0, lgt)
    assert not model.canDropMimeData(data, move, -1, 0, a)
    assert model.dropMimeData(data, move, 0, 0, lgt)
    assert model.to_lists()[0][2] == ["lgt", ["a"], ["key"]]
    # The view asks to remove the dragged rows after a move; refuse.
    assert not model.removeRows(0, 1, lgt)
    assert model.rowCount(lgt) == 2

    # Foreign payloads and another model's drags are refused.
    assert not model.canDropMimeData(QMimeData(), move, -1, 0, lgt)
    assert not model.canDropMimeData(None, move, -1, 0, lgt)
    other = SceneGraphModel(MOVE_FOREST)
    foreign = other.mimeData([other.index_for_name("a")])
    assert not model.canDropMimeData(foreign, move, -1, 0, lgt)
    garbage = QMimeData()
    garbage.setData(SCENE_GRAPH_MIME_TYPE, QByteArray(b"not json"))
    assert not model.canDropMimeData(garbage, move, -1, 0, lgt)
