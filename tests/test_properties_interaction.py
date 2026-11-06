"""Tests for display units, sliders, and label dragging in the properties panel."""

from __future__ import annotations

import logging
import math

import pytest
from pydantic import BaseModel, Field
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QSpinBox

from mtqt import PropertiesBook, PropertiesPanel
from mtqt.widgets.properties_panel import _NumberEditor, _ScrubLabel, _VecEditor


class _Units(BaseModel):
    rotation: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        json_schema_extra={"mtqt_display": "degrees"},
    )
    angle: float = Field(
        default=0.0,
        ge=0.0,
        le=math.pi,
        json_schema_extra={"mtqt_display": "degrees"},
    )
    mix: float = Field(default=0.45, json_schema_extra={"mtqt_display": "percent"})
    length: float = Field(default=2.0, json_schema_extra={"mtqt_suffix": " m"})
    copies: int = Field(default=3, json_schema_extra={"mtqt_suffix": " x"})


class _Numbers(BaseModel):
    count: int = Field(default=5, ge=0, le=10)
    weight: float = Field(default=1.0, ge=0.0, le=5.0)
    free: float = 0.0
    plain: float = Field(
        default=1.0, ge=0.0, le=5.0, json_schema_extra={"mtqt_slider": False}
    )
    distance: float = Field(
        default=10.0,
        ge=0.0,
        json_schema_extra={"mtqt_soft_range": (0.0, 50.0)},
    )
    locked: float = Field(default=1.0, ge=0.0, le=5.0, frozen=True)


def _editor(panel: PropertiesPanel, name: str) -> _NumberEditor:
    editor = panel._editors[name]
    assert isinstance(editor, _NumberEditor)
    return editor


def _row_label(panel: PropertiesPanel, name: str) -> _ScrubLabel:
    label = panel._form.labelForField(panel._editors[name])
    assert isinstance(label, _ScrubLabel)
    return label


def _mouse(
    widget: _ScrubLabel,
    kind: QEvent.Type,
    x: float,
    *,
    buttons: Qt.MouseButton = Qt.MouseButton.LeftButton,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    # Sent directly: qtbot.mouseMove does not carry button state reliably
    # under the offscreen platform.
    pos = QPointF(x, 5.0)
    event = QMouseEvent(
        kind,
        pos,
        widget.mapToGlobal(pos),
        Qt.MouseButton.LeftButton
        if kind != QEvent.Type.MouseMove
        else Qt.MouseButton.NoButton,
        buttons,
        modifiers,
    )
    QApplication.sendEvent(widget, event)


def _drag(
    label: _ScrubLabel,
    xs: list[float],
    *,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    release: bool = True,
) -> None:
    _mouse(label, QEvent.Type.MouseButtonPress, 0.0)
    for x in xs:
        _mouse(label, QEvent.Type.MouseMove, x, modifiers=modifiers)
    if release:
        _mouse(
            label,
            QEvent.Type.MouseButtonRelease,
            xs[-1] if xs else 0.0,
            buttons=Qt.MouseButton.NoButton,
        )


def _record(panel: PropertiesPanel | PropertiesBook) -> dict[str, list]:
    events: dict[str, list] = {"preview": [], "changed": []}
    panel.fieldPreviewed.connect(
        lambda field, value, _model: events["preview"].append((field, value))
    )
    panel.fieldChanged.connect(
        lambda field, value, _model: events["changed"].append((field, value))
    )
    return events


# —— Display units ——


def test_degrees_vector_shows_degrees_and_commits_radians(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    stored_y = 0.123456789
    panel.set_model(_Units(rotation=(math.pi / 2, stored_y, 0.0)))
    vec = panel._editors["rotation"]
    assert isinstance(vec, _VecEditor)
    assert vec._spins[0].value() == pytest.approx(90.0)
    assert vec._spins[0].suffix() == ""
    label = panel._form.labelForField(vec)
    assert label is not None
    assert label.text().endswith("(°)")

    with qtbot.waitSignal(panel.fieldChanged) as blocker:
        vec._spins[0].setValue(45.0)
    committed = blocker.args[1]
    assert committed[0] == pytest.approx(math.pi / 4)
    # Untouched components keep the stored radians exactly, not a
    # value rounded through 3-decimal degrees.
    assert committed[1] == stored_y
    assert committed[2] == 0.0


def test_degrees_scalar_scales_bounds(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Units(angle=math.pi / 6))
    editor = _editor(panel, "angle")
    assert editor.spin.maximum() == pytest.approx(180.0)
    assert editor.spin.value() == pytest.approx(30.0)
    with qtbot.waitSignal(panel.fieldChanged) as blocker:
        editor.spin.setValue(60.0)
    assert blocker.args[1] == pytest.approx(math.pi / 3)


def test_percent_and_suffix_display(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Units())
    mix = _editor(panel, "mix")
    assert mix.spin.value() == pytest.approx(45.0)
    assert mix.spin.suffix() == "%"
    with qtbot.waitSignal(panel.fieldChanged) as blocker:
        mix.spin.setValue(50.0)
    assert blocker.args[1] == pytest.approx(0.5)

    length = _editor(panel, "length")
    assert length.spin.suffix() == " m"
    assert length.spin.value() == pytest.approx(2.0)

    copies = _editor(panel, "copies")
    assert isinstance(copies.spin, QSpinBox)
    assert copies.spin.suffix() == " x"
    with qtbot.waitSignal(panel.fieldChanged) as blocker:
        copies.spin.setValue(4)
    assert blocker.args[1] == 4


def test_unknown_display_preset_warns(qtbot, caplog) -> None:
    class _Bad(BaseModel):
        value: float = Field(
            default=1.0, json_schema_extra={"mtqt_display": "furlongs"}
        )

    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    with caplog.at_level(logging.WARNING, logger="mtqt.widgets.properties_panel"):
        panel.set_model(_Bad())
    assert "furlongs" in caplog.text
    assert _editor(panel, "value").spin.value() == pytest.approx(1.0)


# —— Sliders ——


def test_slider_only_on_bounded_fields(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    assert _editor(panel, "weight").slider is not None
    assert _editor(panel, "count").slider is not None
    assert _editor(panel, "free").slider is None
    assert _editor(panel, "plain").slider is None
    locked = _editor(panel, "locked")
    assert locked.slider is not None
    assert not locked.slider.isEnabled()


def test_soft_range_slider_allows_typing_past_it(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    distance = _editor(panel, "distance")
    assert distance.slider is not None
    assert distance.slider.value() == distance.slider.maximum() // 5
    with qtbot.waitSignal(panel.fieldChanged) as blocker:
        distance.spin.setValue(80.0)
    assert blocker.args[1] == pytest.approx(80.0)
    assert distance.slider.value() == distance.slider.maximum()


def test_slider_drag_previews_then_commits_once(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    weight = _editor(panel, "weight")
    slider = weight.slider
    assert slider is not None

    slider.setSliderDown(True)
    slider.setValue(400)
    slider.setValue(600)
    assert [field for field, _ in events["preview"]] == ["weight", "weight"]
    assert events["preview"][-1][1] == pytest.approx(3.0)
    assert events["changed"] == []
    assert panel.model is not None
    assert panel.model.weight == 1.0  # type: ignore[attr-defined]

    slider.setSliderDown(False)
    assert events["changed"] == [("weight", pytest.approx(3.0))]
    assert weight.spin.value() == pytest.approx(3.0)


def test_slider_track_click_commits(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    slider = _editor(panel, "weight").slider
    assert slider is not None
    slider.setValue(1000)
    assert events["preview"] == []
    assert events["changed"] == [("weight", pytest.approx(5.0))]


def test_refresh_moves_slider_without_signals(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    panel.set_model(_Numbers(weight=2.5))
    slider = _editor(panel, "weight").slider
    assert slider is not None
    assert slider.value() == 500
    assert events == {"preview": [], "changed": []}


# —— Label dragging ——


def test_small_move_is_not_a_drag(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    _drag(_row_label(panel, "weight"), [1.0, 2.0])
    assert events == {"preview": [], "changed": []}
    assert _editor(panel, "weight").spin.value() == pytest.approx(1.0)


def test_label_drag_previews_then_commits_once(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    label = _row_label(panel, "weight")

    # 0..5 spans 300 px, so 60 px is +1.0.
    _drag(label, [20.0, 40.0, 60.0], release=False)
    assert label.dragging
    assert len(events["preview"]) == 3
    assert events["preview"][-1][1] == pytest.approx(2.0)
    assert events["changed"] == []

    _mouse(label, QEvent.Type.MouseButtonRelease, 60.0, buttons=Qt.MouseButton.NoButton)
    assert events["changed"] == [("weight", pytest.approx(2.0))]
    assert panel.model is not None
    assert panel.model.weight == pytest.approx(2.0)  # type: ignore[attr-defined]
    weight = _editor(panel, "weight")
    assert weight.slider is not None
    assert weight.slider.value() == 400


def test_label_drag_modifiers(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    label = _row_label(panel, "weight")
    spin = _editor(panel, "weight").spin

    _drag(label, [60.0], modifiers=Qt.KeyboardModifier.ControlModifier)
    assert spin.value() == pytest.approx(1.1)
    _drag(label, [6.0], modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert spin.value() == pytest.approx(2.1)


def test_label_drag_clamps_and_turns_back_at_limit(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    label = _row_label(panel, "weight")
    spin = _editor(panel, "weight").spin
    _drag(label, [1000.0, 940.0])
    # Clamped at 5.0 on the way out, then -1.0 on the way back.
    assert spin.value() == pytest.approx(4.0)


def test_slow_int_drag_accumulates(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    label = _row_label(panel, "count")
    # 0..10 spans 300 px: 30 px per step, fed one pixel at a time.
    _drag(label, [float(x) for x in range(3, 31)])
    assert _editor(panel, "count").spin.value() == 6
    assert events["changed"] == [("count", 6)]


def test_escape_cancels_drag(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    label = _row_label(panel, "weight")
    _drag(label, [30.0, 60.0], release=False)
    QApplication.sendEvent(
        label,
        QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        ),
    )
    assert not label.dragging
    assert _editor(panel, "weight").spin.value() == pytest.approx(1.0)
    # The last preview puts the start value back for live hosts.
    assert events["preview"][-1] == ("weight", 1.0)
    _mouse(label, QEvent.Type.MouseButtonRelease, 60.0, buttons=Qt.MouseButton.NoButton)
    assert events["changed"] == []


def _claims_escape(label: _ScrubLabel) -> bool:
    # Qt sends ShortcutOverride, ignored, before matching window shortcuts.
    # Accepting it is how a widget keeps a key from a host's shortcut.
    event = QKeyEvent(
        QEvent.Type.ShortcutOverride,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()
    QApplication.sendEvent(label, event)
    return event.isAccepted()


def test_drag_claims_escape_from_window_shortcuts(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    label = _row_label(panel, "weight")
    assert not _claims_escape(label)
    _drag(label, [30.0, 60.0], release=False)
    assert _claims_escape(label)
    _mouse(label, QEvent.Type.MouseButtonRelease, 60.0, buttons=Qt.MouseButton.NoButton)
    assert not _claims_escape(label)


def test_read_only_label_does_not_drag(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    events = _record(panel)
    label = _row_label(panel, "locked")
    _drag(label, [60.0])
    assert _editor(panel, "locked").spin.value() == pytest.approx(1.0)
    assert events == {"preview": [], "changed": []}


def test_vector_axis_label_drag_in_degrees(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    stored_x = 0.3
    panel.set_model(_Units(rotation=(stored_x, 0.0, 0.0)))
    events = _record(panel)
    vec = panel._editors["rotation"]
    assert isinstance(vec, _VecEditor)
    # Unbounded degrees: a quarter of the 1° step per pixel.
    _drag(vec._axis_labels[1], [40.0])
    assert len(events["changed"]) == 1
    assert events["changed"][0][0] == "rotation"
    committed = events["changed"][-1][1]
    assert committed[0] == stored_x
    assert committed[1] == pytest.approx(math.radians(10.0))


def test_preview_handler_set_model_is_deferred(qtbot) -> None:
    """A host refreshing the panel from a preview must not touch the spin mid-drag."""
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Numbers())
    panel.fieldPreviewed.connect(lambda _f, _v, model: panel.set_model(model))
    label = _row_label(panel, "weight")
    spin_id = id(_editor(panel, "weight").spin)
    _drag(label, [30.0, 60.0])
    qtbot.wait(10)
    assert id(_editor(panel, "weight").spin) == spin_id
    assert panel.model is not None
    assert panel.model.weight == pytest.approx(2.0)  # type: ignore[attr-defined]


def test_book_forwards_previews(qtbot) -> None:
    book = PropertiesBook()
    qtbot.addWidget(book)
    book.set_pages([("Numbers", _Numbers())])
    events = _record(book)
    panel = book._panels[0]
    _drag(_row_label(panel, "weight"), [30.0])
    assert events["preview"] == [("weight", pytest.approx(1.5))]
    assert events["changed"] == [("weight", pytest.approx(1.5))]
