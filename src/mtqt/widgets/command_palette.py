"""Line edit over a ranked list of commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class Command:
    """One command a palette can run.

    Args:
        id: Stable identifier emitted by ``commandTriggered``.
        title: Primary text. Matched and shown in the list.
        detail: Extra match text, shown after the title. Empty when omitted.
    """

    id: str
    title: str
    detail: str = ""


def _best_span(haystack: str, needle: str) -> int | None:
    """Return the tightest in-order span of ``needle`` in ``haystack``.

    The span is the sum of the index gaps between matched characters,
    which is the distance from the first matched character to the last.
    ``None`` means ``needle`` is not a subsequence.
    """
    best: int | None = None
    tight = len(needle) - 1
    for start, char in enumerate(haystack):
        if char != needle[0]:
            continue
        pos = start
        matched = True
        for nxt in needle[1:]:
            found = haystack.find(nxt, pos + 1)
            if found < 0:
                matched = False
                break
            pos = found
        if not matched:
            continue
        span = pos - start
        if best is None or span < best:
            best = span
            if best == tight:
                return best
    return best


def _match_commands(commands: Sequence[Command], query: str) -> tuple[Command, ...]:
    text = query.casefold()
    if text == "":
        return tuple(commands)
    ranked: list[tuple[int, int, Command]] = []
    for index, command in enumerate(commands):
        haystack = f"{command.title} {command.detail}".casefold()
        span = _best_span(haystack, text)
        if span is None:
            continue
        ranked.append((span, index, command))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in ranked)


class CommandPalette(QWidget):
    """Line edit over a ranked list of commands.

    An empty query lists every command in the order passed to
    ``set_commands``. Any other query keeps commands whose title and
    detail contain those characters in order, ignoring case. The score
    is the sum of the index gaps in the tightest match. A lower score
    ranks first, and a tie keeps the original order.

    ``set_commands`` and ``set_query`` are silent. Enter, or activating
    a row, emits ``commandTriggered`` with the command id. Up and Down
    move the current row. Escape clears the query and emits nothing.

    Signals:
        commandTriggered(str): The user ran a command. The argument is
            :attr:`Command.id`.

    Args:
        parent: Qt parent. Pass the owning window so lifetime follows Qt
            ownership.
    """

    commandTriggered = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CommandPalette")
        self.setAccessibleName("Commands")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._updating = False
        self._triggering = False
        self._refresh_queued = False
        self._commands: tuple[Command, ...] = ()
        self._matches: tuple[Command, ...] = ()

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("Command")
        self._edit.setAccessibleName("Command search")
        self._list = QListWidget(self)
        self._list.setAccessibleName("Command list")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit)
        layout.addWidget(self._list, stretch=1)

        self.setFocusProxy(self._edit)
        self._edit.installEventFilter(self)
        self._edit.textChanged.connect(self._on_text_changed)
        self._list.itemActivated.connect(self._on_item_activated)

    @property
    def query(self) -> str:
        """The text in the line edit."""
        return self._edit.text()

    @property
    def matches(self) -> tuple[Command, ...]:
        """Commands that match :attr:`query`, best rank first."""
        return self._matches

    def set_commands(self, commands: Sequence[Command]) -> None:
        """Replace the command list and keep the current query.

        Args:
            commands: Commands to offer, in the order an empty query shows.
        """
        self._commands = tuple(commands)
        self._queue_refresh()

    def set_query(self, query: str) -> None:
        """Replace the line-edit text without emitting ``commandTriggered``.

        Args:
            query: New filter text. An empty string lists every command.
        """
        self._updating = True
        try:
            if self._edit.text() != query:
                self._edit.setText(query)
        finally:
            self._updating = False
        self._queue_refresh()

    def sizeHint(self) -> QSize:
        return QSize(320, 240)

    def _queue_refresh(self) -> None:
        if self._triggering:
            # itemActivated is still on the stack. Rebuilding the list now
            # would delete the row that is delivering the activation.
            if not self._refresh_queued:
                self._refresh_queued = True
                QTimer.singleShot(0, self, self._run_queued_refresh)
            return
        self._refresh()

    def _run_queued_refresh(self) -> None:
        self._refresh_queued = False
        if self._triggering:
            self._queue_refresh()
            return
        self._refresh()

    def _refresh(self) -> None:
        self._matches = _match_commands(self._commands, self.query)
        self._list.clear()
        for command in self._matches:
            if command.detail:
                label = f"{command.title} - {command.detail}"
            else:
                label = command.title
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, command.id)
            self._list.addItem(item)
        if self._matches:
            self._list.setCurrentRow(0)

    def _on_text_changed(self, _text: str) -> None:
        if self._updating:
            return
        self._queue_refresh()

    def _move(self, delta: int) -> None:
        count = self._list.count()
        if count == 0:
            return
        row = self._list.currentRow()
        if row < 0:
            row = 0 if delta > 0 else count - 1
        else:
            row = min(max(row + delta, 0), count - 1)
        self._list.setCurrentRow(row)

    def _emit_row(self, row: int) -> None:
        if row < 0 or row >= len(self._matches):
            return
        command_id = self._matches[row].id
        self._triggering = True
        try:
            self.commandTriggered.emit(command_id)
        finally:
            self._triggering = False

    def _trigger_current(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            row = 0
        self._emit_row(row)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        self._emit_row(self._list.row(item))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self._edit or event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if not isinstance(event, QKeyEvent):
            return super().eventFilter(watched, event)
        key = event.key()
        if key == Qt.Key.Key_Down:
            self._move(1)
            return True
        if key == Qt.Key.Key_Up:
            self._move(-1)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._trigger_current()
            return True
        if key == Qt.Key.Key_Escape:
            self.set_query("")
            return True
        return super().eventFilter(watched, event)
