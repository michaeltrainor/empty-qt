"""Main window with a scene graph beside a rendercanvas viewport.

The tree and the viewport share the window. Selecting a name does not
change what is drawn.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QSplitter, QWidget
from rendercanvas.qt import QRenderWidget
from rendercanvas.utils.cube import setup_drawing_sync

from mtqt import SceneGraphTree, apply_stylesheet, ensure_application


class SceneViewportWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scene viewport")
        self.resize(960, 600)

        splitter = QSplitter(self)
        data = Path(__file__).resolve().parent / "data" / "scene_graph.json"
        tree = SceneGraphTree(parent=splitter, path=data)
        tree.setMinimumWidth(220)
        self._canvas = QRenderWidget(splitter, update_mode="continuous")
        splitter.addWidget(tree)
        splitter.addWidget(self._canvas)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 680])
        self.setCentralWidget(splitter)
        apply_stylesheet(self, "base", "widgets")

        draw = setup_drawing_sync(self._canvas)
        self._canvas.request_draw(draw)


def main() -> int:
    app = ensure_application()
    window = SceneViewportWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
