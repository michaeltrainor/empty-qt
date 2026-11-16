"""Standalone window for :class:`~mtqt.BreadcrumbBar`.

Shows an asset path and a read-only log of ``crumbActivated``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import BreadcrumbBar, Crumb, apply_stylesheet, ensure_application


class BreadcrumbBarWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BreadcrumbBar")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        bar = BreadcrumbBar(central)
        bar.set_crumbs(
            (
                Crumb("assets", "Assets"),
                Crumb("characters", "Characters"),
                Crumb("hero", "Hero"),
                Crumb("body", "Body"),
            )
        )
        bar.crumbActivated.connect(
            lambda key: log.appendPlainText(f"crumbActivated: {key}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(bar)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = BreadcrumbBarWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
