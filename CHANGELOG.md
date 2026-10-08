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
- Node graph: double-click a node header to rename it. Enter or a click
  elsewhere commits the title, and Escape cancels. A blank title is left
  unchanged. While the editor has focus, Delete and Backspace edit the
  text. After the edit ends, they remove the selection again.
- Add a standalone example and a VS Code debug launcher for each widget
  (Gallery, LabeledLineEdit, PathPicker, SceneGraphTree, NodeGraph, and
  DropWidget), including widgets launched outside the gallery.
- Add pytest-qt tests for every public widget.
- Require Google-style docstrings, ruff, and MkDocs updates when a PR is
  ready. Every merged PR updates this changelog first. The pr-reviewer
  agent reviews a ready PR and squash-merges it when the review passes.

## 0.1.0 — 2026-08-30

Initial library: `LabeledLineEdit`, `PathPicker`, `ObjectListModel`,
`ObjectTableModel`, and packaged QSS (`base`, `widgets`).
