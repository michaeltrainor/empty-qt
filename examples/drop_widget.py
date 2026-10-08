"""Standalone window for :class:`~mtqt.DropWidget`.

Hosts the drop target in a main window with a short hint, so the widget can
be launched from ``examples/`` as well as from its module ``main``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from mtqt import DropWidget, apply_stylesheet, ensure_application


class DropWidgetWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DropWidget")
        self.resize(480, 360)

        central = QWidget(self)
        hint = QLabel("Drop files onto the dashed area.", central)
        drop = DropWidget(central)

        layout = QVBoxLayout(central)
        layout.addWidget(hint)
        layout.addWidget(drop, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = DropWidgetWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
