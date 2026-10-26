from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QTreeView, QWidget

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
