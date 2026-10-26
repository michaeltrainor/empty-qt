"""Schema-driven property form built from a pydantic model instance."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

import annotated_types as at
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mtqt.widgets.path_picker import PathPicker

type _EditorCommit = Callable[[Any], None]
type EditableCheck = Callable[[str, BaseModel], bool]
type FieldHandler = Callable[[str, Any, BaseModel], None]


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


def _field_read_only(info: FieldInfo) -> bool:
    if info.frozen:
        return True
    extra = info.json_schema_extra
    if isinstance(extra, dict) and extra.get("mtqt_read_only"):
        return True
    return False


def _field_editor_kind(info: FieldInfo) -> str | None:
    """Return ``mtqt_editor`` from ``Field(json_schema_extra=...)``, if any."""
    extra = info.json_schema_extra
    if isinstance(extra, dict):
        kind = extra.get("mtqt_editor")
        if isinstance(kind, str):
            return kind
    return None


def _numeric_bounds(info: FieldInfo) -> tuple[float | None, float | None]:
    """Return ``(minimum, maximum)`` from pydantic ``ge`` / ``le`` / ``gt`` / ``lt``."""
    lo: float | None = None
    hi: float | None = None
    for item in info.metadata:
        if isinstance(item, at.Ge):
            lo = float(item.ge)
        elif isinstance(item, at.Gt):
            lo = float(item.gt)
        elif isinstance(item, at.Le):
            hi = float(item.le)
        elif isinstance(item, at.Lt):
            hi = float(item.lt)
    return lo, hi


def _apply_spin_bounds(
    spin: QSpinBox | QDoubleSpinBox,
    info: FieldInfo,
    *,
    default_lo: float,
    default_hi: float,
) -> None:
    lo, hi = _numeric_bounds(info)
    minimum = default_lo if lo is None else lo
    maximum = default_hi if hi is None else hi
    if isinstance(spin, QSpinBox):
        spin.setRange(int(minimum), int(maximum))
    else:
        spin.setRange(float(minimum), float(maximum))


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


class _VecEditor(QWidget):
    """Row of spin boxes for a fixed-length float tuple."""

    def __init__(
        self,
        size: int,
        parent: QWidget | None = None,
        *,
        read_only: bool = False,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesVecEditor")
        self._spins: list[QDoubleSpinBox] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        labels = ("X", "Y", "Z", "W")
        lo = -1_000_000.0 if minimum is None else minimum
        hi = 1_000_000.0 if maximum is None else maximum
        for index in range(size):
            spin = QDoubleSpinBox(self)
            spin.setRange(lo, hi)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setReadOnly(read_only)
            spin.setKeyboardTracking(False)
            if index < len(labels):
                spin.setPrefix(f"{labels[index]} ")
            layout.addWidget(spin)
            self._spins.append(spin)

    def value(self) -> tuple[float, ...]:
        """Return the current float tuple."""
        return tuple(spin.value() for spin in self._spins)

    def set_value(self, values: Sequence[float]) -> None:
        """Load values into the spin boxes without emitting commits."""
        for spin, value in zip(self._spins, values, strict=True):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

    def bind(self, commit: _EditorCommit) -> None:
        """Call ``commit`` with the tuple whenever a spin changes."""

        def on_change(_value: float = 0.0) -> None:
            commit(self.value())

        for spin in self._spins:
            spin.valueChanged.connect(on_change)


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
        self._swatch.setFixedSize(28, 24)
        self._swatch.setEnabled(not read_only)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
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
    ``tuple[float, ...]`` (2–4), and color strings marked with
    ``Field(json_schema_extra={"mtqt_editor": "color"})``. Unsupported
    fields render as a read-only summary.

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
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
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
            label = QLabel(_field_label(name, info), self._form_host)
            if info.description:
                label.setToolTip(info.description)
                editor.setToolTip(info.description)
            self._form.addRow(label, editor)

    def _refresh_editors(self, model: BaseModel) -> None:
        for name, editor in self._editors.items():
            self._set_editor_value(editor, getattr(model, name))

    def _set_editor_value(self, editor: QWidget, value: Any) -> None:
        if isinstance(editor, _VecEditor):
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
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            editor.blockSignals(True)
            editor.setValue(value)
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
            )
            editor.set_value(value)
            if not read_only:
                editor.bind(
                    lambda new_value, field=name: self._commit(field, new_value)
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
        if is_int:
            spin = QSpinBox(self._form_host)
            _apply_spin_bounds(
                spin,
                info,
                default_lo=-1_000_000_000,
                default_hi=1_000_000_000,
            )
            spin.setValue(int(value))
            spin.setReadOnly(read_only)
            spin.setKeyboardTracking(False)
            if not read_only:
                spin.valueChanged.connect(
                    lambda new_value, field=name: self._commit(field, new_value)
                )
            return spin

        if annotation is float or isinstance(value, float):
            spin = QDoubleSpinBox(self._form_host)
            _apply_spin_bounds(
                spin,
                info,
                default_lo=-1_000_000.0,
                default_hi=1_000_000.0,
            )
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setValue(float(value))
            spin.setReadOnly(read_only)
            spin.setKeyboardTracking(False)
            if not read_only:
                spin.valueChanged.connect(
                    lambda new_value, field=name: self._commit(field, new_value)
                )
            return spin

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
