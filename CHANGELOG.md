# Changelog

## Unreleased

- Add `SceneGraphModel` / `SceneNode` for nested list-of-lists scene graphs, and
  `SceneGraphTree` composing `QTreeView`.
- Add `mtqt.widgets.node_graph`: a Qt-free DAG (`NodeGraph`) and
  `NodeGraphWidget`, which embeds a custom widget in each node.
- Node graph: drag a connected input or a wire to reconnect it, and
  right-click the canvas background to add a node.
- Node graph: draw port names beside each socket.
- Node graph: pan with Alt-drag and Space-drag as well as middle-drag.
- Node graph: `GraphHistory` undoes a gesture with Ctrl+Z. Ctrl+Y and
  Ctrl+Shift+Z redo it.
- Node graph: nodes draw as a title and ports, with no inner text field.
- Node graph: pad the title and port names so they no longer collide.
- Node graph: nodes have a 180px minimum width.
- Node graph: hovering a node draws a thin glow, and hovering a wire
  brightens it.
- Node graph: press ``F`` to frame the current selection, or every object
  when nothing is selected.

## 0.1.0 — 2026-08-30

Initial library: `LabeledLineEdit`, `PathPicker`, `ObjectListModel`,
`ObjectTableModel`, and packaged QSS (`base`, `widgets`).
