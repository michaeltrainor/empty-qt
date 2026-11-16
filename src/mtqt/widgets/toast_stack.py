"""Vertical stack of transient messages, newest on top."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ToastLevel(StrEnum):
    """How urgently a toast should read.

    The values are the QSS ``level`` property: ``info``, ``warning``,
    and ``error``.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Toast(QFrame):
    """One message row inside a :class:`ToastStack`.

    The object name is ``Toast`` and the dynamic property ``level`` is
    the :class:`ToastLevel` value, so a stylesheet can color the row.

    Args:
        text: Message shown in the row.
        level: ``info``, ``warning``, or ``error``.
        parent: Owning stack.
    """

    dismiss_requested = Signal()

    def __init__(self, text: str, level: ToastLevel, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("level", level.value)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

        message = QLabel(text, self)
        message.setWordWrap(True)
        close = QToolButton(self)
        close.setText("x")
        close.setAutoRaise(True)
        close.setAccessibleName("Dismiss")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(8)
        layout.addWidget(message, stretch=1)
        layout.addWidget(close)
        close.clicked.connect(self._on_close)

    def _on_close(self) -> None:
        self.dismiss_requested.emit()


@dataclass
class _Row:
    toast_id: str
    widget: Toast
    timer: QTimer | None = None


class ToastStack(QWidget):
    """A vertical stack of transient messages. The newest row is on top.

    ``show_toast`` returns a new id. A positive ``timeout_ms`` dismisses
    that row from a timer parented to it. ``timeout_ms`` of 0 or less
    stays until ``dismiss``. ``dismiss`` of an unknown id does nothing,
    and a second dismiss does not emit again.

    Signals:
        toastDismissed(str): A toast actually went away, from the close
            button or from its timer. The argument is the toast id.

    Args:
        parent: Qt parent. Pass the owning window so lifetime follows Qt
            ownership.
    """

    toastDismissed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToastStack")
        self.setAccessibleName("Toasts")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._next_id = 0
        self._rows: list[_Row] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)

    @property
    def toasts(self) -> tuple[str, ...]:
        """Toast ids, newest first."""
        return tuple(row.toast_id for row in self._rows)

    def show_toast(
        self,
        text: str,
        *,
        level: ToastLevel = ToastLevel.INFO,
        timeout_ms: int = 4000,
    ) -> str:
        """Show one message at the top of the stack.

        Args:
            text: Message to show.
            level: ``info``, ``warning``, or ``error``. Defaults to info.
            timeout_ms: Milliseconds before the toast dismisses itself.
                ``0`` or less keeps it until :meth:`dismiss`.

        Returns:
            The new toast id.
        """
        self._next_id += 1
        toast_id = str(self._next_id)
        row_widget = Toast(text, level, self)
        row_widget.dismiss_requested.connect(
            lambda toast_id=toast_id: self.dismiss(toast_id)
        )
        timer: QTimer | None = None
        if timeout_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda toast_id=toast_id: self.dismiss(toast_id))
            timer.start(timeout_ms)
        self._rows.insert(0, _Row(toast_id, row_widget, timer))
        self._layout.insertWidget(0, row_widget)
        return toast_id

    def dismiss(self, toast_id: str) -> None:
        """Remove one toast. An unknown id does nothing.

        Args:
            toast_id: Id returned by :meth:`show_toast`.
        """
        index = next(
            (i for i, row in enumerate(self._rows) if row.toast_id == toast_id),
            None,
        )
        if index is None:
            return
        row = self._rows.pop(index)
        widget = row.widget
        # deleteLater on a timer that has already fired segfaults in the next
        # offscreen processEvents. That timer stays parented to the stack.
        if row.timer is not None and row.timer.isActive():
            row.timer.stop()
            row.timer.deleteLater()
        self._layout.removeWidget(widget)
        widget.hide()
        widget.deleteLater()
        self.toastDismissed.emit(toast_id)
