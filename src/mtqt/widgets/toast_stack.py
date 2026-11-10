"""Vertical stack of transient messages, newest on top."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import Qt, QTimer, Signal
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
        # A frame skips a stylesheet background unless this is set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        row = Toast(text, level, self)
        row.dismiss_requested.connect(lambda toast_id=toast_id: self.dismiss(toast_id))
        if timeout_ms > 0:
            timer = QTimer(row)
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda toast_id=toast_id: self._dismiss_after_timeout(toast_id)
            )
            timer.start(timeout_ms)
        self._rows.insert(0, _Row(toast_id, row))
        self._layout.insertWidget(0, row)
        return toast_id

    def _dismiss_after_timeout(self, toast_id: str) -> None:
        # The row owns the timer. Deleting the row inside the timeout
        # callback frees that timer before the callback returns.
        QTimer.singleShot(0, self, lambda toast_id=toast_id: self.dismiss(toast_id))

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
        for timer in widget.findChildren(QTimer):
            # A timer that already fired cannot be deleteLater'd here. The
            # offscreen platform crashes on the next processEvents. Park it
            # on the stack until the stack is destroyed. A timer that has
            # not fired yet can be deleted with the row.
            fired = not timer.isActive()
            timer.stop()
            if fired:
                timer.setParent(self)
            else:
                timer.deleteLater()
        self._layout.removeWidget(widget)
        widget.hide()
        widget.deleteLater()
        self.toastDismissed.emit(toast_id)
