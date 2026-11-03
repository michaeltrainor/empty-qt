"""Schema-driven property form built from a pydantic model instance."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, SupportsFloat, cast, get_args, get_origin

import annotated_types as at
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mtqt.widgets.path_picker import PathPicker

type _EditorCommit = Callable[[Any], None]
type EditableCheck = Callable[[str, BaseModel], bool]
type FieldHandler = Callable[[str, Any, BaseModel], None]

logger = logging.getLogger(__name__)


def _unwrap_annotation(annotation: Any) -> Any:
    """Strip ``Annotated`` wrappers and return the concrete annotation."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _unwrap_annotation(get_args(annotation)[0])
    return annotation


def _literal_choices(annotation: Any) -> list[Any] | None:
    annotation = _unwrap_annotation(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return list(get_args(annotation))
    return None


def _enum_type(annotation: Any) -> type[Enum] | None:
    annotation = _unwrap_annotation(annotation)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation
    return None


def _float_tuple_size(annotation: Any) -> int | None:
    annotation = _unwrap_annotation(annotation)
    origin = get_origin(annotation)
    if origin is not tuple:
        return None
    args = get_args(annotation)
    if not args or any(arg is Ellipsis for arg in args):
        return None
    if all(arg is float for arg in args):
        return len(args)
    return None


def _is_path_type(annotation: Any) -> bool:
    annotation = _unwrap_annotation(annotation)
    return isinstance(annotation, type) and issubclass(annotation, Path)


def _field_label(name: str, info: FieldInfo) -> str:
    if info.title:
        return info.title
    return name.replace("_", " ").title()


def _schema_extra(info: FieldInfo) -> dict[str, Any]:
    """Return ``Field(json_schema_extra=...)`` when it is a dict, else ``{}``."""
    extra = info.json_schema_extra
    return cast(dict[str, Any], extra) if isinstance(extra, dict) else {}


def _field_read_only(info: FieldInfo) -> bool:
    if info.frozen:
        return True
    return bool(_schema_extra(info).get("mtqt_read_only"))


def _field_editor_kind(info: FieldInfo) -> str | None:
    """Return ``mtqt_editor`` from ``Field(json_schema_extra=...)``, if any."""
    kind = _schema_extra(info).get("mtqt_editor")
    return kind if isinstance(kind, str) else None


@dataclass(frozen=True, slots=True)
class _DisplayUnit:
    """Conversion between a stored float and the number an editor shows."""

    scale: float = 1.0
    suffix: str = ""
    decimals: int = 3
    step: float = 0.1

    def to_display(self, value: float) -> float:
        """Return ``value`` in display units."""
        return float(value) * self.scale

    def from_display(self, value: float) -> float:
        """Return a display-unit ``value`` in stored units."""
        return float(value) / self.scale


_DISPLAY_PRESETS: dict[str, _DisplayUnit] = {
    "degrees": _DisplayUnit(scale=180.0 / math.pi, suffix="°", decimals=2, step=1.0),
    "percent": _DisplayUnit(scale=100.0, suffix="%", decimals=1, step=1.0),
}


def _display_unit(info: FieldInfo) -> _DisplayUnit:
    """Return the display unit from ``mtqt_display`` / ``mtqt_suffix``."""
    extra = _schema_extra(info)
    unit = _DisplayUnit()
    preset = extra.get("mtqt_display")
    if preset is not None:
        found = _DISPLAY_PRESETS.get(preset) if isinstance(preset, str) else None
        if found is None:
            logger.warning("Unknown mtqt_display %r; showing stored values", preset)
        else:
            unit = found
    suffix = extra.get("mtqt_suffix")
    if isinstance(suffix, str):
        unit = replace(unit, suffix=suffix)
    return unit


def _soft_range(info: FieldInfo) -> tuple[float, float] | None:
    """Return ``mtqt_soft_range`` as ``(low, high)`` in stored units, if valid."""
    raw = _schema_extra(info).get("mtqt_soft_range")
    if raw is None:
        return None
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        try:
            low, high = float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            pass
        else:
            if low < high:
                return low, high
    logger.warning("Ignoring mtqt_soft_range %r; expected (low, high)", raw)
    return None


def _slider_allowed(info: FieldInfo) -> bool:
    """Return ``False`` when the field sets ``mtqt_slider: False``."""
    return _schema_extra(info).get("mtqt_slider", True) is not False


def _numeric_bounds(info: FieldInfo) -> tuple[float | None, float | None]:
    """Return ``(minimum, maximum)`` from pydantic ``ge`` / ``le`` / ``gt`` / ``lt``."""
    lo: float | None = None
    hi: float | None = None
    for item in info.metadata:
        if isinstance(item, at.Ge):
            lo = float(cast(SupportsFloat, item.ge))
        elif isinstance(item, at.Gt):
            lo = float(cast(SupportsFloat, item.gt))
        elif isinstance(item, at.Le):
            hi = float(cast(SupportsFloat, item.le))
        elif isinstance(item, at.Lt):
            hi = float(cast(SupportsFloat, item.lt))
    return lo, hi


def _polish_invalid(widget: QWidget, invalid: bool) -> None:
    previous = widget.property("invalid")
    widget.setProperty("invalid", invalid)
    style = widget.style()
    if style is not None and previous != invalid:
        style.unpolish(widget)
        style.polish(widget)


def _validation_message(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()))
        msg = str(error.get("msg", "invalid"))
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) if parts else "Validation failed"


# Compact attribute-editor metrics (Maya / Blender density: ~22 px fields,
# a few pixels between rows). QSS in widgets.qss sets font and padding.
_FORM_MARGINS = (8, 6, 8, 8)
_FORM_H_SPACING = 8
_FORM_V_SPACING = 3
_VEC_SPACING = 2
_VEC_MIN_WIDTH = 40
_SWATCH_SIZE = (22, 20)
_NUMBER_SPIN_WIDTH = 64
_SLIDER_STEPS = 1000
_SCRUB_THRESHOLD_PX = 3.0
_SCRUB_RANGE_PX = 300.0
_DEFAULT_INT_RANGE = 1_000_000_000
_DEFAULT_FLOAT_RANGE = 1_000_000.0

type _Spin = QSpinBox | QDoubleSpinBox
type _ValueCallback = Callable[[Any], None]


def _scrub_multiplier(modifiers: Qt.KeyboardModifier) -> float:
    """Return the drag speed factor: Ctrl is fine (x0.1), Shift is coarse (x10).

    macOS reports the Control key as ``MetaModifier``, so both count as Ctrl.
    """
    fine = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
    if modifiers & fine:
        return 0.1
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return 10.0
    return 1.0


def _scrub_step(spin: _Spin, low: float | None, high: float | None) -> float:
    """Return value change per pixel: bounded ranges span ``_SCRUB_RANGE_PX``."""
    if low is not None and high is not None and high > low:
        return (high - low) / _SCRUB_RANGE_PX
    return float(spin.singleStep()) / 4.0


class _ScrubLabel(QLabel):
    """Label that changes a spin box's value when dragged left or right.

    A drag starts after ``_SCRUB_THRESHOLD_PX`` of movement, so a plain click
    does nothing. Values go into the spin box with signals blocked; listeners
    get ``scrubbed`` per change, ``scrubFinished`` on release, and
    ``scrubCancelled`` after Esc puts the start value back.
    """

    scrubbed = Signal()
    scrubFinished = Signal()
    scrubCancelled = Signal()

    def __init__(
        self,
        text: str,
        spin: _Spin,
        parent: QWidget | None = None,
        *,
        px_step: float,
    ) -> None:
        super().__init__(text, parent)
        self._spin = spin
        self._px_step = px_step
        self._press_x: float | None = None
        self._last_x = 0.0
        self._start: float = 0.0
        self._accum = 0.0
        self._dragging = False
        self.setProperty("scrub", not spin.isReadOnly())
        if not spin.isReadOnly():
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    @property
    def dragging(self) -> bool:
        """Whether a drag is in progress."""
        return self._dragging

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._spin.isReadOnly():
            super().mousePressEvent(event)
            return
        self._press_x = event.position().x()
        self._last_x = self._press_x
        self._start = float(self._spin.value())
        self._accum = 0.0
        self._dragging = False
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_x is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        x = event.position().x()
        if not self._dragging:
            if abs(x - self._press_x) < _SCRUB_THRESHOLD_PX:
                return
            self._dragging = True
        delta = (x - self._last_x) * self._px_step
        self._last_x = x
        self._accum += delta * _scrub_multiplier(event.modifiers())
        spin = self._spin
        target = min(
            max(self._start + self._accum, float(spin.minimum())),
            float(spin.maximum()),
        )
        # Clamp the running total too, so dragging back from a limit
        # responds at once instead of crossing a dead zone.
        self._accum = target - self._start
        self._set_spin(target)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_x is None:
            super().mouseReleaseEvent(event)
            return
        dragged = self._dragging
        self._press_x = None
        self._dragging = False
        if dragged:
            self.scrubFinished.emit()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._dragging:
            self._press_x = None
            self._dragging = False
            self._set_spin(self._start, notify=False)
            self.scrubCancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_spin(self, value: float, *, notify: bool = True) -> None:
        spin = self._spin
        before = spin.value()
        spin.blockSignals(True)
        if isinstance(spin, QSpinBox):
            spin.setValue(round(value))
        else:
            spin.setValue(value)
        spin.blockSignals(False)
        if notify and spin.value() != before:
            self.scrubbed.emit()


class _VecEditor(QWidget):
    """Row of spin boxes for a fixed-length float tuple.

    Each component has a draggable X/Y/Z/W label. Components the user did
    not touch return their stored value exactly, so display rounding (for
    example radians shown in degrees) never drifts the other components.
    """

    def __init__(
        self,
        size: int,
        parent: QWidget | None = None,
        *,
        read_only: bool = False,
        minimum: float | None = None,
        maximum: float | None = None,
        unit: _DisplayUnit | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesVecEditor")
        self._unit = unit or _DisplayUnit()
        self._spins: list[QDoubleSpinBox] = []
        self._axis_labels: list[_ScrubLabel] = []
        self._stored: tuple[float, ...] = (0.0,) * size
        self._loaded: list[float] = [0.0] * size
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_VEC_SPACING)
        labels = ("X", "Y", "Z", "W")
        lo = None if minimum is None else self._unit.to_display(minimum)
        hi = None if maximum is None else self._unit.to_display(maximum)
        for index in range(size):
            spin = QDoubleSpinBox(self)
            spin.setRange(
                -_DEFAULT_FLOAT_RANGE if lo is None else lo,
                _DEFAULT_FLOAT_RANGE if hi is None else hi,
            )
            spin.setDecimals(self._unit.decimals)
            spin.setSingleStep(self._unit.step)
            spin.setReadOnly(read_only)
            spin.setKeyboardTracking(False)
            # Share the row evenly instead of sizing to the +/-1e6 range text,
            # which would push the last component off a narrow panel.
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setMinimumWidth(_VEC_MIN_WIDTH)
            spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            axis = _ScrubLabel(
                labels[index], spin, self, px_step=_scrub_step(spin, lo, hi)
            )
            axis.setObjectName("PropertiesAxisLabel")
            layout.addWidget(axis)
            layout.addWidget(spin, stretch=1)
            self._spins.append(spin)
            self._axis_labels.append(axis)

    @property
    def suffix(self) -> str:
        """Unit text for the row label; the narrow spins omit it."""
        return self._unit.suffix.strip()

    def value(self) -> tuple[float, ...]:
        """Return the current float tuple in stored units."""
        return tuple(
            stored if spin.value() == loaded else self._unit.from_display(spin.value())
            for spin, stored, loaded in zip(
                self._spins, self._stored, self._loaded, strict=True
            )
        )

    def set_value(self, values: Sequence[float]) -> None:
        """Load values into the spin boxes without emitting commits."""
        self._stored = tuple(float(value) for value in values)
        for index, (spin, value) in enumerate(
            zip(self._spins, self._stored, strict=True)
        ):
            spin.blockSignals(True)
            spin.setValue(self._unit.to_display(value))
            spin.blockSignals(False)
            self._loaded[index] = spin.value()

    def bind(
        self,
        commit: _ValueCallback,
        preview: _ValueCallback | None = None,
        finish: _ValueCallback | None = None,
    ) -> None:
        """Wire typed edits to ``commit`` and label drags to preview/finish.

        Args:
            commit: Called with the tuple when a spin value is entered.
            preview: Called with the tuple on each drag step and on cancel.
            finish: Called with the tuple when a drag ends.
        """

        def on_change(_value: float = 0.0) -> None:
            commit(self.value())

        for spin in self._spins:
            spin.valueChanged.connect(on_change)
        for axis in self._axis_labels:
            if preview is not None:
                axis.scrubbed.connect(lambda: preview(self.value()))
                axis.scrubCancelled.connect(lambda: preview(self.value()))
            if finish is not None:
                axis.scrubFinished.connect(lambda: finish(self.value()))


class _NumberEditor(QWidget):
    """Spin box for an ``int`` or ``float`` field, with an optional slider.

    The slider appears when ``slider_range`` is given (Maya-style field and
    slider group). While the handle is dragged, values go to ``preview``;
    releasing it calls ``finish``. Typed values and track clicks commit.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        is_int: bool,
        unit: _DisplayUnit,
        minimum: float | None,
        maximum: float | None,
        slider_range: tuple[float, float] | None,
        read_only: bool,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesNumberEditor")
        self._is_int = is_int
        self._unit = unit
        self._stored: float = 0
        self._loaded: float = 0.0
        self._commit: _ValueCallback | None = None
        self._preview: _ValueCallback | None = None
        self._finish: _ValueCallback | None = None
        self.spin: _Spin
        if is_int:
            int_spin = QSpinBox(self)
            int_spin.setRange(
                -_DEFAULT_INT_RANGE if minimum is None else int(minimum),
                _DEFAULT_INT_RANGE if maximum is None else int(maximum),
            )
            self.spin = int_spin
        else:
            float_spin = QDoubleSpinBox(self)
            float_spin.setDecimals(unit.decimals)
            float_spin.setSingleStep(unit.step)
            float_spin.setRange(
                -_DEFAULT_FLOAT_RANGE if minimum is None else minimum,
                _DEFAULT_FLOAT_RANGE if maximum is None else maximum,
            )
            self.spin = float_spin
        self.spin.setSuffix(unit.suffix)
        self.spin.setReadOnly(read_only)
        self.spin.setKeyboardTracking(False)

        bounded = minimum is not None and maximum is not None
        self._scrub_range: tuple[float | None, float | None] = (
            slider_range
            if slider_range is not None
            else (minimum, maximum)
            if bounded
            else (None, None)
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_VEC_SPACING * 2)
        self.slider: QSlider | None = None
        self._slider_lo = 0.0
        self._slider_hi = 1.0
        if slider_range is None:
            layout.addWidget(self.spin)
            return
        self._slider_lo, self._slider_hi = slider_range
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setFixedWidth(_NUMBER_SPIN_WIDTH)
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setObjectName("PropertiesSlider")
        span = self._slider_hi - self._slider_lo
        steps = int(span) if is_int and span <= _SLIDER_STEPS else _SLIDER_STEPS
        slider.setRange(0, max(steps, 1))
        slider.setPageStep(max(steps // 10, 1))
        slider.setEnabled(not read_only)
        layout.addWidget(self.spin)
        layout.addWidget(slider, stretch=1)
        self.slider = slider

    def scrub_step(self) -> float:
        """Return the drag change per pixel for this editor's label."""
        low, high = self._scrub_range
        return _scrub_step(self.spin, low, high)

    def value(self) -> float:
        """Return the current value in stored units (``int`` for int fields)."""
        shown = self.spin.value()
        if shown == self._loaded:
            return self._stored
        if self._is_int:
            return int(shown)
        return self._unit.from_display(shown)

    def set_value(self, value: float) -> None:
        """Load ``value`` (stored units) without emitting commits."""
        self._stored = value
        self.spin.blockSignals(True)
        if isinstance(self.spin, QSpinBox):
            self.spin.setValue(int(value))
        else:
            self.spin.setValue(self._unit.to_display(value))
        self.spin.blockSignals(False)
        self._loaded = self.spin.value()
        self._sync_slider()

    def bind(
        self,
        commit: _ValueCallback,
        preview: _ValueCallback,
        finish: _ValueCallback,
    ) -> None:
        """Wire typed edits, slider moves, and drags to the panel callbacks.

        Args:
            commit: Called with the value when it is entered or stepped.
            preview: Called with the value on each drag step and on cancel.
            finish: Called with the value when a slider or label drag ends.
        """
        self._commit = commit
        self._preview = preview
        self._finish = finish
        self.spin.valueChanged.connect(self._on_spin_changed)
        if self.slider is not None:
            self.slider.valueChanged.connect(self._on_slider_changed)
            self.slider.sliderReleased.connect(self._on_drag_finished)

    def attach_scrub(self, label: _ScrubLabel) -> None:
        """Route drags on the row label through this editor."""
        label.scrubbed.connect(self._on_scrubbed)
        label.scrubCancelled.connect(self._on_scrubbed)
        label.scrubFinished.connect(self._on_drag_finished)

    def _on_spin_changed(self, _value: float) -> None:
        self._sync_slider()
        if self._commit is not None:
            self._commit(self.value())

    def _on_slider_changed(self, position: int) -> None:
        slider = self.slider
        if slider is None:
            return
        fraction = position / slider.maximum()
        shown = self._slider_lo + (self._slider_hi - self._slider_lo) * fraction
        self.spin.blockSignals(True)
        if isinstance(self.spin, QSpinBox):
            self.spin.setValue(round(shown))
        else:
            self.spin.setValue(shown)
        self.spin.blockSignals(False)
        if slider.isSliderDown():
            if self._preview is not None:
                self._preview(self.value())
        elif self._commit is not None:
            self._commit(self.value())

    def _on_scrubbed(self) -> None:
        self._sync_slider()
        if self._preview is not None:
            self._preview(self.value())

    def _on_drag_finished(self) -> None:
        if self._finish is not None:
            self._finish(self.value())

    def _sync_slider(self) -> None:
        slider = self.slider
        if slider is None:
            return
        span = self._slider_hi - self._slider_lo
        fraction = (float(self.spin.value()) - self._slider_lo) / span
        position = round(min(max(fraction, 0.0), 1.0) * slider.maximum())
        slider.blockSignals(True)
        slider.setValue(position)
        slider.blockSignals(False)


class _ColorEditor(QWidget):
    """Hex color line edit with a swatch that opens ``QColorDialog``."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesColorEditor")
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("#rrggbb")
        self._edit.setReadOnly(read_only)
        self._swatch = QPushButton(self)
        self._swatch.setObjectName("PropertiesColorSwatch")
        self._swatch.setFixedSize(_SWATCH_SIZE[0], _SWATCH_SIZE[1])
        self._swatch.setEnabled(not read_only)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_VEC_SPACING * 2)
        layout.addWidget(self._edit, stretch=1)
        layout.addWidget(self._swatch)
        if not read_only:
            self._edit.editingFinished.connect(self._on_edit_finished)
            self._swatch.clicked.connect(self._on_pick)
        self._commit: _EditorCommit | None = None

    def value(self) -> str:
        """Return the current hex (or raw) color text."""
        return self._edit.text().strip()

    def set_value(self, value: str) -> None:
        """Load a color string and refresh the swatch."""
        text = str(value)
        if self._edit.text() != text:
            self._edit.blockSignals(True)
            self._edit.setText(text)
            self._edit.blockSignals(False)
        self._update_swatch(text)

    def bind(self, commit: _EditorCommit) -> None:
        """Call ``commit`` when the color changes."""
        self._commit = commit

    def _emit(self) -> None:
        if self._commit is not None:
            self._commit(self.value())

    def _update_swatch(self, text: str) -> None:
        color = QColor(text)
        if not color.isValid():
            color = QColor("#000000")
        self._swatch.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #555;"
        )

    def _on_edit_finished(self) -> None:
        self._update_swatch(self.value())
        self._emit()

    def _on_pick(self) -> None:
        initial = QColor(self.value())
        if not initial.isValid():
            initial = QColor("#ffffff")
        chosen = QColorDialog.getColor(initial, self, "Select color")
        if not chosen.isValid():
            return
        self.set_value(chosen.name())
        self._emit()


class PropertiesPanel(QWidget):
    """A form generated from a :class:`~pydantic.BaseModel` instance.

    The model type is the field profile: titles, frozen/read-only flags,
    ``Literal`` choices, numeric bounds, and types drive the editors. The
    panel does not know about meshes, scenes, or any host domain.

    Host tools keep business logic outside the widget by registering field
    handlers and optional editability hooks. Schema ``frozen`` /
    ``mtqt_read_only`` still apply unless :meth:`set_field_editable`
    overrides a field.

    Supported field types: ``str``, ``bool``, ``int``, ``float``,
    ``Literal[...]``, ``Enum``, ``pathlib.Path``, fixed-length
    ``tuple[float, ...]`` (2-4), and color strings marked with
    ``Field(json_schema_extra={"mtqt_editor": "color"})``. Unsupported
    fields render as a read-only summary.

    Numbers can be dragged: drag a numeric row's label (or a vector's X/Y/Z
    label) left or right; hold Ctrl for fine steps and Shift for coarse
    ones, and press Esc to cancel. ``int`` and ``float`` fields with both a
    lower and an upper bound get a slider. Optional
    ``json_schema_extra`` keys:

    - ``mtqt_display``: ``"degrees"`` (stored radians) or ``"percent"``
      (stored fractions) for floats and float tuples.
    - ``mtqt_suffix``: text shown after the number, such as ``" m"``.
    - ``mtqt_slider``: ``False`` hides the slider on a bounded field.
    - ``mtqt_soft_range``: ``(low, high)`` in stored units; the slider
      covers it while typed values may go past it.

    Drags and slider moves emit :attr:`fieldPreviewed` for each step and
    :attr:`fieldChanged` once on release, so a host can update live and
    record one undo step.

    Calling :meth:`set_model` with the same model type refreshes editor
    values in place instead of rebuilding the form. Any form refresh,
    rebuild, or clear triggered from inside a field commit is deferred to
    the next event-loop tick so spin boxes are not touched mid-click.

    Args:
        parent: Qt parent widget.
        empty_message: Placeholder shown when no model is set.
    """

    modelChanged = Signal(object)
    fieldChanged = Signal(str, object, object)
    fieldPreviewed = Signal(str, object, object)
    """``(field_name, value, model)`` during a drag; ``model`` is a validated
    copy and :attr:`model` is unchanged until the drag ends."""
    validationFailed = Signal(str, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        empty_message: str = "No properties",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesPanel")
        self.setAccessibleName("Properties")
        self._model: BaseModel | None = None
        self._empty_message = empty_message
        self._updating = False
        self._committing = False
        self._editors: dict[str, QWidget] = {}
        self._editable_check: EditableCheck | None = None
        self._editable_overrides: dict[str, bool] = {}
        self._field_handlers: dict[str, list[FieldHandler]] = {}
        self._handlers: list[FieldHandler] = []

        self._empty = QLabel(empty_message, self)
        self._empty.setWordWrap(True)
        self._empty.setObjectName("PropertiesPanelEmpty")

        self._form_host = QWidget(self)
        self._form = QFormLayout(self._form_host)
        # Pin every form metric: platform styles differ (macOS top-aligns
        # left-aligned labels and wraps rows), and DCC attribute editors use
        # right-aligned labels with a few pixels between rows.
        self._form.setContentsMargins(*_FORM_MARGINS)
        self._form.setHorizontalSpacing(_FORM_H_SPACING)
        self._form.setVerticalSpacing(_FORM_V_SPACING)
        self._form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self._form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._form_host)

        self._error = QLabel(self)
        self._error.setObjectName("PropertiesPanelError")
        self._error.setWordWrap(True)
        self._error.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._empty)
        layout.addWidget(scroll, stretch=1)
        layout.addWidget(self._error)
        self._scroll = scroll
        self._show_empty()

    @property
    def model(self) -> BaseModel | None:
        """Model currently displayed, or ``None`` when empty."""
        return self._model

    def set_editable_check(self, check: EditableCheck | None) -> None:
        """Set a host hook that decides whether each field is editable.

        The callable receives ``(field_name, model)`` and returns ``True``
        when the field may be edited. Explicit :meth:`set_field_editable`
        overrides win over this hook. Changing the check rebuilds the
        current form so widgets pick up the new policy.

        Args:
            check: Editability callback, or ``None`` to clear it.
        """
        self._editable_check = check
        self._reload_editors_for_policy()

    def set_field_editable(self, field_name: str, editable: bool) -> None:
        """Force a field editable or read-only, overriding the schema hook.

        Args:
            field_name: Model field name.
            editable: ``True`` to allow edits, ``False`` to lock the field.
        """
        self._editable_overrides[field_name] = editable
        self._reload_editors_for_policy()

    def clear_field_editable(self, field_name: str | None = None) -> None:
        """Clear one field override, or all overrides when ``field_name`` is omitted.

        Args:
            field_name: Field to clear, or ``None`` for every override.
        """
        if field_name is None:
            self._editable_overrides.clear()
        else:
            self._editable_overrides.pop(field_name, None)
        self._reload_editors_for_policy()

    def register_handler(self, handler: FieldHandler) -> None:
        """Register a callback for every successful field commit.

        Args:
            handler: ``handler(field_name, value, model)`` invoked after
                validation succeeds and the panel model is updated.
        """
        self._handlers.append(handler)

    def unregister_handler(self, handler: FieldHandler) -> None:
        """Remove a previously registered global handler.

        Args:
            handler: Callback passed to :meth:`register_handler`.
        """
        self._handlers = [item for item in self._handlers if item is not handler]

    def register_field_handler(self, field_name: str, handler: FieldHandler) -> None:
        """Register a callback for commits to one field.

        Args:
            field_name: Model field name.
            handler: ``handler(field_name, value, model)`` after a successful
                commit of that field.
        """
        self._field_handlers.setdefault(field_name, []).append(handler)

    def unregister_field_handler(
        self,
        field_name: str,
        handler: FieldHandler | None = None,
    ) -> None:
        """Remove one field handler, or all handlers for ``field_name``.

        Args:
            field_name: Model field name.
            handler: Specific callback to remove. When ``None``, clears every
                handler registered for ``field_name``.
        """
        if handler is None:
            self._field_handlers.pop(field_name, None)
            return
        current = self._field_handlers.get(field_name, [])
        self._field_handlers[field_name] = [
            item for item in current if item is not handler
        ]
        if not self._field_handlers[field_name]:
            self._field_handlers.pop(field_name, None)

    def set_model(self, model: BaseModel | None) -> None:
        """Show ``model``, refreshing in place when the type is unchanged.

        Args:
            model: Pydantic model instance whose fields become editors.
        """
        if model is None:
            self._model = None
            if self._committing:
                QTimer.singleShot(0, self._apply_clear)
            else:
                self._apply_clear()
            return

        same_type = (
            self._model is not None
            and type(self._model) is type(model)
            and bool(self._editors)
        )
        self._model = model
        self._empty.hide()
        self._scroll.show()
        if same_type:
            if self._committing:
                # Avoid setValue on a spin still inside mousePress/stepBy.
                QTimer.singleShot(0, lambda current=model: self._apply_refresh(current))
                return
            self._apply_refresh(model)
            return
        if self._committing:
            # Avoid destroying the editor that is still handling the click.
            QTimer.singleShot(0, lambda current=model: self._apply_rebuild(current))
            return
        self._apply_rebuild(model)

    def clear(self) -> None:
        """Remove the current model and show the empty placeholder."""
        self.set_model(None)

    def _apply_clear(self) -> None:
        if self._model is not None:
            return
        self._editors.clear()
        self._clear_form()
        self._clear_validation()
        self._show_empty()

    def _apply_refresh(self, model: BaseModel) -> None:
        if self._model is not model or not self._editors:
            return
        self._updating = True
        try:
            self._refresh_editors(model)
            self._clear_validation()
        finally:
            self._updating = False

    def _apply_rebuild(self, model: BaseModel) -> None:
        if self._model is not model:
            return
        self._updating = True
        try:
            self._rebuild_form(model)
            self._clear_validation()
        finally:
            self._updating = False

    def _reload_editors_for_policy(self) -> None:
        if self._model is None:
            return
        model = self._model
        if self._committing:
            QTimer.singleShot(0, lambda current=model: self._apply_rebuild(current))
            return
        self._apply_rebuild(model)

    def _compute_read_only(self, field_name: str, info: FieldInfo) -> bool:
        if field_name in self._editable_overrides:
            return not self._editable_overrides[field_name]
        if _field_read_only(info):
            return True
        if self._editable_check is not None and self._model is not None:
            return not self._editable_check(field_name, self._model)
        return False

    def _dispatch_handlers(
        self,
        field_name: str,
        value: Any,
        model: BaseModel,
    ) -> None:
        for handler in self._field_handlers.get(field_name, ()):
            handler(field_name, value, model)
        for handler in self._handlers:
            handler(field_name, value, model)

    def _show_empty(self) -> None:
        self._empty.setText(self._empty_message)
        self._empty.show()
        self._scroll.hide()
        self._error.hide()

    def _clear_form(self) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._editors.clear()

    def _rebuild_form(self, model: BaseModel) -> None:
        self._clear_form()
        for name, info in type(model).model_fields.items():
            editor = self._make_editor(name, info, getattr(model, name))
            self._editors[name] = editor
            text = _field_label(name, info)
            if isinstance(editor, _VecEditor) and editor.suffix:
                text = f"{text} ({editor.suffix})"
            if isinstance(editor, _NumberEditor):
                label = _ScrubLabel(
                    text,
                    editor.spin,
                    self._form_host,
                    px_step=editor.scrub_step(),
                )
                editor.attach_scrub(label)
            else:
                label = QLabel(text, self._form_host)
            if info.description:
                label.setToolTip(info.description)
                editor.setToolTip(info.description)
            self._form.addRow(label, editor)

    def _refresh_editors(self, model: BaseModel) -> None:
        for name, editor in self._editors.items():
            self._set_editor_value(editor, getattr(model, name))

    def _set_editor_value(self, editor: QWidget, value: Any) -> None:
        if isinstance(editor, (_VecEditor, _NumberEditor)):
            editor.set_value(value)
        elif isinstance(editor, _ColorEditor):
            editor.set_value(str(value))
        elif isinstance(editor, PathPicker):
            editor.path = value
        elif isinstance(editor, QCheckBox):
            editor.blockSignals(True)
            editor.setChecked(bool(value))
            editor.blockSignals(False)
        elif isinstance(editor, QComboBox):
            index = editor.findData(value)
            editor.blockSignals(True)
            if index >= 0:
                editor.setCurrentIndex(index)
            editor.blockSignals(False)
        elif isinstance(editor, QLineEdit):
            text = str(value)
            if editor.text() != text:
                editor.blockSignals(True)
                editor.setText(text)
                editor.blockSignals(False)
        elif isinstance(editor, QLabel):
            editor.setText(repr(value))

    def _clear_validation(self) -> None:
        self._error.hide()
        self._error.clear()
        for editor in self._editors.values():
            _polish_invalid(editor, False)

    def _show_validation_error(self, field_name: str, message: str) -> None:
        self._error.setText(message)
        self._error.show()
        editor = self._editors.get(field_name)
        if editor is not None:
            _polish_invalid(editor, True)

    def _commit(self, field_name: str, value: Any) -> None:
        if self._updating or self._model is None:
            return
        self._committing = True
        try:
            try:
                updated = self._model.model_copy(update={field_name: value})
                updated = type(self._model).model_validate(updated.model_dump())
            except ValidationError as exc:
                message = _validation_message(exc)
                self._show_validation_error(field_name, message)
                self.validationFailed.emit(field_name, message)
                self._updating = True
                try:
                    editor = self._editors.get(field_name)
                    if editor is not None:
                        self._set_editor_value(editor, getattr(self._model, field_name))
                finally:
                    self._updating = False
                return
            self._model = updated
            self._clear_validation()
            self.fieldChanged.emit(field_name, value, updated)
            self.modelChanged.emit(updated)
            self._dispatch_handlers(field_name, value, updated)
        finally:
            self._committing = False

    def _preview(self, field_name: str, value: Any) -> None:
        """Emit :attr:`fieldPreviewed` for a validated drag value."""
        if self._updating or self._model is None:
            return
        try:
            updated = self._model.model_copy(update={field_name: value})
            updated = type(self._model).model_validate(updated.model_dump())
        except ValidationError:
            return
        self._committing = True
        try:
            self.fieldPreviewed.emit(field_name, value, updated)
        finally:
            self._committing = False

    def _finish_drag(self, field_name: str, value: Any) -> None:
        """Commit a drag's final value once, or restore the preview if unchanged."""
        if self._updating or self._model is None:
            return
        if value == getattr(self._model, field_name):
            self._preview(field_name, value)
            return
        self._commit(field_name, value)

    def _make_editor(self, name: str, info: FieldInfo, value: Any) -> QWidget:
        read_only = self._compute_read_only(name, info)
        annotation = _unwrap_annotation(info.annotation)
        editor_kind = _field_editor_kind(info)

        if editor_kind == "color" or (
            editor_kind is None and name == "color" and isinstance(value, str)
        ):
            editor = _ColorEditor(self._form_host, read_only=read_only)
            editor.set_value(str(value))
            if not read_only:
                editor.bind(
                    lambda new_value, field=name: self._commit(field, new_value)
                )
            return editor

        if _is_path_type(annotation):
            picker = PathPicker(self._form_host)
            picker.path = value
            picker.setEnabled(not read_only)
            if not read_only:

                def on_path(text: str, field: str = name) -> None:
                    if not text:
                        return
                    self._commit(field, Path(text))

                picker.pathChanged.connect(on_path)
            return picker

        literal = _literal_choices(annotation)
        if literal is not None:
            return self._combo_editor(name, literal, value, read_only=read_only)

        enum_cls = _enum_type(annotation)
        if enum_cls is not None:
            choices = list(enum_cls)

            def enum_label(item: object) -> str:
                return item.name if isinstance(item, Enum) else str(item)

            return self._combo_editor(
                name,
                choices,
                value,
                read_only=read_only,
                label_of=enum_label,
                value_of=lambda item: item,
            )

        tuple_size = _float_tuple_size(annotation)
        if tuple_size is not None and 2 <= tuple_size <= 4:
            lo, hi = _numeric_bounds(info)
            editor = _VecEditor(
                tuple_size,
                self._form_host,
                read_only=read_only,
                minimum=lo,
                maximum=hi,
                unit=_display_unit(info),
            )
            editor.set_value(value)
            if not read_only:
                editor.bind(
                    lambda new_value, field=name: self._commit(field, new_value),
                    lambda new_value, field=name: self._preview(field, new_value),
                    lambda new_value, field=name: self._finish_drag(field, new_value),
                )
            return editor

        if annotation is bool or isinstance(value, bool):
            box = QCheckBox(self._form_host)
            box.setChecked(bool(value))
            box.setEnabled(not read_only)
            if not read_only:
                box.toggled.connect(
                    lambda checked, field=name: self._commit(field, checked)
                )
            return box

        is_int = annotation is int or (
            isinstance(value, int) and not isinstance(value, bool)
        )
        if is_int or annotation is float or isinstance(value, float):
            return self._number_editor(
                name, info, value, is_int=is_int, read_only=read_only
            )

        if annotation is str or isinstance(value, str):
            edit = QLineEdit(self._form_host)
            edit.setText(str(value))
            edit.setReadOnly(read_only)
            if not read_only:
                edit.editingFinished.connect(
                    lambda field=name, widget=edit: self._commit(field, widget.text())
                )
            return edit

        summary = QLabel(repr(value), self._form_host)
        summary.setWordWrap(True)
        summary.setEnabled(False)
        return summary

    def _number_editor(
        self,
        name: str,
        info: FieldInfo,
        value: Any,
        *,
        is_int: bool,
        read_only: bool,
    ) -> QWidget:
        unit = _display_unit(info)
        if is_int:
            # Scaling an int would show fractions; ints only take a suffix.
            unit = _DisplayUnit(suffix=unit.suffix, decimals=0, step=1.0)

        def shown(bound: float | None) -> float | None:
            return None if bound is None else unit.to_display(bound)

        lo, hi = _numeric_bounds(info)
        lo, hi = shown(lo), shown(hi)
        slider_range: tuple[float, float] | None = None
        if _slider_allowed(info):
            soft = _soft_range(info)
            if soft is not None:
                slider_range = (unit.to_display(soft[0]), unit.to_display(soft[1]))
            elif lo is not None and hi is not None and hi > lo:
                slider_range = (lo, hi)
        editor = _NumberEditor(
            self._form_host,
            is_int=is_int,
            unit=unit,
            minimum=lo,
            maximum=hi,
            slider_range=slider_range,
            read_only=read_only,
        )
        editor.set_value(int(value) if is_int else float(value))
        if not read_only:
            editor.bind(
                lambda new_value, field=name: self._commit(field, new_value),
                lambda new_value, field=name: self._preview(field, new_value),
                lambda new_value, field=name: self._finish_drag(field, new_value),
            )
        return editor

    def _combo_editor(
        self,
        name: str,
        choices: Sequence[Any],
        value: Any,
        *,
        read_only: bool,
        label_of: Callable[[Any], str] | None = None,
        value_of: Callable[[Any], Any] | None = None,
    ) -> QWidget:
        label_of = label_of or str
        value_of = value_of or (lambda item: item)
        combo = QComboBox(self._form_host)
        for choice in choices:
            combo.addItem(label_of(choice), value_of(choice))
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.setEnabled(not read_only)
        if not read_only:
            combo.currentIndexChanged.connect(
                lambda _i, field=name, widget=combo: self._commit(
                    field, widget.currentData()
                )
            )
        return combo
