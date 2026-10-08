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

`NodeGraphWidget` draws a directed acyclic graph. Each node is a title and its ports. The graph is a `NodeGraph`. It does not depend on Qt, so a tool can build, validate, and walk the DAG before attaching a view.

```python
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

canvas = NodeGraphWidget(graph, parent=window)
canvas.currentNodeChanged.connect(print)
```

`connect` raises `CycleError` when the new edge would loop, and `NodeGraphError` when a port is missing, types disagree, or an input already has an edge. Outputs may fan out. `topological_order` is a safe evaluation order. `to_dict` / `from_dict` round-trip the structure; `payload` is stored as a Python object, not JSON.

Drag an output socket onto an input to connect. Drag a connected input, or the wire, to move that connection onto another input. Drop it on empty canvas to disconnect. A rejected drop emits `connectionRejected` and leaves the original wire in place. Port names are drawn inside the node, next to each socket. Hovering a node draws a thin bright glow around its border. Hovering a wire brightens that wire. Right-click empty canvas and choose Add node to insert a node at the cursor. `set_node_factory` replaces that node. Delete removes the selected nodes and edges through the same graph object. ``F`` centers the view on the selection and zooms it to fit. With nothing selected, ``F`` frames every object in the scene.

Middle-drag, Alt-drag, and Space-drag pan the canvas. Space pans only while the view has focus, so an embedded editor still receives the space bar. The wheel zooms toward the cursor.

`GraphHistory` records those edits. `NodeGraphWidget.history` is one, and Ctrl+Z undoes the last gesture when the canvas has focus. Ctrl+Y and Ctrl+Shift+Z redo it. One gesture is one step: deleting a node restores its wires with it, and a node drag restores the position where the drag started. A focused embedded editor keeps its own undo. `undo()` and `redo()` return false when the stack is empty.

The node body is the header and the port names. `content_factory` can still embed a widget there later; leave it unset for this frame.

Do not grow this catalog in a drive-by change; add widgets deliberately.
