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

## File drops

`DropWidget` is a dashed drop area for local files. `filesDropped` emits a `list[str]` of paths, skipping any non-file URLs in the same drag. A drag with no local files is refused, and the signal does not fire.

```python
from pathlib import Path
from mtqt import DropWidget

drop = DropWidget(parent=window)
drop.filesDropped.connect(lambda files: print([Path(f) for f in files]))
```

## Properties panel

`PropertiesPanel` builds a form from a **pydantic** `BaseModel` instance. The model type is the field profile (titles, frozen/read-only fields, `ge`/`le` bounds, `Literal` / `Enum` choices, types). The widget does not know about meshes or any host domain.

Supported fields: `str`, `bool`, `int`, `float`, `Literal`, `Enum`, `pathlib.Path`, fixed-length `tuple[float, …]` (2–4), and color strings via `Field(json_schema_extra={"mtqt_editor": "color"})` (also auto-detected when the field is named `color`).

Signals: `modelChanged` (updated model), `fieldChanged(field, value, model)`, `validationFailed(field, message)`. Host tools register callbacks with `register_handler` / `register_field_handler` so business logic stays outside the widget. Editability is controlled with `set_editable_check` and/or `set_field_editable` (overrides schema `frozen` / `mtqt_read_only`). Calling `set_model` again with the same model type refreshes values in place. Refreshes, rebuilds, and clears that run from inside a field commit are deferred to the next event-loop tick so spin boxes are not touched mid-click.

### Dragging, sliders, and display units

- **Drag to change numbers.** Drag a numeric row's label left or right to change its value; vector rows have a draggable X/Y/Z label per component. A bounded field spans its range in about 300px, and an unbounded one moves a quarter of its step per pixel. Hold **Ctrl** for fine steps (x0.1) or **Shift** for coarse ones (x10), and press **Esc** to cancel. A drag claims Esc, so a host window's Esc shortcut doesn't fire until it ends. A plain click does nothing.
- **Sliders.** `int` and `float` fields with both a lower and an upper bound (`ge`/`gt` and `le`/`lt`) get a slider next to the number. `mtqt_slider: False` turns it off, and `mtqt_soft_range: (low, high)` gives the slider a narrower range than the bounds (or a range for an unbounded field) while typed values can still go past it.
- **Display units.** `mtqt_display: "degrees"` stores radians and shows degrees; `"percent"` stores fractions and shows percent. Both work on floats and float tuples. `mtqt_suffix: " m"` adds text after the number without converting it. Vector rows show the unit in the row label, as in "Rotation (°)". Vector components you didn't edit commit their stored value exactly, so display rounding never changes them.
- **Live previews.** Drags and slider moves emit `fieldPreviewed(field, value, model)` for each step, with a validated copy of the model; `panel.model` and registered handlers wait for the release, which emits `fieldChanged` once. A host can update its view live from `fieldPreviewed` and record one undo step on `fieldChanged`. Cancelling a drag previews the start value so the host can put it back. `PropertiesBook` forwards `fieldPreviewed` too.

```python
import math


class LightProps(BaseModel):
    angle: float = Field(
        default=math.pi / 4,
        ge=0.0,
        le=math.pi,
        json_schema_extra={"mtqt_display": "degrees"},
    )
    falloff: float = Field(
        default=10.0,
        ge=0.0,
        json_schema_extra={"mtqt_soft_range": (0.0, 50.0), "mtqt_suffix": " m"},
    )
    mix: float = Field(
        default=0.5, ge=0.0, le=1.0, json_schema_extra={"mtqt_display": "percent"}
    )


panel.fieldPreviewed.connect(lambda field, value, model: viewport.show(model))
panel.fieldChanged.connect(lambda field, value, model: history.record(model))
```

```python
from pathlib import Path
from pydantic import BaseModel, Field
from mtqt import PropertiesPanel


class ShotProps(BaseModel):
    name: str = Field(frozen=True, title="Name")
    frame: int = Field(default=1001, ge=1001, le=1100, title="Frame")
    color: str = Field(default="#4a8fd4", json_schema_extra={"mtqt_editor": "color"})
    scene: Path = Field(default=Path("/tmp/shot.ma"), title="Scene")


panel = PropertiesPanel(parent=window, empty_message="Nothing selected")
panel.set_editable_check(lambda field, model: field != "name")
panel.set_field_editable("scene", False)
panel.register_field_handler("frame", lambda field, value, model: print(field, value))
panel.register_handler(lambda field, value, model: apply_shot(model))
panel.set_model(ShotProps(name="sh010"))
panel.validationFailed.connect(lambda field, msg: print(field, msg))
```

## Properties book

`PropertiesBook` is a tabbed stack of `PropertiesPanel` pages for one object with several components (Transform / Mesh / Light). Shared handlers and editability hooks apply to every page. `set_pages([("Transform", t), ("Mesh", m)])` shows tabs; a single page hides the tab bar. `set_model` / `clear` still work for the one-page case.

```python
from mtqt import PropertiesBook

book = PropertiesBook(parent=window, empty_message="Nothing selected")
book.register_handler(lambda field, value, model: apply_component(model))
book.set_pages([("Transform", transform), ("Mesh", mesh)])
```

## Scene graph tree

`SceneGraphTree` **contains** a `QTreeView` bound to `SceneGraphModel`. Pass a nested list-of-lists forest or a JSON `path`. The view uses extended selection. `currentNameChanged` emits the current UID as `str`. `selectedNamesChanged` emits all selected UIDs as `list[str]`. Use `selected_names` / `set_selected_names` to read or replace the multi-selection. `SceneGraphModel.index_for_name` looks up a UID. While the tree has focus, its navigation keys (arrows, Home, End, Page Up/Down, alone or with Shift) take priority over the host window's shortcuts; letters and other keys still reach them.

```python
from pathlib import Path
from mtqt import SceneGraphTree

tree = SceneGraphTree(parent=window, path=Path("scene_graph.json"))
tree.currentNameChanged.connect(print)
tree.selectedNamesChanged.connect(print)
tree.set_selected_names(["geo", "lgt"])
```

### Drag and drop

Pass `drag_drop=True`, or call `set_drag_drop_enabled(True)`, and rows can be dragged. Dropping on a row makes the dragged rows its last children and expands it. Dropping between rows reorders them in that parent. A multi-selection keeps its tree order, a row can't be dropped into itself or a descendant, and a row whose ancestor is also dragged moves with the ancestor.

The model does the move with proper `rowsMoved` notifications, so persistent indexes and the selection follow. `nodesMoved` then emits `(names, parent, row)`: the top-most moved UIDs in their new order, the new parent's UID (`""` for the forest root), and the row of the first moved node. `set_move_filter` installs a `(names, parent, row) -> bool` veto that runs while the cursor hovers, so a rejected target shows a forbidden cursor. Keep it cheap. `SceneGraphModel.move_nodes` and `can_move_nodes` offer the same move without a drag.

```python
tree = SceneGraphTree(forest, parent=window, drag_drop=True)
tree.set_move_filter(lambda names, parent, row: parent != "")
tree.nodesMoved.connect(lambda names, parent, row: print(names, "->", parent, row))
```

The scene viewport uses this to reparent transforms while keeping their world pose, records one undo step per drop, and rebuilds the tree from its own store afterwards.

## Timeline bar

`TimelineBar` is a row of transport controls for clip playback: play / pause, stop, loop, a clip combo box, a scrub slider, a time readout, and a speed box (0.25x to 2x by default). It knows nothing about what it plays. The host fills the clip list and keeps the duration and time current; the bar emits `playToggled(bool)`, `stopRequested()`, `loopToggled(bool)`, `speedChanged(float)`, `clipChanged(str)`, and `timeScrubbed(float)` for the user's actions only. The setters never emit, so `set_time` can run on every playback tick, and it leaves the slider handle alone while the user is dragging it. With no clips the controls are disabled; hosts usually hide the bar then.

```python
from mtqt import TimelineBar

bar = TimelineBar(parent=window)
bar.set_clips(["Walk", "Run"])
bar.set_duration(4.0)
bar.playToggled.connect(player.set_playing)
bar.timeScrubbed.connect(player.seek)
timer.timeout.connect(lambda: bar.set_time(player.time))
```

The scene viewport shows one under the viewport when the scene has animation clips.

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

Drag an output socket onto an input to connect. Drag a connected input, or the wire, to move that connection onto another input. Drop it on empty canvas to disconnect. A rejected drop emits `connectionRejected` and leaves the original wire in place. Port names are drawn inside the node, next to each socket. Hovering a node draws a thin bright glow around its border. Hovering a wire brightens that wire. Right-click empty canvas and choose Add node to insert a node at the cursor. `set_node_factory` replaces that node. Double-click a node header to rename it. Enter or a click elsewhere commits the title, and Escape cancels. A blank title leaves the name unchanged, and undo restores the previous title. While that editor has focus, Delete and Backspace edit the text instead of removing the node. After the edit ends, they again remove the selected nodes and edges through the same graph object. ``F`` centers the view on the selection and zooms it to fit. With nothing selected, ``F`` frames every object in the scene.

Middle-drag, Alt-drag, and Space-drag pan the canvas. Space pans only while the view has focus, so an embedded editor still receives the space bar. The wheel zooms toward the cursor.

`GraphHistory` records those edits. `NodeGraphWidget.history` is one, and Ctrl+Z undoes the last gesture when the canvas has focus. Ctrl+Y and Ctrl+Shift+Z redo it. One gesture is one step: deleting a node restores its wires with it, and a node drag restores the position where the drag started. A focused embedded editor keeps its own undo. `undo()` and `redo()` return false when the stack is empty.

The node body is the header and the port names. `content_factory` can still embed a widget there later; leave it unset for this frame.

Do not grow this catalog in a drive-by change; add widgets deliberately.
