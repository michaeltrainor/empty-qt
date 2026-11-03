# Changelog

## Unreleased

- `DropWidget` emits a new `filesDropped` signal with the local paths from a
  drop, and no longer prints to stdout. It refuses drags with no local files.
- `import mtqt` no longer fails when package metadata is missing, as when it
  is vendored or on `PYTHONPATH`. `__version__` is then `0+unknown`.
- Remove the unused `pyside6-qtads` dependency.
- Point project URLs at the GitHub repository, document installing from it
  (mtqt is not on PyPI), and drop the Windows-only VS Code interpreter path.
- Move project agent tooling to Claude Code: the `pr-reviewer` agent and the
  PySide6 skill now live under `.claude/`, with shared permissions and a
  `ruff format` hook in `.claude/settings.json`. Remove the Grok agent config.
- Fix CI, which stopped at test collection without pygfx. CI now installs the
  `examples` group, and `tests/test_scene_textures.py` skips when pygfx is
  missing. The pr-reviewer agent now merges only after every CI check has
  finished and passed; a local run no longer replaces CI.
- Fix light shadows for all kinds: directional positive near/far frustum,
  point/spot depth pinning (pygfx resets near≈0 each frame), shared light
  aim for directional/spot, Create → Spot Light, and ground planes default
  to cast-shadow off (stops peter-panning / detached contact shadows).
- Scene viewport: draw transform gizmos above meshes (no depth occlusion);
  drop AxesHelper in favor of red/blue X/Z center lines on the ground grid.
- Scene viewport: Maya-style shading hotkeys **1**–**6** (bounding box,
  wireframe on shaded, flat, wireframe, shaded, textured) via the Shading
  menu. Textured mode samples optional per-mesh ``albedo_map`` (UV
  primitives + QImage load).
- Scene viewport: ``SceneWorld`` owns component tables and shape indexes
  (not a full ECS); window/history/IO go through that façade.
- Scene viewport rendering: `MeshStandardMaterial` (roughness/metalness),
  outline-only selection, procedural studio IBL/skybox, exposure/gamma and
  shadow cull in Preferences, and tighter camera near/far on Frame.
- Scene viewport: File New/Open/Save for JSON scenes; Ctrl+D duplicate;
  F2 rename; **F** frame selection; Ctrl+I isolate / Ctrl+Shift+I show all;
  per-transform `visible` flag.
- Refresh packaged QSS (`base`, `widgets`): denser dark chrome, underline
  tabs, pill tool buttons, styled menus/scrollbars/splitters, and cleaner
  outliner / properties chrome (`documentMode` tabs, hidden tree header).
- Add `PropertiesPanel`: a schema-driven form from a pydantic `BaseModel`
  instance (titles, frozen fields, `Literal` / `Enum`, vectors, `Path`,
  color editor, `ge`/`le` bounds, in-place refresh, `validationFailed`,
  `fieldChanged`, editability hooks, and field/global commit handlers).
  Depends on `pydantic`. Standalone example: `examples/properties_panel.py`.
  Same-type `set_model` refreshes in place; refreshes/rebuilds/clears
  triggered during a field commit are deferred so spin-box clicks do not
  segfault.
- Add `PropertiesBook`: tabbed stack of `PropertiesPanel` pages for
  multi-component objects (shared handlers / editability).
- Scene viewport: hierarchical `TransformComponent` parents store local TRS;
  mesh and light shapes parent under them. Selection always resolves to the
  transform. `PropertiesBook` tabs show Transform / Mesh / Light. Properties
  `space` toggles local/world editing.
- Scene viewport: Maya-style gizmos (W/E/R tools, Q local/world) via
  `MayaTransformGizmo` wrapping `pygfx.TransformGizmo`; drags write local
  TRS back to the transform store.
- Scene viewport: point lights (`fill`) with editable color, intensity,
  distance, decay, and cast-shadow on the Light tab; pickable helpers.
- Scene viewport: shadow maps enabled (light + mesh cast/receive flags,
  directional shadow frustum tuned for the set).
- Scene viewport: Create/Delete for empties, mesh primitives, and lights;
  Maya-style XZ viewport grid (not in the outliner). Default scene is a
  directional key light and the grid only.
- Scene viewport: toolbar with Select/Move/Rotate/Scale (Q/W/E/R) and
  World/Local; edit space defaults to world for new and selected objects.
- Scene viewport: Settings → Preferences dialog edits and saves app
  settings (edit space, grid/axes, gizmo size, ambient, background,
  shadow bias/extent) to `~/.config/mtqt/scene_viewport.json`.
- Scene viewport: local pivot offset (panel + **D** Edit Pivot),
  multi-select manipulator at average pivot, Parent (**P**) / Unparent
  (**Shift+P**) preserving world pose, and Ctrl+Z / Ctrl+Y undo-redo
  for create/delete/TRS/pivot/reparent.
- `SceneGraphTree` uses extended selection. Add `selectedNamesChanged`,
  `selected_names`, and `set_selected_names`, plus
  `SceneGraphModel.index_for_name`.
- Scene viewport example: click meshes to select (Shift/Ctrl/Meta for
  multi-select); tree and viewport selection stay in sync with emissive
  highlights and a thin wireframe edge overlay.
- Add `examples/scene_viewport` package: `SceneGraphTree` beside a pygfx
  viewport. Numba builds box/plane/sphere meshes; mesh components and a shared
  scene description drive both the tree and lit Phong draws (ambient +
  directional light). Install with
  `uv sync --extra dev --extra docs --group examples`, then
  `uv run --group examples python -m examples.scene_viewport`.
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
