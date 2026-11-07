"""Transport controls and a scrub slider for clip playback."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QStyle,
    QToolButton,
    QWidget,
)


class TimelineBar(QWidget):
    """Play / pause, stop, loop, clip picker, scrub slider, time, and speed.

    The bar knows nothing about what it plays. A host fills the clip list,
    sets the duration and the current time, and reacts to the signals.
    The setters never emit: :meth:`set_time` runs on every playback tick,
    so it blocks the slider's signals and leaves the slider alone while
    the user is dragging it. Times are seconds; the slider works in
    milliseconds. The widget sets ``objectName`` ``TimelineBar`` for QSS.

    Signals:
        playToggled(bool): The play button was pressed; ``True`` to play.
        stopRequested(): The stop button was pressed.
        loopToggled(bool): The loop button was toggled.
        speedChanged(float): A playback rate was picked, such as ``2.0``.
        clipChanged(str): A clip was picked in the combo box.
        timeScrubbed(float): The user moved the slider, in seconds.

    Args:
        parent: Qt parent. Pass the owning window so lifetime follows Qt
            ownership.
        speeds: Rates offered in the speed box; ``1.0`` is picked first.
    """

    playToggled = Signal(bool)
    stopRequested = Signal()
    loopToggled = Signal(bool)
    speedChanged = Signal(float)
    clipChanged = Signal(str)
    timeScrubbed = Signal(float)

    #: Default playback rates, in order of appearance.
    SPEEDS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        speeds: Sequence[float] = SPEEDS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TimelineBar")
        self.setAccessibleName("Timeline")
        self._updating = False
        self._duration = 0.0

        style = self.style()
        self._play = QToolButton(self)
        self._play.setCheckable(True)
        self._play.setToolTip("Play / pause")
        self._play.setAccessibleName("Play")
        self._play_icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._pause_icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        self._play.setIcon(self._play_icon)

        self._stop = QToolButton(self)
        self._stop.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self._stop.setToolTip("Stop and rewind")
        self._stop.setAccessibleName("Stop")

        self._loop = QToolButton(self)
        self._loop.setCheckable(True)
        self._loop.setChecked(True)
        self._loop.setText("Loop")
        self._loop.setToolTip("Start over at the end")

        self._clips = QComboBox(self)
        self._clips.setToolTip("Clip")
        self._clips.setAccessibleName("Clip")
        self._clips.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._clips.setMinimumContentsLength(12)

        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(0, 0)
        self._slider.setSingleStep(10)
        self._slider.setPageStep(1000)
        self._slider.setAccessibleName("Time")

        self._time = QLabel(self)
        self._time.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._time.setMinimumWidth(
            self._time.fontMetrics().horizontalAdvance("0000.00 / 0000.00 s")
        )

        self._speed = QComboBox(self)
        self._speed.setToolTip("Playback speed")
        self._speed.setAccessibleName("Speed")
        for rate in speeds:
            self._speed.addItem(f"{rate:g}x", float(rate))
        index = self._speed.findData(1.0)
        self._speed.setCurrentIndex(index if index >= 0 else 0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)
        layout.addWidget(self._play)
        layout.addWidget(self._stop)
        layout.addWidget(self._loop)
        layout.addWidget(self._clips)
        layout.addWidget(self._slider, stretch=1)
        layout.addWidget(self._time)
        layout.addWidget(self._speed)

        self._play.toggled.connect(self._on_play_toggled)
        self._stop.clicked.connect(self._on_stop_clicked)
        self._loop.toggled.connect(self._on_loop_toggled)
        self._clips.currentIndexChanged.connect(self._on_clip_index)
        self._slider.valueChanged.connect(self._on_slider_value)
        self._speed.currentIndexChanged.connect(self._on_speed_index)
        self._refresh_time_label()
        self._set_enabled(False)

    # -- state -----------------------------------------------------------

    @property
    def clip(self) -> str:
        """The clip shown in the combo box, or ``""`` when it is empty."""
        return self._clips.currentText()

    @property
    def clips(self) -> list[str]:
        """Clip names in the combo box."""
        return [self._clips.itemText(i) for i in range(self._clips.count())]

    @property
    def duration(self) -> float:
        """Slider length in seconds."""
        return self._duration

    @property
    def time(self) -> float:
        """Slider position in seconds."""
        return self._slider.value() / 1000.0

    @property
    def playing(self) -> bool:
        """Whether the play button shows the paused (pressed) state."""
        return self._play.isChecked()

    @property
    def loop(self) -> bool:
        """Whether the loop button is pressed."""
        return self._loop.isChecked()

    @property
    def speed(self) -> float:
        """The rate picked in the speed box."""
        data = self._speed.currentData()
        return float(data) if data is not None else 1.0

    # -- setters (never emit) -------------------------------------------

    def set_clips(self, names: Sequence[str], current: str | None = None) -> None:
        """Replace the clip list.

        Args:
            names: Clip names to offer. An empty list disables the bar.
            current: Clip to show, when it is in ``names``. Otherwise the
                previously shown clip stays if still present, else the first.
        """
        previous = self.clip
        self._updating = True
        try:
            self._clips.clear()
            self._clips.addItems(list(names))
            wanted = current if current in names else previous
            index = self._clips.findText(wanted) if wanted else -1
            self._clips.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._updating = False
        self._set_enabled(bool(names))

    def set_duration(self, seconds: float) -> None:
        """Set the slider length.

        Args:
            seconds: Clip length; the slider's position is clamped to it.
        """
        self._duration = max(0.0, float(seconds))
        self._updating = True
        try:
            self._slider.setMaximum(round(self._duration * 1000.0))
        finally:
            self._updating = False
        self._refresh_time_label()

    def set_time(self, seconds: float) -> None:
        """Move the slider and time label without emitting ``timeScrubbed``.

        While the user holds the slider the handle is left where it is, so
        a playback tick does not fight the drag; the label still updates.

        Args:
            seconds: Playhead position.
        """
        if not self._slider.isSliderDown():
            self._updating = True
            try:
                self._slider.setValue(round(float(seconds) * 1000.0))
            finally:
                self._updating = False
        self._refresh_time_label(seconds)

    def set_playing(self, playing: bool) -> None:
        """Show the play button pressed (pause icon) or released.

        Args:
            playing: ``True`` while the host is playing.
        """
        self._updating = True
        try:
            self._play.setChecked(playing)
        finally:
            self._updating = False

    def set_loop(self, loop: bool) -> None:
        """Show the loop button pressed or released without emitting.

        Args:
            loop: Whether playback wraps at the end.
        """
        self._updating = True
        try:
            self._loop.setChecked(loop)
        finally:
            self._updating = False

    def set_speed(self, speed: float) -> None:
        """Pick the closest offered rate without emitting.

        Args:
            speed: Wanted playback rate.
        """
        best = min(
            range(self._speed.count()),
            key=lambda i: abs(float(self._speed.itemData(i)) - speed),
            default=-1,
        )
        if best < 0:
            return
        self._updating = True
        try:
            self._speed.setCurrentIndex(best)
        finally:
            self._updating = False

    # -- internals -------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self._play,
            self._stop,
            self._loop,
            self._clips,
            self._slider,
            self._speed,
        ):
            widget.setEnabled(enabled)

    def _refresh_time_label(self, seconds: float | None = None) -> None:
        current = self.time if seconds is None else max(0.0, float(seconds))
        self._time.setText(f"{current:.2f} / {self._duration:.2f} s")

    def _on_play_toggled(self, checked: bool) -> None:
        self._play.setIcon(self._pause_icon if checked else self._play_icon)
        if not self._updating:
            self.playToggled.emit(checked)

    def _on_stop_clicked(self) -> None:
        self.stopRequested.emit()

    def _on_loop_toggled(self, checked: bool) -> None:
        if not self._updating:
            self.loopToggled.emit(checked)

    def _on_clip_index(self, index: int) -> None:
        if self._updating or index < 0:
            return
        self.clipChanged.emit(self._clips.itemText(index))

    def _on_slider_value(self, value: int) -> None:
        if self._updating:
            return
        self._refresh_time_label(value / 1000.0)
        self.timeScrubbed.emit(value / 1000.0)

    def _on_speed_index(self, index: int) -> None:
        if self._updating or index < 0:
            return
        self.speedChanged.emit(float(self._speed.itemData(index)))
