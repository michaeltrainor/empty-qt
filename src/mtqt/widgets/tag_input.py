"""One row of tag chips and a line edit."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)


def _normalize(tags: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return tuple(result)


class _TagChip(QFrame):
    """One tag and its remove button."""

    remove_requested = Signal(str)

    def __init__(self, label: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TagChip")
        self._label = label
        # A plain frame skips a stylesheet background unless this is set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        text = QLabel(label, self)
        remove = QToolButton(self)
        remove.setText("x")
        remove.setAutoRaise(True)
        remove.setAccessibleName(f"Remove {label}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 2, 0)
        layout.setSpacing(0)
        layout.addWidget(text)
        layout.addWidget(remove)
        remove.clicked.connect(self._on_remove)

    def _on_remove(self) -> None:
        self.remove_requested.emit(self._label)


class TagInput(QFrame):
    """A horizontal row of tag chips followed by a line edit.

    ``set_tags`` strips whitespace, drops empty strings, and drops
    duplicates, keeping the first occurrence. It does not emit.
    Enter or a comma commits the draft. Backspace on an empty draft
    removes the last tag. Each chip has a remove button. A commit that
    changes nothing does not emit.

    Signals:
        tagsChanged(object): The user committed or removed a tag. The
            argument is the new ``tuple`` of tags.

    Args:
        parent: Qt parent. Pass the owning window so lifetime follows Qt
            ownership.
    """

    tagsChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TagInput")
        self.setAccessibleName("Tags")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._updating = False
        self._tags: tuple[str, ...] = ()

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("Add a tag")
        self._edit.setAccessibleName("Tag")
        self._edit.setFrame(False)

        self._suggestions = QStringListModel(self)
        self._completer = QCompleter(self._suggestions, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._edit.setCompleter(self._completer)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._layout.addWidget(self._edit, stretch=1)

        self._edit.installEventFilter(self)
        self._edit.returnPressed.connect(self._commit_draft)
        self._edit.textChanged.connect(self._on_text_changed)

    @property
    def tags(self) -> tuple[str, ...]:
        """Current tags, first-seen order, with no blanks or duplicates."""
        return self._tags

    def set_tags(self, tags: Sequence[str]) -> None:
        """Replace the tags without emitting ``tagsChanged``.

        Args:
            tags: New tags. Whitespace is stripped, and empty or repeated
                values are dropped. The first occurrence wins.
        """
        self._tags = _normalize(tags)
        self._rebuild_chips()

    def set_suggestions(self, suggestions: Sequence[str]) -> None:
        """Offer completion strings for the line edit.

        Args:
            suggestions: Values the popup completer can insert.
        """
        self._suggestions.setStringList(list(suggestions))

    def _set_draft(self, text: str) -> None:
        self._updating = True
        try:
            if self._edit.text() != text:
                self._edit.setText(text)
        finally:
            self._updating = False

    def _commit_draft(self) -> None:
        draft = self._edit.text().strip()
        self._set_draft("")
        if not draft or draft in self._tags:
            return
        self._tags = (*self._tags, draft)
        self._rebuild_chips()
        self.tagsChanged.emit(self._tags)

    def _on_text_changed(self, text: str) -> None:
        if self._updating or "," not in text:
            return
        parts = text.split(",")
        changed = False
        for part in parts[:-1]:
            tag = part.strip()
            if tag and tag not in self._tags:
                self._tags = (*self._tags, tag)
                changed = True
        self._set_draft(parts[-1])
        if changed:
            self._rebuild_chips()
            self.tagsChanged.emit(self._tags)

    def _remove_tag(self, label: str) -> None:
        if label not in self._tags:
            return
        self._tags = tuple(tag for tag in self._tags if tag != label)
        self._rebuild_chips()
        self.tagsChanged.emit(self._tags)

    def _remove_last(self) -> None:
        if not self._tags:
            return
        self._remove_tag(self._tags[-1])

    def _on_chip_remove(self, label: str) -> None:
        # The remove button is still inside its clicked slot. Delete the
        # chip on the next turn.
        QTimer.singleShot(0, self, lambda label=label: self._remove_tag(label))

    def _rebuild_chips(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for index, tag in enumerate(self._tags):
            chip = _TagChip(tag, self)
            chip.remove_requested.connect(self._on_chip_remove)
            self._layout.insertWidget(index, chip)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self._edit or event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if not isinstance(event, QKeyEvent):
            return super().eventFilter(watched, event)
        if (
            event.key() == Qt.Key.Key_Backspace
            and self._edit.text() == ""
            and self._tags
        ):
            self._remove_last()
            return True
        return super().eventFilter(watched, event)
