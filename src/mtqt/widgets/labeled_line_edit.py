"""Composite labeled line edit: composition, signals, and QSS state."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget


class LabeledLineEdit(QWidget):
    """A ``QLabel`` plus ``QLineEdit`` composed in one widget.

    Prefer composition like this over multiple inheritance. The widget sets
    ``objectName`` so QSS can target ``LabeledLineEdit`` and an ``invalid``
    dynamic property for validation styling.

    Args:
        label: Text shown to the left of the field.
        parent: Qt parent. Pass the owning window or layout widget so
            lifetime follows Qt ownership. ``None`` means Python owns this
            widget until it is parented or goes out of scope.
        placeholder: Placeholder text for the line edit.
        required: When true, empty text is treated as invalid.
    """

    textChanged = Signal(str)
    validityChanged = Signal(bool)

    def __init__(
        self,
        label: str = "",
        parent: QWidget | None = None,
        *,
        placeholder: str = "",
        required: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LabeledLineEdit")
        self.setAccessibleName(label or "Labeled line edit")
        self._required = required

        self._label = QLabel(label, self)
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(placeholder)
        self._label.setBuddy(self._edit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        layout.addWidget(self._edit, stretch=1)

        self._edit.textChanged.connect(self._on_text_changed)
        self._apply_validity(self.is_valid)

    @property
    def text(self) -> str:
        """Current line-edit contents."""
        return self._edit.text()

    @text.setter
    def text(self, value: str) -> None:
        self._edit.setText(value)

    @property
    def is_valid(self) -> bool:
        """Whether the current text passes the simple required check."""
        if not self._required:
            return True
        return bool(self.text.strip())

    def _on_text_changed(self, value: str) -> None:
        self.textChanged.emit(value)
        self._apply_validity(self.is_valid)

    def _apply_validity(self, valid: bool) -> None:
        invalid = not valid
        previous = self.property("invalid")
        self.setProperty("invalid", invalid)
        style = self.style()
        if style is not None and previous != invalid:
            style.unpolish(self)
            style.polish(self)
        self.validityChanged.emit(valid)
