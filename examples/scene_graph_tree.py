"""Standalone window for :class:`~mtqt.SceneGraphTree`.

Shows a small nested forest and a read-only log of ``currentNameChanged``.
Launch this instead of the gallery when debugging the scene tree on its own.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import SceneGraphTree, apply_stylesheet, ensure_application


class SceneGraphTreeWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SceneGraphTree")
        self.resize(640, 480)

        central = QWidget(self)
        forest = [["world", ["camera", "geo"]], ["light"]]
        tree = SceneGraphTree(forest, central)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Selection log")
        tree.currentNameChanged.connect(
            lambda name: log.appendPlainText(f"currentNameChanged: {name!r}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(tree, stretch=2)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = SceneGraphTreeWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
