"""Horizontal dual-handle slider for a lower and upper value."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# Same hex values as the tokens at the top of base.qss. This widget paints
# itself, so the stylesheet cannot supply these colors.
_GROOVE = QColor("#2f2f2f")
_GROOVE_BORDER = QColor("#565656")
_SPAN = QColor("#4c8dff")
_HANDLE = QColor("#eeeeee")
_HANDLE_BORDER = QColor("#3e5f8a")

_HANDLE_RADIUS = 7.0
_GROOVE_HEIGHT = 4.0


def _order_bounds(minimum: float, maximum: float) -> tuple[float, float]:
    low = float(minimum)
    high = float(maximum)
    if low > high:
        return high, low
    return low, high


def _clamp_values(
    lower: float,
    upper: float,
    minimum: float,
    maximum: float,
) -> tuple[float, float]:
    low = float(lower)
    high = float(upper)
    if low > high:
        low, high = high, low
    low = min(max(low, minimum), maximum)
    high = min(max(high, minimum), maximum)
    return low, high


class RangeSlider(QWidget):
    """Horizontal slider with a lower handle and an upper handle.

    Values stay ordered: minimum <= lower <= upper <= maximum. The default
    bounds are 0 and 100, and the default values span that whole range.
    ``set_bounds`` and ``set_values`` are silent. A user drag emits
    ``rangeChanged``.

    Signals:
        rangeChanged(float, float): The user dragged a handle. The
            arguments are the lower value and the upper value.

    Args:
        parent: Qt parent. Pass the owning window so lifetime follows Qt
            ownership.
    """

    rangeChanged = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RangeSlider")
        self.setAccessibleName("Range")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._updating = False
        self._minimum = 0.0
        self._maximum = 100.0
        self._lower = 0.0
        self._upper = 100.0
        self._handle: int | None = None

    @property
    def bounds(self) -> tuple[float, float]:
        """Minimum and maximum, in that order."""
        return (self._minimum, self._maximum)

    @property
    def values(self) -> tuple[float, float]:
        """Lower and upper values, in that order."""
        return (self._lower, self._upper)

    def set_bounds(self, minimum: float, maximum: float) -> None:
        """Replace the limits and keep the current values inside them.

        Args:
            minimum: Lower limit. Swapped with ``maximum`` when it is greater.
            maximum: Upper limit.
        """
        minimum, maximum = _order_bounds(minimum, maximum)
        lower, upper = _clamp_values(self._lower, self._upper, minimum, maximum)
        self._updating = True
        try:
            self._minimum = minimum
            self._maximum = maximum
            self._commit(lower, upper)
        finally:
            self._updating = False

    def set_values(self, lower: float, upper: float) -> None:
        """Replace the two values without emitting ``rangeChanged``.

        Args:
            lower: Wanted lower value. Swapped with ``upper`` when it is
                greater, then clamped into the bounds.
            upper: Wanted upper value.
        """
        lower, upper = _clamp_values(lower, upper, self._minimum, self._maximum)
        self._updating = True
        try:
            self._commit(lower, upper)
        finally:
            self._updating = False

    def _drag_to(self, handle: int, value: float) -> None:
        """Move one handle the way a drag does, and emit when it changes.

        Args:
            handle: ``0`` for the lower handle, ``1`` for the upper handle.
            value: Wanted position for that handle. Clamped so the handles
                never cross and both stay inside the bounds.
        """
        value = float(value)
        if handle == 0:
            lower = min(max(value, self._minimum), self._upper)
            upper = self._upper
        else:
            lower = self._lower
            upper = max(min(value, self._maximum), self._lower)
        self._commit(lower, upper)

    def _commit(self, lower: float, upper: float) -> None:
        changed = (lower, upper) != (self._lower, self._upper)
        self._lower = lower
        self._upper = upper
        self.update()
        if changed and not self._updating:
            self.rangeChanged.emit(lower, upper)

    def sizeHint(self) -> QSize:
        return QSize(160, 24)

    def minimumSizeHint(self) -> QSize:
        return QSize(160, 24)

    def _track(self) -> tuple[float, float]:
        pad = _HANDLE_RADIUS + 1.0
        right = max(pad, float(self.width()) - pad)
        return pad, right

    def _x_for(self, value: float) -> float:
        left, right = self._track()
        span = self._maximum - self._minimum
        if span <= 0.0 or right <= left:
            return left
        t = (value - self._minimum) / span
        return left + t * (right - left)

    def _value_at(self, x: float) -> float:
        left, right = self._track()
        span = self._maximum - self._minimum
        if span <= 0.0 or right <= left:
            return self._minimum
        t = min(max((x - left) / (right - left), 0.0), 1.0)
        return self._minimum + t * span

    def _handle_at(self, x: float) -> int:
        lower_distance = abs(x - self._x_for(self._lower))
        upper_distance = abs(x - self._x_for(self._upper))
        if lower_distance <= upper_distance:
            return 0
        return 1

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = self._track()
        mid = self.height() / 2.0
        top = mid - _GROOVE_HEIGHT / 2.0
        painter.setPen(QPen(_GROOVE_BORDER, 1))
        painter.setBrush(_GROOVE)
        painter.drawRoundedRect(
            QRectF(left, top, max(0.0, right - left), _GROOVE_HEIGHT),
            2.0,
            2.0,
        )
        x_lower = self._x_for(self._lower)
        x_upper = self._x_for(self._upper)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_SPAN)
        painter.drawRect(
            QRectF(x_lower, top, max(0.0, x_upper - x_lower), _GROOVE_HEIGHT)
        )
        painter.setPen(QPen(_HANDLE_BORDER, 1))
        painter.setBrush(_HANDLE)
        for center in (x_lower, x_upper):
            painter.drawEllipse(QPointF(center, mid), _HANDLE_RADIUS, _HANDLE_RADIUS)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._handle = self._handle_at(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._handle is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        self._drag_to(self._handle, self._value_at(event.position().x()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._handle is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        handle = self._handle
        self._handle = None
        self._drag_to(handle, self._value_at(event.position().x()))
        event.accept()
