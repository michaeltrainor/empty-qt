"""Standalone window for :class:`~mtqt.NodeGraphWidget`.

Builds a three-node graph with one edge, then logs ``currentNodeChanged``
and ``connectionRejected``. This is not the gallery shader. Launch it when
debugging the canvas on its own.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import NodeGraph, NodeGraphWidget, apply_stylesheet, ensure_application
from mtqt.widgets.node_graph import Node, Port, PortDirection


def _sample_graph() -> NodeGraph:
    """Read feeds Grade. Write is left unconnected so a drop can be tried."""
    graph = NodeGraph()
    graph.add_node(
        Node("read", "Read", outputs=(Port("out", PortDirection.OUT, "image"),))
    )
    graph.add_node(
        Node(
            "grade",
            "Grade",
            inputs=(Port("in", PortDirection.IN, "image"),),
            outputs=(Port("out", PortDirection.OUT, "image"),),
        )
    )
    graph.add_node(
        Node("write", "Write", inputs=(Port("in", PortDirection.IN, "image"),))
    )
    graph.connect("read", "out", "grade", "in")
    graph.arrange()
    return graph


class NodeGraphWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NodeGraph")
        self.resize(900, 600)

        central = QWidget(self)
        canvas = NodeGraphWidget(_sample_graph(), central)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Graph log")
        canvas.currentNodeChanged.connect(
            lambda node_id: log.appendPlainText(f"currentNodeChanged: {node_id!r}")
        )
        canvas.connectionRejected.connect(
            lambda message: log.appendPlainText(f"connectionRejected: {message}")
        )

        layout = QVBoxLayout(central)
        layout.addWidget(canvas, stretch=2)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = NodeGraphWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
