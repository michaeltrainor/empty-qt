# Widgets

Composite widgets take `parent: QWidget | None = None`, set `objectName` for QSS, and set an accessible name.

## Composition, not multiple inheritance

`LabeledLineEdit` **contains** a `QLabel` and a `QLineEdit` in a layout. That is the usual way to build a reusable control. Multiple inheritance (`class Foo(QWidget, SomeMixin)`) is rarely worth it in PySide6.

```python
from mtqt import LabeledLineEdit

field = LabeledLineEdit("Shot", parent=window, placeholder="seq010_sh010")
field.textChanged.connect(print)
field.validityChanged.connect(lambda ok: print("valid" if ok else "invalid"))
```

When `required=True` (the default), empty text sets the dynamic property `invalid` so QSS can draw a red border.

## Nested widgets and dialogs

`PathPicker` is a line edit plus Browse. File dialogs return `str`; the public Python API is `pathlib.Path`. `pathChanged` emits `str` because `Path` is not a Qt metatype — read the `path` property in Python.

```python
from pathlib import Path
from mtqt import PathPicker

picker = PathPicker(parent=window, caption="Scene file")
picker.path = Path("/tmp/scene.ma")
picker.pathChanged.connect(lambda s: print(Path(s) if s else None))
```

## Scene graph tree

`SceneGraphTree` **contains** a `QTreeView` bound to `SceneGraphModel`. Pass a nested list-of-lists forest or a JSON `path`. `currentNameChanged` emits the selected UID as `str`.

```python
from pathlib import Path
from mtqt import SceneGraphTree

tree = SceneGraphTree(parent=window, path=Path("scene_graph.json"))
tree.currentNameChanged.connect(print)
```

## Node graph

`NodeGraphWidget` draws a directed acyclic graph and embeds a `QWidget` in each node. The graph is a `NodeGraph`. It does not depend on Qt, so a tool can build, validate, and walk the DAG before attaching a view.

```python
from PySide6.QtWidgets import QLabel
from mtqt import NodeGraph, NodeGraphWidget
from mtqt.widgets.node_graph import Node, Port, PortDirection

graph = NodeGraph()
graph.add_node(Node("read", "Read", outputs=(Port("out", PortDirection.OUT, "image"),)))
graph.add_node(
    Node(
        "grade",
        "Grade",
        inputs=(Port("in", PortDirection.IN, "image"),),
        outputs=(Port("out", PortDirection.OUT, "image"),),
        position=(260, 48),
    )
)
graph.connect("read", "out", "grade", "in")

def content(node, parent):
    label = QLabel(str(node.payload or node.title), parent)
    return label

canvas = NodeGraphWidget(graph, parent=window, content_factory=content)
canvas.currentNodeChanged.connect(print)
```

`connect` raises `CycleError` when the new edge would loop, and `NodeGraphError` when a port is missing, types disagree, or an input already has an edge. Outputs may fan out. `topological_order` is a safe evaluation order. `to_dict` / `from_dict` round-trip the structure; `payload` is stored as a Python object, not JSON.

Drag an output socket onto an input to connect. A rejected drop emits `connectionRejected`. Delete removes the selected nodes and edges through the same graph object. Replace the embedded widgets with `set_content_factory`. The factory must not mutate the graph.

Do not grow this catalog in a drive-by change; add widgets deliberately.
