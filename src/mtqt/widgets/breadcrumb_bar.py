"""Clickable path segments in one horizontal row."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)


@dataclass(frozen=True)
class Crumb:
    """One segment of a breadcrumb path.

    Args:
        key: Stable identifier emitted by ``crumbActivated``.
        label: Text shown on the segment button.
    """

    key: str
    label: str


def _polish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class BreadcrumbBar(QWidget):
    """Clickable path segments in a horizontally scrolling row.

    ``set_crumbs`` rebuilds the row and does not emit. The last segment
    has the dynamic property ``current`` set, so QSS can draw it as the
    current location. Chevrons between segments are labels, not buttons.
    Clicking a segment emits ``crumbActivated`` with that crumb's key
    and does not rebuild. If ``set_crumbs`` runs while that signal is
    still emitting, the rebuild waits until the click has returned.

    Signals:
        crumbActivated(str): The user clicked a segment. The argument is
            :attr:`Crumb.key`.

    Args:
        parent: Qt parent. Pass the owning window so lifetime follows Qt
            ownership.
    """

    crumbActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BreadcrumbBar")
        self.setAccessibleName("Breadcrumb")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(32)
        self._crumbs: tuple[Crumb, ...] = ()
        self._emitting = False
        self._rebuild_queued = False

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._content = QWidget(self._scroll)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        self._row = QHBoxLayout(self._content)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(2)
        self._scroll.setWidget(self._content)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    @property
    def crumbs(self) -> tuple[Crumb, ...]:
        """The segments currently shown, left to right."""
        return self._crumbs

    def set_crumbs(self, crumbs: Sequence[Crumb]) -> None:
        """Replace the path without emitting ``crumbActivated``.

        Args:
            crumbs: Segments from the root to the current location.
        """
        self._crumbs = tuple(crumbs)
        if self._emitting:
            self._queue_rebuild()
            return
        self._rebuild()

    def _queue_rebuild(self) -> None:
        if self._rebuild_queued:
            return
        self._rebuild_queued = True
        QTimer.singleShot(0, self, self._run_queued_rebuild)

    def _run_queued_rebuild(self) -> None:
        self._rebuild_queued = False
        if self._emitting:
            self._queue_rebuild()
            return
        self._rebuild()

    def _rebuild(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        last = len(self._crumbs) - 1
        for index, crumb in enumerate(self._crumbs):
            if index:
                chevron = QLabel(">", self._content)
                chevron.setObjectName("BreadcrumbChevron")
                self._row.addWidget(chevron)
            button = QPushButton(crumb.label, self._content)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("current", index == last)
            _polish(button)
            button.clicked.connect(
                lambda _checked=False, key=crumb.key: self._activate(key)
            )
            self._row.addWidget(button)
        self._row.addStretch(1)

    def _activate(self, key: str) -> None:
        self._emitting = True
        try:
            self.crumbActivated.emit(key)
        finally:
            self._emitting = False
