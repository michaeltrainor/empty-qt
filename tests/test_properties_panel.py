"""Tests for :class:`~mtqt.PropertiesPanel`."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from mtqt import PropertiesPanel
from mtqt.widgets.path_picker import PathPicker
from mtqt.widgets.properties_panel import _ColorEditor, _VecEditor


class _Priority(Enum):
    LOW = "low"
    HIGH = "high"


class _Sample(BaseModel):
    name: str = Field(frozen=True, title="Name")
    enabled: bool = True
    count: int = Field(default=1, ge=0, le=10)
    weight: float = Field(default=1.0, ge=0.0, le=5.0)
    kind: Literal["box", "sphere"] = "box"
    priority: _Priority = _Priority.LOW
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    color: str = Field(default="#ffffff", json_schema_extra={"mtqt_editor": "color"})
    asset: Path = Field(default=Path("/tmp/asset.ma"), title="Asset")

    @field_validator("color")
    @classmethod
    def _color_must_be_hex(cls, value: str) -> str:
        if not value.startswith("#") or len(value) not in {4, 7}:
            msg = "color must be #rgb or #rrggbb"
            raise ValueError(msg)
        return value


def test_properties_panel_empty(qtbot) -> None:
    panel = PropertiesPanel(empty_message="Nothing selected")
    qtbot.addWidget(panel)
    assert panel.objectName() == "PropertiesPanel"
    assert panel.model is None
    assert not panel._empty.isHidden()
    assert panel._scroll.isHidden()
    assert panel._empty.text() == "Nothing selected"


def test_properties_panel_builds_editors_and_commits(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    sample = _Sample(name="crate", enabled=False, count=2, weight=0.5, color="#ff00aa")
    panel.set_model(sample)
    assert panel.model is not None
    assert panel.model.name == "crate"
    assert panel._empty.isHidden()

    edits = panel._form_host.findChildren(QLineEdit)
    assert any(edit.isReadOnly() and edit.text() == "crate" for edit in edits)

    checks = panel._form_host.findChildren(QCheckBox)
    assert checks
    with qtbot.waitSignal(panel.modelChanged) as blocker:
        checks[0].setChecked(True)
    updated = blocker.args[0]
    assert isinstance(updated, _Sample)
    assert updated.enabled is True
    assert panel.model is updated


def test_properties_panel_literal_and_vec(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Sample(name="ball", kind="sphere", position=(1.0, 2.0, 3.0)))

    combos = panel._form_host.findChildren(QComboBox)
    kind_combo = next(c for c in combos if c.currentData() == "sphere")
    with qtbot.waitSignal(panel.modelChanged) as blocker:
        kind_combo.setCurrentIndex(kind_combo.findData("box"))
    assert blocker.args[0].kind == "box"

    vec = panel._form_host.findChild(_VecEditor)
    assert vec is not None
    with qtbot.waitSignal(panel.modelChanged) as blocker:
        vec._spins[0].setValue(9.5)
    assert blocker.args[0].position[0] == 9.5


def test_properties_panel_bounds_color_path_and_validation(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Sample(name="props"))

    count = next(
        spin
        for spin in panel._form_host.findChildren(QSpinBox)
        if spin.minimum() == 0 and spin.maximum() == 10
    )
    weight = next(
        spin
        for spin in panel._form_host.findChildren(QDoubleSpinBox)
        if abs(spin.maximum() - 5.0) < 1e-9
    )
    assert count.minimum() == 0
    assert weight.maximum() == 5.0

    color = panel._form_host.findChild(_ColorEditor)
    assert color is not None
    with qtbot.waitSignal(panel.modelChanged) as blocker:
        color.set_value("#abc")
        color._emit()
    assert blocker.args[0].color == "#abc"

    with qtbot.waitSignal(panel.validationFailed) as blocker:
        color.set_value("not-a-color")
        color._emit()
    assert blocker.args[0] == "color"
    assert "color" in blocker.args[1]
    assert panel.model is not None
    assert panel.model.color == "#abc"
    assert not panel._error.isHidden()

    picker = panel._form_host.findChild(PathPicker)
    assert picker is not None
    with qtbot.waitSignal(panel.modelChanged) as blocker:
        picker.path = Path("/tmp/other.ma")
    assert blocker.args[0].asset == Path("/tmp/other.ma")


def test_properties_panel_inplace_refresh(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    first = _Sample(name="a", count=1, color="#111111")
    panel.set_model(first)
    color = panel._form_host.findChild(_ColorEditor)
    assert color is not None
    editor_id = id(color)

    second = first.model_copy(update={"count": 4, "color": "#222222"})
    panel.set_model(second)
    color_after = panel._form_host.findChild(_ColorEditor)
    assert color_after is not None
    assert id(color_after) == editor_id
    assert color_after.value() == "#222222"
    assert panel.model is not None
    assert panel.model.count == 4


def test_properties_panel_clear(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(_Sample(name="x"))
    assert panel._empty.isHidden()
    panel.clear()
    assert panel.model is None
    assert not panel._empty.isHidden()
    assert panel._scroll.isHidden()


def test_properties_panel_editability_and_handlers(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    events: list[tuple[str, object]] = []

    def on_any(field: str, value: object, model: BaseModel) -> None:
        events.append((field, value))
        assert isinstance(model, _Sample)

    def on_count(field: str, value: object, model: BaseModel) -> None:
        events.append((f"count:{field}", value))

    panel.register_handler(on_any)
    panel.register_field_handler("count", on_count)
    panel.set_field_editable("enabled", False)
    panel.set_model(_Sample(name="hooked", enabled=True, count=1))

    enabled = panel._form_host.findChild(QCheckBox)
    assert enabled is not None
    assert not enabled.isEnabled()

    count = next(
        spin
        for spin in panel._form_host.findChildren(QSpinBox)
        if spin.minimum() == 0 and spin.maximum() == 10
    )
    count_id = id(count)
    with qtbot.waitSignal(panel.fieldChanged) as blocker:
        count.setValue(3)
    assert blocker.args[0] == "count"
    assert blocker.args[1] == 3
    assert ("count", 3) in events
    assert ("count:count", 3) in events
    # Same-type set_model outside a commit refreshes in place.
    panel.set_model(panel.model.model_copy(update={"count": 3}))
    assert any(id(spin) == count_id for spin in panel._form_host.findChildren(QSpinBox))

    panel.set_editable_check(lambda field, _model: field != "weight")
    weight = next(
        spin
        for spin in panel._form_host.findChildren(QDoubleSpinBox)
        if abs(spin.maximum() - 5.0) < 1e-9
    )
    assert weight.isReadOnly()

    panel.unregister_handler(on_any)
    panel.unregister_field_handler("count")
    events.clear()
    count = next(
        spin
        for spin in panel._form_host.findChildren(QSpinBox)
        if spin.minimum() == 0 and spin.maximum() == 10
    )
    count.setValue(4)
    assert events == []


def test_properties_panel_handler_set_model_keeps_vec_spin(qtbot) -> None:
    """Handlers that call set_model must not destroy a spin mid-commit."""
    panel = PropertiesPanel()
    qtbot.addWidget(panel)

    def mirror(_field: str, _value: object, model: BaseModel) -> None:
        panel.set_model(model)

    panel.register_handler(mirror)
    panel.set_model(_Sample(name="parent", position=(0.0, 0.0, 0.0)))
    vec = panel._form_host.findChild(_VecEditor)
    assert vec is not None
    spin = vec._spins[0]
    spin_id = id(spin)

    with qtbot.waitSignal(panel.modelChanged):
        spin.setValue(1.5)
    assert id(vec._spins[0]) == spin_id
    assert panel.model is not None
    assert panel.model.position[0] == 1.5
    qtbot.waitUntil(lambda: abs(vec._spins[0].value() - 1.5) < 1e-9)
