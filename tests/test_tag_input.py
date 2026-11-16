"""TagInput: silent normalization, commit, duplicate, and backspace remove."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton

from mtqt import TagInput


def _tags(qtbot) -> TagInput:
    widget = TagInput()
    qtbot.addWidget(widget)
    return widget


def test_set_tags_normalizes_without_emitting(qtbot) -> None:
    widget = _tags(qtbot)
    assert widget.objectName() == "TagInput"
    assert widget.accessibleName() == "Tags"
    emitted: list[object] = []
    widget.tagsChanged.connect(emitted.append)
    widget.set_tags(["a", "a", " b "])
    assert widget.tags == ("a", "b")
    assert emitted == []


def test_return_pressed_commits_the_draft(qtbot) -> None:
    widget = _tags(qtbot)
    widget._edit.setText("hero")
    with qtbot.waitSignal(widget.tagsChanged) as blocker:
        widget._edit.returnPressed.emit()
    assert blocker.args == [("hero",)]
    assert widget.tags == ("hero",)
    assert widget._edit.text() == ""


def test_duplicate_commit_does_not_emit(qtbot) -> None:
    widget = _tags(qtbot)
    widget.set_tags(["hero"])
    widget._edit.setText("hero")
    emitted: list[object] = []
    widget.tagsChanged.connect(emitted.append)
    widget._edit.returnPressed.emit()
    assert widget.tags == ("hero",)
    assert widget._edit.text() == ""
    assert emitted == []


def test_blank_commit_does_not_emit(qtbot) -> None:
    widget = _tags(qtbot)
    widget._edit.setText("   ")
    emitted: list[object] = []
    widget.tagsChanged.connect(emitted.append)
    widget._edit.returnPressed.emit()
    assert widget.tags == ()
    assert emitted == []


def test_comma_commits_the_draft(qtbot) -> None:
    widget = _tags(qtbot)
    with qtbot.waitSignal(widget.tagsChanged) as blocker:
        widget._edit.setText("hero,prop,")
    assert blocker.args == [("hero", "prop")]
    assert widget.tags == ("hero", "prop")
    assert widget._edit.text() == ""


def test_backspace_on_an_empty_draft_removes_the_last_tag(qtbot) -> None:
    widget = _tags(qtbot)
    widget.set_tags(["a", "b"])
    widget.show()
    widget._edit.clear()
    with qtbot.waitSignal(widget.tagsChanged) as blocker:
        qtbot.keyClick(widget._edit, Qt.Key.Key_Backspace)
    assert blocker.args == [("a",)]
    assert widget.tags == ("a",)


def test_remove_button_drops_that_tag_after_the_click(qtbot) -> None:
    widget = _tags(qtbot)
    widget.set_tags(["a", "b"])
    button = widget.findChildren(QToolButton)[0]
    with qtbot.waitSignal(widget.tagsChanged) as blocker:
        button.click()
    assert blocker.args == [("b",)]
    assert widget.tags == ("b",)


def test_suggestions_feed_the_completer(qtbot) -> None:
    widget = _tags(qtbot)
    widget.set_suggestions(["hero", "prop"])
    completer = widget._edit.completer()
    assert completer is not None
    completer.setCompletionPrefix("he")
    assert completer.currentCompletion() == "hero"
