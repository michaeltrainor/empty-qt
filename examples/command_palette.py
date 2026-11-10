"""Standalone window for :class:`~mtqt.CommandPalette`.

Shows a few tool commands and a read-only log of ``commandTriggered``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import Command, CommandPalette, apply_stylesheet, ensure_application


class CommandPaletteWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CommandPalette")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        palette = CommandPalette(central)
        palette.set_commands(
            (
                Command("open-scene", "Open Scene", "Load a scene file"),
                Command("save-scene", "Save Scene", "Write the current scene"),
                Command("preferences", "Preferences", "Edit tool settings"),
                Command("frame-all", "Frame All", "Fit the view"),
            )
        )
        palette.commandTriggered.connect(
            lambda command_id: log.appendPlainText(f"commandTriggered: {command_id}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(palette, stretch=1)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = CommandPaletteWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
