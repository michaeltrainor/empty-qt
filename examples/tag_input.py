"""Standalone window for :class:`~mtqt.TagInput`.

Shows a tag row with a few suggestions and a read-only log of
``tagsChanged``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import TagInput, apply_stylesheet, ensure_application


class TagInputWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TagInput")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        tags = TagInput(central)
        tags.set_suggestions(("hero", "prop", "camera", "light"))
        tags.tagsChanged.connect(
            lambda value: log.appendPlainText(f"tagsChanged: {value}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(tags)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = TagInputWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
