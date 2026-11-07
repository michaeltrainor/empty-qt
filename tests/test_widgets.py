from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QDragMoveEvent, QDropEvent, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QTreeView,
    QWidget,
)

from mtqt import SceneGraphModel
from mtqt.widgets import LabeledLineEdit, PathPicker, SceneGraphTree


def test_labeled_line_edit_required(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    field = LabeledLineEdit("Name", host)
    qtbot.addWidget(field)
    assert field.objectName() == "LabeledLineEdit"
    assert field.accessibleName() == "Name"
    assert not field.is_valid
    with qtbot.waitSignal(field.validityChanged) as blocker:
        field.text = "Ada"
    assert blocker.args == [True]
    assert field.is_valid
    assert field.text == "Ada"
    with qtbot.waitSignal(field.validityChanged) as blocker:
        field.text = ""
    assert blocker.args == [False]
    assert not field.is_valid
    assert field.text == ""


def test_labeled_line_edit_optional_placeholder_and_text_changed(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    field = LabeledLineEdit(
        "Name",
        host,
        placeholder="Enter a name",
        required=False,
    )
    qtbot.addWidget(field)
    assert field.is_valid
    assert field._edit.placeholderText() == "Enter a name"

    with qtbot.waitSignal(field.textChanged) as blocker:
        field.text = "Ada"
    assert blocker.args == ["Ada"]
    assert field.is_valid

    field.text = ""
    assert field.is_valid
    assert field.text == ""


def test_path_picker_path_property(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    picker = PathPicker(host)
    qtbot.addWidget(picker)
    assert picker.path is None
    picker.path = Path("/tmp/example.txt")
    assert picker.path == Path("/tmp/example.txt")


def test_path_picker_browse_mocked(qtbot, monkeypatch) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    picker = PathPicker(host)
    qtbot.addWidget(picker)

    def fake_open(_parent, _caption, _start, _filt):
        return ("/tmp/picked.txt", "All files (*)")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    picker._on_browse()
    assert picker.path == Path("/tmp/picked.txt")


def test_path_picker_path_changed_emits_string_and_clears(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    picker = PathPicker(host)
    qtbot.addWidget(picker)
    assert picker.objectName() == "PathPicker"

    chosen = Path("/tmp/example.txt")
    with qtbot.waitSignal(picker.pathChanged) as blocker:
        picker.path = chosen
    assert blocker.args == [str(chosen)]
    assert picker.path == chosen

    with qtbot.waitSignal(picker.pathChanged) as blocker:
        picker.path = ""
    assert blocker.args == [""]
    assert picker.path is None
    assert picker._edit.text() == ""

    picker.path = chosen
    with qtbot.waitSignal(picker.pathChanged) as blocker:
        picker.path = None
    assert blocker.args == [""]
    assert picker.path is None
    assert picker._edit.text() == ""


def test_path_picker_edit_finished_updates_path(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    picker = PathPicker(host)
    qtbot.addWidget(picker)
    picker._edit.setText("  /tmp/typed.txt  ")
    with qtbot.waitSignal(picker.pathChanged) as blocker:
        picker._on_edit_finished()
    assert picker.path == Path("/tmp/typed.txt")
    assert blocker.args == [str(Path("/tmp/typed.txt"))]


def test_path_picker_directory_browse_mocked(qtbot, monkeypatch) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    picker = PathPicker(host, directory_mode=True)
    qtbot.addWidget(picker)

    def fake_directory(_parent, _caption, _start):
        return "/tmp/picked-dir"

    def fail_open(*_args, **_kwargs):
        raise AssertionError("file dialog should not be used")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_directory)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", fail_open)
    picker._on_browse()
    assert picker.path == Path("/tmp/picked-dir")


def test_path_picker_cancelled_browse_keeps_path(qtbot, monkeypatch) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    picker = PathPicker(host)
    qtbot.addWidget(picker)
    picker.path = Path("/tmp/keep.txt")

    def fake_open(_parent, _caption, _start, _filt):
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    picker._on_browse()
    assert picker.path == Path("/tmp/keep.txt")

    directory = PathPicker(host, directory_mode=True)
    qtbot.addWidget(directory)
    directory.path = Path("/tmp/keep-dir")

    def fake_directory(_parent, _caption, _start):
        return ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_directory)
    directory._on_browse()
    assert directory.path == Path("/tmp/keep-dir")


def test_scene_graph_tree_selection(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    tree = SceneGraphTree([["world", ["geo"]]], host)
    qtbot.addWidget(tree)
    assert tree.objectName() == "SceneGraphTree"
    world = tree.model.index(0, 0)
    geo = tree.model.index(0, 0, world)
    with qtbot.waitSignal(tree.currentNameChanged) as blocker:
        tree.view.setCurrentIndex(geo)
    assert blocker.args == ["geo"]
    assert tree.current_name == "geo"


def test_scene_graph_tree_multi_selection(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    tree = SceneGraphTree([["world", ["geo"], ["lgt"]]], host)
    qtbot.addWidget(tree)
    assert tree.model.index_for_name("geo").isValid()
    assert not tree.model.index_for_name("missing").isValid()

    with qtbot.waitSignal(tree.selectedNamesChanged) as blocker:
        tree.set_selected_names(["geo", "lgt"])
    assert blocker.args == [["geo", "lgt"]]
    assert tree.selected_names == ["geo", "lgt"]
    assert tree.current_name == "lgt"

    with qtbot.waitSignal(tree.selectedNamesChanged) as blocker:
        tree.set_selected_names([])
    assert blocker.args == [[]]
    assert tree.selected_names == []
    assert tree.current_name == ""


def test_scene_graph_tree_load_json(qtbot, tmp_path) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    path = tmp_path / "scene.json"
    path.write_text('[["root", ["leaf"]]]', encoding="utf-8")
    tree = SceneGraphTree(parent=host, path=path)
    qtbot.addWidget(tree)
    assert tree.model.rowCount() == 1
    assert tree.model.data(tree.model.index(0, 0)) == "root"
    other = tmp_path / "other.json"
    other.write_text('[["camera"]]', encoding="utf-8")
    tree.load_json(other)
    assert tree.model.data(tree.model.index(0, 0)) == "camera"


def test_scene_graph_tree_empty_selection(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    tree = SceneGraphTree([["world", ["geo"]]], host)
    qtbot.addWidget(tree)
    assert tree.objectName() == "SceneGraphTree"
    assert tree.current_name == ""
    assert isinstance(tree.model, SceneGraphModel)
    assert isinstance(tree.view, QTreeView)
    assert tree.view.model() is tree.model


def _claims_key(widget: QWidget, key: Qt.Key, modifiers: Qt.KeyboardModifier) -> bool:
    # Qt sends ShortcutOverride, ignored, before matching window shortcuts.
    event = QKeyEvent(QEvent.Type.ShortcutOverride, key, modifiers)
    event.ignore()
    QApplication.sendEvent(widget, event)
    return event.isAccepted()


@pytest.mark.parametrize(
    ("key", "modifiers", "claimed"),
    [
        (Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier, True),
        (Qt.Key.Key_End, Qt.KeyboardModifier.ShiftModifier, True),
        (Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier, True),
        (Qt.Key.Key_Home, Qt.KeyboardModifier.KeypadModifier, True),
        (Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier, False),
        (Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, False),
    ],
)
def test_scene_graph_tree_claims_navigation_keys(
    qtbot, key: Qt.Key, modifiers: Qt.KeyboardModifier, claimed: bool
) -> None:
    """Row navigation keys beat host window shortcuts; other keys don't."""
    tree = SceneGraphTree([["root", ["child"]]])
    qtbot.addWidget(tree)
    assert _claims_key(tree.view, key, modifiers) is claimed


DRAG_FOREST = [["world", ["geo", ["a"], ["b"]], ["lgt", ["key"]]]]


def test_scene_graph_tree_drag_drop_toggle(qtbot) -> None:
    """Drag and drop is off by default and switches the view's mode."""
    plain = SceneGraphTree(DRAG_FOREST)
    qtbot.addWidget(plain)
    assert not plain.drag_drop_enabled
    assert plain.view.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
    assert not plain.view.acceptDrops()

    tree = SceneGraphTree(DRAG_FOREST, drag_drop=True)
    qtbot.addWidget(tree)
    assert tree.drag_drop_enabled
    assert tree.view.dragDropMode() == QAbstractItemView.DragDropMode.DragDrop
    assert tree.view.acceptDrops()
    assert tree.view.dragEnabled()
    assert tree.view.showDropIndicator()
    assert tree.view.defaultDropAction() == Qt.DropAction.MoveAction

    tree.set_drag_drop_enabled(False)
    assert not tree.drag_drop_enabled
    assert tree.view.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop


def _drop(tree: SceneGraphTree, names: list[str], pos: QPoint) -> bool:
    """Drag ``names`` over ``pos`` in the view and drop them there."""
    view = tree.view
    data = tree.model.mimeData([tree.model.index_for_name(n) for n in names])
    actions = Qt.DropAction.MoveAction
    buttons = Qt.MouseButton.LeftButton
    modifiers = Qt.KeyboardModifier.NoModifier
    move = QDragMoveEvent(pos, actions, data, buttons, modifiers)
    view.dragMoveEvent(move)
    if not move.isAccepted():
        return False
    drop = QDropEvent(QPointF(pos), actions, data, buttons, modifiers)
    view.dropEvent(drop)
    return drop.isAccepted()


def test_scene_graph_tree_drop_reparents_and_reorders(qtbot) -> None:
    """Dropping on a row appends under it; above or below a row reorders."""
    tree = SceneGraphTree(DRAG_FOREST, drag_drop=True)
    qtbot.addWidget(tree)
    tree.resize(320, 400)
    tree.show()
    qtbot.waitExposed(tree)
    moves: list[tuple[list[str], str, int]] = []
    tree.nodesMoved.connect(
        lambda names, parent, row: moves.append((names, parent, row))
    )
    view = tree.view
    model = tree.model

    # Onto a collapsed leaf: it becomes a parent and is expanded.
    key = model.index_for_name("key")
    assert _drop(tree, ["a"], view.visualRect(key).center())
    assert model.to_lists() == [["world", ["geo", ["b"]], ["lgt", ["key", ["a"]]]]]
    assert moves[-1] == (["a"], "key", 0)
    assert view.isExpanded(model.index_for_name("key"))

    # Above a row inserts before it, in that row's parent.
    b = model.index_for_name("b")
    rect = view.visualRect(b)
    assert _drop(tree, ["key"], QPoint(rect.center().x(), rect.top()))
    assert model.to_lists() == [["world", ["geo", ["key", ["a"]], ["b"]], ["lgt"]]]
    assert moves[-1] == (["key"], "geo", 0)

    # Below a row inserts after it.
    rect = view.visualRect(model.index_for_name("b"))
    assert _drop(tree, ["a"], QPoint(rect.center().x(), rect.bottom()))
    assert model.to_lists() == [["world", ["geo", ["key"], ["b"], ["a"]], ["lgt"]]]
    assert moves[-1] == (["a"], "geo", 2)

    # Into its own descendant is refused and nothing changes.
    count = len(moves)
    assert not _drop(tree, ["geo"], view.visualRect(model.index_for_name("b")).center())
    assert len(moves) == count
    assert model.to_lists() == [["world", ["geo", ["key"], ["b"], ["a"]], ["lgt"]]]


def test_scene_graph_tree_move_filter_blocks_drop(qtbot) -> None:
    """A host filter rejects the hover, so the drop never happens."""
    tree = SceneGraphTree(DRAG_FOREST, drag_drop=True)
    qtbot.addWidget(tree)
    tree.resize(320, 400)
    tree.show()
    qtbot.waitExposed(tree)
    seen: list[tuple[list[str], str, int]] = []

    def accept(names: list[str], parent: str, row: int) -> bool:
        seen.append((names, parent, row))
        return parent == "lgt"

    tree.set_move_filter(accept)
    geo = tree.model.index_for_name("geo")
    assert not _drop(tree, ["key"], tree.view.visualRect(geo).center())
    assert seen[-1] == (["key"], "geo", 2)
    lgt = tree.model.index_for_name("lgt")
    assert _drop(tree, ["a"], tree.view.visualRect(lgt).center())
    assert tree.model.to_lists() == [["world", ["geo", ["b"]], ["lgt", ["key"], ["a"]]]]
