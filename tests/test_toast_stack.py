"""ToastStack: ids, one dismiss signal, and the level property."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QToolButton

from mtqt import ToastLevel, ToastStack
from mtqt.widgets.toast_stack import Toast


def _stack(qtbot) -> ToastStack:
    stack = ToastStack()
    qtbot.addWidget(stack)
    return stack


def test_show_toast_without_timeout_keeps_the_id(qtbot) -> None:
    stack = _stack(qtbot)
    assert stack.objectName() == "ToastStack"
    toast_id = stack.show_toast("Saved", timeout_ms=0)
    assert stack.toasts == (toast_id,)
    newer = stack.show_toast("Saved again", timeout_ms=0)
    assert stack.toasts == (newer, toast_id)


def test_dismiss_emits_once_and_drops_the_id(qtbot) -> None:
    stack = _stack(qtbot)
    toast_id = stack.show_toast("Saved", timeout_ms=0)
    with qtbot.waitSignal(stack.toastDismissed) as blocker:
        stack.dismiss(toast_id)
    assert blocker.args == [toast_id]
    assert stack.toasts == ()
    emitted: list[str] = []
    stack.toastDismissed.connect(emitted.append)
    stack.dismiss(toast_id)
    assert emitted == []


def test_dismiss_of_an_unknown_id_does_not_emit(qtbot) -> None:
    stack = _stack(qtbot)
    emitted: list[str] = []
    stack.toastDismissed.connect(emitted.append)
    stack.dismiss("missing")
    assert emitted == []
    assert stack.toasts == ()


def test_level_is_stored_on_the_row(qtbot) -> None:
    stack = _stack(qtbot)
    stack.show_toast("Could not save", level=ToastLevel.ERROR, timeout_ms=0)
    row = stack.findChild(QFrame, "Toast")
    assert row is not None
    assert row.property("level") == "error"
    stack.show_toast("Careful", level=ToastLevel.WARNING, timeout_ms=0)
    levels = {item.property("level") for item in stack.findChildren(Toast)}
    assert levels == {"error", "warning"}


def test_close_button_dismisses_without_destroying_mid_click(qtbot) -> None:
    stack = _stack(qtbot)
    toast_id = stack.show_toast("Saved", timeout_ms=0)
    button = stack.findChild(QToolButton)
    assert button is not None
    with qtbot.waitSignal(stack.toastDismissed) as blocker:
        button.click()
    assert blocker.args == [toast_id]
    assert stack.toasts == ()


def test_dismiss_before_timeout_drops_the_id(qtbot) -> None:
    stack = _stack(qtbot)
    toast_id = stack.show_toast("Saved", timeout_ms=5000)
    with qtbot.waitSignal(stack.toastDismissed) as blocker:
        stack.dismiss(toast_id)
    assert blocker.args == [toast_id]
    assert stack.toasts == ()
    stack.dismiss(toast_id)


def test_timeout_dismisses_the_toast(qtbot) -> None:
    stack = _stack(qtbot)
    with qtbot.waitSignal(stack.toastDismissed, timeout=1000) as blocker:
        toast_id = stack.show_toast("Saved", timeout_ms=30)
    assert blocker.args == [toast_id]
    assert stack.toasts == ()
