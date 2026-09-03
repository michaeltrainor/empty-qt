from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget

from mtqt.widgets import LabeledLineEdit, PathPicker, SceneGraphTree


def test_labeled_line_edit_required(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    field = LabeledLineEdit("Name", host)
    qtbot.addWidget(field)
    assert field.objectName() == "LabeledLineEdit"
    assert not field.is_valid
    with qtbot.waitSignal(field.validityChanged):
        field.text = "Ada"
    assert field.is_valid
    assert field.text == "Ada"


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
