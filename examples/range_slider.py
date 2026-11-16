"""Standalone window for :class:`~mtqt.RangeSlider`.

Shows a dual-handle slider and a read-only log of ``rangeChanged``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import RangeSlider, apply_stylesheet, ensure_application


class RangeSliderWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("RangeSlider")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        slider = RangeSlider(central)
        slider.set_values(20, 80)

        slider.rangeChanged.connect(
            lambda lower, upper: log.appendPlainText(f"rangeChanged: {lower}, {upper}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(slider)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = RangeSliderWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
