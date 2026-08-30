from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from mtqt.style import apply_stylesheet, load_stylesheet


def test_load_stylesheet_contains_tokens() -> None:
    sheet = load_stylesheet("base", "widgets")
    assert "QMainWindow" in sheet
    assert "PathPicker" in sheet
    assert "[invalid=" in sheet


def test_load_stylesheet_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown stylesheet"):
        load_stylesheet("nope")


def test_load_stylesheet_requires_names() -> None:
    with pytest.raises(ValueError, match="At least one"):
        load_stylesheet()


def test_apply_stylesheet_on_widget(qtbot) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    apply_stylesheet(widget, "base")
    assert "QWidget" in widget.styleSheet()
