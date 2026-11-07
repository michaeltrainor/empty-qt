# Changelog

## Unreleased

- Scene viewport: File → Export Scene… and Export Selection… write `.glb`
  or `.gltf`. Transforms become nodes with their TRS, primitives are
  tessellated, imported meshes are re-embedded from their loaded buffers,
  materials carry the Mesh tab values and the file's maps (alpha mode,
  sides, and unlit included), and lights become `KHR_lights_punctual`
  aimed at the origin as the viewport aims them. Hidden transforms are
  left out, with a note. `KHR_materials_*` textures are not exported yet.
- Scene viewport: `KHR_lights_punctual` lights in glTF files import as
  light shapes on their nodes. A directional light's node is placed so
  the viewport's aim at the origin reproduces its direction; a spot light
  keeps its position but loses the file's direction, with a warning.
  Intensities above 20 are clamped, with a warning.
- Scene viewport import, second batch from the glTF sample survey:
  - `KHR_materials_clearcoat`, `ior`, `specular`, `sheen`, `iridescence`,
    and `anisotropy` render through pygfx's physical material, with their
    textures; `KHR_materials_emissive_strength` scales emission. These
    values are read from the file at draw time, like the maps.
  - A second UV set (`TEXCOORD_1`) is read, so occlusion and other maps
    that sample it show instead of being dropped.
  - Vertex colors (`COLOR_0`, and PLY/OBJ per-vertex colors) show when the
    mesh has no base color texture in use.
  - Sampler wrap modes (clamp, mirror) apply per map.
  - Internals: `MaterialSurface` is the one place the importer reads a
    surface from a file, and `materials.build_material` the one place it
    becomes a pygfx material; `GltfDocument` reads the accessors, images,
    and samplers trimesh drops.
- Scene viewport import, first batch from the glTF sample survey:
  - Alpha: glTF `alphaMode` MASK and BLEND, `alphaCutoff`, and the base
    color alpha now render (cut-out foliage, glass, decals). The Mesh tab
    gets Opacity, Alpha Mode, and Alpha Cutoff.
  - Sides: `doubleSided` is honoured, so single-sided glTF materials cull
    their back faces. Primitives and older scenes stay double sided (new
    Double Sided field).
  - Occlusion maps darken ambient light, with the file's strength.
  - `KHR_texture_transform` offsets, rotations, and scales apply per map.
  - `KHR_materials_unlit` materials show their color and texture without
    lighting or shadows (new Unlit field).
  - Image URIs with percent-escapes such as `paint%20new.png` load.
  - A map that samples a second UV set is dropped with a warning instead
    of being mapped wrong.
- Scene viewport file handling:
  - File → Open takes model files (glTF/GLB, OBJ, STL, PLY) as well as
    scene JSON. A model opens as a new, untitled scene with the model
    imported and framed.
  - File → Open Recent lists the last ten scenes and models opened,
    saved, or imported, with Clear Recent Files. The list is stored next
    to the settings file.
  - New Scene, Open, and closing the window ask to save, discard, or
    cancel when the scene has unsaved changes.
  - `scene-viewport <file> [more model files]` opens the first file and
    imports the rest.
- `apps/scene_viewport/scripts/survey_gltf_samples.py` loads every model in
  a glTF-Sample-Assets checkout headless and writes a Markdown report of
  what loads, what fails, and which glTF features appear most often, to
  guide import work.
- Scene viewport: an OBJ material library named without a `.mtl` suffix is
  scanned for textures too, and MTL map statements with an unknown
  `-option` or a file name starting with `-` resolve to the right file.
- Scene viewport import fixes from the PR #28 review:
  - A texture added to an edited `.mtl` is now tracked, warned about, and
    reloaded without touching the `.obj`; each material library is scanned
    on its own timestamp.
  - OBJ `mtllib` lines with several libraries, and `mtllib` / `map_*` file
    names with spaces (after options such as `-bm 1` or `-o 0 0.5`), are
    read correctly.
  - glTF buffers and images behind `http:` / `https:` URIs are reported as
    "remote file not loaded" instead of as missing files.
  - A textured material build reads its model file once instead of once
    per map, so large scenes stat fewer files.
- `SceneGraphTree` reparents and reorders by drag and drop when
  `drag_drop=True` (or `set_drag_drop_enabled`). Drop on a row to append
  under it, or between rows to reorder; `nodesMoved` reports the move, and
  `set_move_filter` lets the host veto a target while hovering.
  `SceneGraphModel.move_nodes` does the same move without a drag, with
  proper `rowsMoved` notifications.
- Scene viewport: drag transforms in the tree to reparent them (world pose
  kept, as with P / Shift+P) or reorder siblings. Drop on the `geo` or
  `lgt` folder to unparent. Each drop is one undo step, and the new order
  is saved with the scene. Model files dropped on the tree still import.
- Scene viewport import gaps closed:
  - Textured mode (6) also uses glTF roughness/metal, normal, and emissive
    maps. Normal-mapped meshes get computed tangents, so normal maps work
    in the top, front, and side views too.
  - Preferences → Import Up Axis and Import Units convert OBJ, STL, and
    PLY files to the scene's Y-up metres. Auto turns STL and PLY Z-up
    files upright; glTF is always Y-up metres.
  - Model, material, and texture files edited outside the app reload when
    the window is activated again, or with File → Reload Changed Files.
  - Import and Open warn about texture files that are missing or
    unreadable; those parts show the material color.
- Scene viewport: imported models bring their base color texture (glTF,
  embedded or external, and OBJ). Textured mode (6) shows it; a Mesh tab
  Albedo Map still overrides it. Adds `pillow` to the app's dependencies.
- Scene viewport: framing (F, A, Home, and after an import) in the top,
  front, and side views no longer clips the framed objects. A flat object
  facing the view could vanish entirely.
- Scene viewport: import glTF/GLB, OBJ, STL, and PLY models with File →
  Import… or by dropping files on the window. glTF files keep their node
  hierarchy and names, and materials bring their base color, roughness,
  and metalness. Scenes save the model paths relative to the scene file;
  a missing model opens as a placeholder box with a warning. Adds `trimesh`
  to the app's dependencies.
- `SceneGraphTree` keeps its navigation keys (arrows, Home, End, Page
  Up/Down, alone or with Shift) while it has focus, even when the host
  window binds them as shortcuts. In the scene viewport, Home moves to the
  first row in the tree instead of framing the view; A still frames all.
- Scene viewport: local-space move snapping stays on the dragged axis for
  objects under a non-uniformly scaled parent, and no longer breaks on a
  zero-scale axis.
- Scene viewport fixes:
  - The box-select outline stays visible over meshes in the top, front,
    and side views.
  - Shift+ or Ctrl+drag on a manipulator handle moves the selection
    (Ctrl inverts snapping) instead of starting a box.
  - Releasing Ctrl a moment before the mouse button no longer snaps or
    unsnaps the final position of a drag.
- Scene viewport manipulator: grab anywhere on an axis line, not only its
  small tip, to move or scale along it. Pressing a line used to fall
  through to the object behind it. Moves along an axis or plane no longer
  drift off it under a perspective camera, so an X drag leaves Y alone.
- Box-select in the scene viewport: left-drag draws a rectangle and selects
  every visible object it touches. Shift toggles, Ctrl removes, and
  Ctrl+Shift adds, as for clicks. It is off under the pygfx camera preset,
  where left-drag orbits.
- `PropertiesPanel` adds three ways to edit numbers:
  - Drag a numeric row's label, or a vector's X/Y/Z label, to change the
    value. Ctrl gives fine steps, Shift gives coarse ones, and Esc cancels,
    even when the host window has its own Esc shortcut.
  - `int` and `float` fields with both bounds get a slider.
    `mtqt_slider: False` turns it off, and `mtqt_soft_range` sets a slider
    range that typed values can go past.
  - `mtqt_display: "degrees"` or `"percent"` and `mtqt_suffix` show values
    in friendlier units while the model keeps its own.
- New `fieldPreviewed` signal on `PropertiesPanel` and `PropertiesBook`.
  Drags and slider moves preview each step and emit `fieldChanged` once on
  release.
- Scene viewport: rotation is shown in degrees (still stored in radians).
  Dragging a property updates the viewport live and records one undo step
  on release.
- `PropertiesPanel` and `PropertiesBook` use a compact layout, about as
  dense as the attribute editors in Maya and Blender: 12px text, 22px
  fields, 3px between rows, right-aligned labels that are vertically
  centered, and tighter tabs. Vector X/Y/Z spin boxes no longer show
  arrow buttons, and they share the row width, so all three fit in a
  narrow panel. The styles are scoped to `#PropertiesBook` and
  `#PropertiesPanel`, and the rest of the app chrome keeps its sizes.
- Lighten the `base` and `widgets` stylesheets from near-black blue-grays
  to neutral mid-grays in the style of Maya (panels `#3c3c3c`, inputs and
  bars `#2f2f2f`, borders `#565656`, muted text `#b0b0b0`). The scene
  viewport canvas placeholder matches the 3D background. The node graph
  keeps its own painted colors.
- Scene viewport: the background now defaults to Maya's flat 0.36 gray
  (`#5c5c5c`). It is unlit and no longer tied to the IBL colors. A new
  `solid` background mode and Background Color setting are added, and
  settings files saved before this change switch to `solid` once.
- Scene viewport: the Gamma setting no longer darkens the whole image.
  pygfx already outputs sRGB, so a display gamma of 2.2 now means no extra
  correction.
- Scene viewport: grid lines are darker so they read on the gray
  background. The grid stays unlit and never casts or receives shadows.
- Claude Code config:
  - A PreToolUse hook (`.claude/hooks/guard_merge.py`) blocks
    `gh pr merge` and the REST merge endpoint everywhere except the
    `pr-reviewer` agent. It tokenizes commands, so text that only mentions
    merging passes, and blocks a merge command it can't parse.
  - A SessionStart hook keeps the environment synced with `uv.lock`.
  - `pr-reviewer` runs in its own worktree and can't edit files, and the
    instructions that used to be repeated in every handoff are now in the
    agent file.
  - `/ship` takes a change from `scripts/check` to a reviewed PR.
  - Path-scoped rules cover the library and the scene viewport.
- The `platforms` CI job no longer installs the docs group.
- Move the `dev` and `docs` extras to PEP 735 dependency groups, so they
  no longer appear in mtqt's published metadata. They are default groups,
  so `uv sync --all-packages` installs everything.
- CI syncs with `--locked`, which fails on a stale `uv.lock`. `--frozen`
  ignored the mismatch.
- Add `scripts/check`, the one list of checks that CI, agents, and
  contributors run. Pre-commit runs ruff and pyright from `uv.lock`, so
  Dependabot updates reach it.
- The docs gate no longer requires a docs change for `.github/` edits.
- Fix the scene viewport's 63 pyright errors, and type-check
  `apps/scene_viewport/src` in CI. Shadow depth pins move from private
  attributes on pygfx shadows into the viewport, with regression tests. Calls
  where pygfx's missing annotations make pyright infer `int` for float
  parameters carry a rule-scoped `pyright: ignore`.
- The `gate` job skips Dependabot PRs, which need no changelog or docs update.
- Widen the Claude Code force-push deny rules to cover flags after the
  remote, `+refspec` pushes, and `--mirror`.
- Move the scene viewport from `examples/` to `apps/scene_viewport`, an
  unpublished uv workspace member with its own tests. Install it with
  `uv sync --all-packages`, and run it with
  `uv run --package scene-viewport scene-viewport`. This replaces the
  `examples` dependency group.
- Make the scene viewport navigate like Maya or Blender. Preferences pick
  Maya (default), Blender, or pygfx camera bindings. The View menu adds
  frame all and orthographic top / front / side views. Toolbar snapping
  covers move and rotate. Clicks follow Maya's selection modifiers. New
  shortcuts: Ctrl+A select all, Alt+A deselect, Ctrl+H hide. A status bar
  shows the selection, tool, and camera bindings. Fix objects lagging the
  manipulator by its first drag step.

## 0.2.0 — 2026-11-02

- CI runs pyright, enforces 80% coverage, and tests on macOS and Windows.
  A weekly workflow tests the newest PySide6 without blocking PRs. Dependabot
  opens weekly updates, leaving Python and PySide6 pinned.
- Adopt ruff rules `B`, `C4`, `SIM`, `RUF`, `PT`, and `T20`, and fix the
  findings along with the pyright errors in `PropertiesPanel`.
- Add `QAbstractItemModelTester` coverage for all three item models. Tests now
  fail on Qt warnings.
- Update ruff to 0.17.3, `uv_build` to 0.13, `actions/checkout` to v7, and
  `setup-uv` to v10.
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
