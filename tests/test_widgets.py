from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget

from mtqt.widgets import LabeledLineEdit, PathPicker


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
