"""Standalone window for :class:`~mtqt.LabeledLineEdit`.

Shows a required name field, an optional notes field, and a read-only log of
``textChanged`` and ``validityChanged``. Launch this instead of the gallery
when debugging the composite line edit on its own.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import LabeledLineEdit, apply_stylesheet, ensure_application


class LabeledLineEditWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LabeledLineEdit")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        name = LabeledLineEdit("Name", central, placeholder="Ada")
        notes = LabeledLineEdit(
            "Notes",
            central,
            required=False,
            placeholder="optional",
        )

        def log_line(message: str) -> None:
            log.appendPlainText(message)

        name.textChanged.connect(lambda text: log_line(f"Name textChanged: {text!r}"))
        name.validityChanged.connect(lambda ok: log_line(f"Name validityChanged: {ok}"))
        notes.textChanged.connect(lambda text: log_line(f"Notes textChanged: {text!r}"))
        notes.validityChanged.connect(
            lambda ok: log_line(f"Notes validityChanged: {ok}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(name)
        layout.addWidget(notes)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = LabeledLineEditWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
