"""Standalone window for :class:`~mtqt.PathPicker`.

Shows a file picker and a directory picker, plus a read-only log of each
``pathChanged`` signal. Launch this instead of the gallery when debugging
path selection on its own.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import PathPicker, apply_stylesheet, ensure_application


class PathPickerWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PathPicker")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        scene = PathPicker(central, caption="Scene file")
        project = PathPicker(central, directory_mode=True, caption="Project folder")

        def log_line(message: str) -> None:
            log.appendPlainText(message)

        scene.pathChanged.connect(
            lambda value: log_line(f"Scene file pathChanged: {value!r}")
        )
        project.pathChanged.connect(
            lambda value: log_line(f"Project folder pathChanged: {value!r}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(scene)
        layout.addWidget(project)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = PathPickerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
