"""Standalone gallery that imports mtqt like a production tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListView,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mtqt import (
    DropWidget,
    LabeledLineEdit,
    NodeGraph,
    NodeGraphWidget,
    ObjectListModel,
    ObjectTableModel,
    PathPicker,
    SceneGraphTree,
    apply_stylesheet,
    ensure_application,
)
from mtqt.widgets.node_graph import Node, Port, PortDirection


@dataclass
class Person:
    name: str
    role: str

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


def _in(name: str, data_type: str) -> Port:
    return Port(name, PortDirection.IN, data_type)


def _out(name: str, data_type: str) -> Port:
    return Port(name, PortDirection.OUT, data_type)


def _shader_graph() -> NodeGraph:
    """A small lit-surface shader: constants, maps, and a wide master node."""
    graph = NodeGraph()
    graph.add_node(Node("uv", "UV", outputs=(_out("UV", "uv"),)))
    graph.add_node(Node("time", "Time", outputs=(_out("t", "float"),)))
    graph.add_node(Node("height", "Height", outputs=(_out("Value", "float"),)))
    graph.add_node(Node("depth", "Depth Scale", outputs=(_out("Value", "float"),)))
    graph.add_node(Node("tint", "Tint", outputs=(_out("RGB", "color"),)))
    graph.add_node(Node("metal", "Metal", outputs=(_out("Value", "float"),)))
    graph.add_node(Node("rough", "Roughness Amount", outputs=(_out("Value", "float"),)))
    graph.add_node(Node("power", "Pow", outputs=(_out("x", "float"),)))
    graph.add_node(Node("emission", "Emission", outputs=(_out("RGB", "color"),)))
    graph.add_node(
        Node(
            "parallax",
            "Parallax Occlusion Mapping",
            inputs=(
                _in("UV", "uv"),
                _in("Height", "float"),
                _in("Scale", "float"),
            ),
            outputs=(_out("UV", "uv"),),
        )
    )
    graph.add_node(
        Node(
            "albedo",
            "Sample Albedo",
            inputs=(_in("UV", "uv"),),
            outputs=(_out("RGB", "color"), _out("A", "float")),
        )
    )
    graph.add_node(
        Node(
            "normal",
            "Sample Normal",
            inputs=(_in("UV", "uv"),),
            outputs=(_out("Normal", "vec3"),),
        )
    )
    graph.add_node(
        Node(
            "multiply",
            "Multiply",
            inputs=(_in("A", "color"), _in("B", "color")),
            outputs=(_out("Product", "color"),),
        )
    )
    graph.add_node(
        Node(
            "add",
            "Add",
            inputs=(_in("A", "float"), _in("B", "float")),
            outputs=(_out("Sum", "float"),),
        )
    )
    graph.add_node(
        Node(
            "fresnel",
            "Fresnel",
            inputs=(_in("Normal", "vec3"), _in("Power", "float")),
            outputs=(_out("Mask", "float"),),
        )
    )
    graph.add_node(
        Node(
            "lerp",
            "Lerp",
            inputs=(
                _in("A", "color"),
                _in("B", "color"),
                _in("T", "float"),
            ),
            outputs=(_out("Result", "color"),),
        )
    )
    graph.add_node(
        Node(
            "surface",
            "Lit Surface",
            inputs=(
                _in("Base Color", "color"),
                _in("Metallic", "float"),
                _in("Roughness", "float"),
                _in("Normal", "vec3"),
                _in("Emission", "color"),
                _in("Ambient Occlusion", "float"),
                _in("Opacity", "float"),
            ),
        )
    )
    graph.connect("uv", "UV", "parallax", "UV")
    graph.connect("height", "Value", "parallax", "Height")
    graph.connect("depth", "Value", "parallax", "Scale")
    graph.connect("parallax", "UV", "albedo", "UV")
    graph.connect("parallax", "UV", "normal", "UV")
    graph.connect("albedo", "RGB", "multiply", "A")
    graph.connect("tint", "RGB", "multiply", "B")
    graph.connect("time", "t", "add", "A")
    graph.connect("power", "x", "add", "B")
    graph.connect("normal", "Normal", "fresnel", "Normal")
    graph.connect("add", "Sum", "fresnel", "Power")
    graph.connect("multiply", "Product", "lerp", "A")
    graph.connect("tint", "RGB", "lerp", "B")
    graph.connect("fresnel", "Mask", "lerp", "T")
    graph.connect("lerp", "Result", "surface", "Base Color")
    graph.connect("metal", "Value", "surface", "Metallic")
    graph.connect("rough", "Value", "surface", "Roughness")
    graph.connect("normal", "Normal", "surface", "Normal")
    graph.connect("emission", "RGB", "surface", "Emission")
    graph.connect("albedo", "A", "surface", "Ambient Occlusion")
    graph.connect("albedo", "A", "surface", "Opacity")
    return graph


class GalleryWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mtqt gallery")
        self.resize(1920, 1080)

        self._use_qss = QCheckBox("Use mtqt stylesheet", self)
        self._use_qss.setChecked(True)
        self._use_qss.toggled.connect(self._on_qss_toggled)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_widgets_tab(), "Widgets")
        tabs.addTab(self._build_list_tab(), "List")
        tabs.addTab(self._build_table_tab(), "Table")
        tabs.addTab(self._build_tree_tab(), "Tree")
        tabs.addTab(self._build_drop_tab(), "Drop")
        tabs.addTab(self._build_graph_tab(), "Graph")

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.addWidget(self._use_qss)
        layout.addWidget(tabs)
        self.setCentralWidget(central)

        self._on_qss_toggled(True)

    def _on_qss_toggled(self, checked: bool) -> None:
        if checked:
            apply_stylesheet(self, "base", "widgets")
        else:
            self.setStyleSheet("")

    def _build_widgets_tab(self) -> QWidget:
        page = QWidget(self)
        log = QPlainTextEdit(page)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        name = LabeledLineEdit("Name", page, placeholder="Required")
        path = PathPicker(page, caption="Pick a file")

        def log_line(message: str) -> None:
            log.appendPlainText(message)

        name.textChanged.connect(lambda text: log_line(f"textChanged: {text!r}"))
        name.validityChanged.connect(lambda ok: log_line(f"validityChanged: {ok}"))
        path.pathChanged.connect(lambda value: log_line(f"pathChanged: {value!r}"))

        layout = QVBoxLayout(page)
        layout.addWidget(name)
        layout.addWidget(path)
        layout.addWidget(log, stretch=1)
        return page

    def _build_list_tab(self) -> QWidget:
        page = QWidget(self)
        people = [
            Person("Ada", "TD"),
            Person("Lin", "Pipeline"),
        ]
        model = ObjectListModel(people, parent=page, display_attr="name")
        view = QListView(page)
        view.setModel(model)

        add = QPushButton("Add row", page)
        remove = QPushButton("Remove selected", page)

        def on_add() -> None:
            model.append(Person(f"User {model.rowCount() + 1}", "Artist"))

        def on_remove() -> None:
            indexes = view.selectedIndexes()
            if indexes:
                model.remove_row(indexes[0].row())

        add.clicked.connect(on_add)
        remove.clicked.connect(on_remove)

        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()

        layout = QVBoxLayout(page)
        layout.addWidget(view)
        layout.addLayout(buttons)
        return page

    def _build_table_tab(self) -> QWidget:
        page = QWidget(self)
        people = [
            Person("Ada", "TD"),
            Person("Lin", "Pipeline"),
        ]
        model = ObjectTableModel(people, columns=("name", "role"), parent=page)
        view = QTableView(page)
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.horizontalHeader().setStretchLastSection(True)

        snapshot = QPlainTextEdit(page)
        snapshot.setReadOnly(True)

        def refresh_snapshot() -> None:
            lines = [
                f"{model.row_at(row).name}: {model.row_at(row).role}"  # type: ignore[union-attr]
                for row in range(model.rowCount())
            ]
            snapshot.setPlainText("\n".join(lines))

        model.dataChanged.connect(lambda *_args: refresh_snapshot())
        refresh_snapshot()

        layout = QVBoxLayout(page)
        layout.addWidget(view, stretch=2)
        layout.addWidget(snapshot, stretch=1)
        return page

    def _build_tree_tab(self) -> QWidget:
        page = QWidget(self)
        data_path = Path(__file__).parent / "data" / "scene_graph.json"
        tree = SceneGraphTree(parent=page, path=data_path)

        log = QPlainTextEdit(page)
        log.setReadOnly(True)
        log.setPlaceholderText("Selection log")
        tree.currentNameChanged.connect(
            lambda name: log.appendPlainText(f"currentNameChanged: {name!r}")
        )

        add = QPushButton("Add child", page)
        remove = QPushButton("Remove selected", page)

        def on_add() -> None:
            parent = tree.view.currentIndex()
            count = tree.model.rowCount(parent)
            tree.model.append_child(parent, f"node_{count + 1}")
            tree.view.expand(parent)

        def on_remove() -> None:
            index = tree.view.currentIndex()
            if index.isValid():
                tree.model.remove_row(index)

        add.clicked.connect(on_add)
        remove.clicked.connect(on_remove)

        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()

        layout = QVBoxLayout(page)
        layout.addWidget(tree, stretch=2)
        layout.addLayout(buttons)
        layout.addWidget(log, stretch=1)
        return page

    def _build_graph_tab(self) -> QWidget:
        page = QWidget(self)
        graph = _shader_graph()
        graph.arrange(x_gap=420, y_gap=150)

        canvas = NodeGraphWidget(graph, page)
        log = QPlainTextEdit(page)
        log.setReadOnly(True)
        log.setPlaceholderText("Graph log")
        canvas.currentNodeChanged.connect(
            lambda node_id: log.appendPlainText(f"currentNodeChanged: {node_id!r}")
        )
        canvas.connectionRejected.connect(
            lambda message: log.appendPlainText(f"connectionRejected: {message}")
        )
        canvas.graphChanged.connect(lambda: log.appendPlainText("graphChanged"))

        layout = QVBoxLayout(page)
        layout.addWidget(canvas, stretch=2)
        layout.addWidget(log, stretch=1)
        return page

    def _build_drop_tab(self) -> QWidget:
        page = QWidget(self)
        drop = DropWidget(page)
        layout = QVBoxLayout(page)
        layout.addWidget(drop, stretch=1)
        return page


def main() -> int:
    app = ensure_application()
    window = GalleryWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
