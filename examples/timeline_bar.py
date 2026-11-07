"""Standalone window for :class:`~mtqt.TimelineBar`.

A timer plays a fake four second clip: the bar's time and a counter label
follow it, and every signal the bar emits is logged. Launch this instead of
the gallery when debugging the transport controls on their own.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mtqt import TimelineBar, apply_stylesheet, ensure_application

CLIPS = {"Walk": 4.0, "Run": 1.5, "Idle": 10.0}


class TimelineBarWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TimelineBar")
        self.resize(720, 360)
        self._time = 0.0
        self._speed = 1.0
        self._loop = True
        self._last = time.perf_counter()

        central = QWidget(self)
        self._readout = QLabel("0.00 s", central)
        self._readout.setStyleSheet("font-size: 32px;")
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")
        bar = TimelineBar(central)
        bar.set_clips(list(CLIPS))
        bar.set_duration(CLIPS[bar.clip])

        timer = QTimer(self)
        timer.setInterval(16)

        def log_line(message: str) -> None:
            log.appendPlainText(message)

        def tick() -> None:
            now = time.perf_counter()
            self._time += (now - self._last) * self._speed
            self._last = now
            duration = CLIPS[bar.clip]
            if self._time >= duration:
                if self._loop:
                    self._time %= duration
                else:
                    self._time = duration
                    timer.stop()
                    bar.set_playing(False)
            bar.set_time(self._time)
            self._readout.setText(f"{self._time:.2f} s")

        def on_play(playing: bool) -> None:
            log_line(f"playToggled: {playing}")
            self._last = time.perf_counter()
            timer.start() if playing else timer.stop()

        def on_stop() -> None:
            log_line("stopRequested")
            timer.stop()
            self._time = 0.0
            bar.set_playing(False)
            bar.set_time(0.0)
            self._readout.setText("0.00 s")

        def on_clip(name: str) -> None:
            log_line(f"clipChanged: {name!r}")
            self._time = 0.0
            bar.set_duration(CLIPS[name])
            bar.set_time(0.0)

        def on_scrub(seconds: float) -> None:
            log_line(f"timeScrubbed: {seconds:.2f}")
            self._time = seconds
            self._readout.setText(f"{seconds:.2f} s")

        def on_speed(speed: float) -> None:
            log_line(f"speedChanged: {speed:g}")
            self._speed = speed

        def on_loop(loop: bool) -> None:
            log_line(f"loopToggled: {loop}")
            self._loop = loop

        timer.timeout.connect(tick)
        bar.playToggled.connect(on_play)
        bar.stopRequested.connect(on_stop)
        bar.clipChanged.connect(on_clip)
        bar.timeScrubbed.connect(on_scrub)
        bar.speedChanged.connect(on_speed)
        bar.loopToggled.connect(on_loop)

        layout = QVBoxLayout(central)
        layout.addWidget(self._readout)
        layout.addWidget(log, stretch=1)
        layout.addWidget(bar)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = TimelineBarWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
