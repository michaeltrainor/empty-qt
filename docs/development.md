# Development

```bash
uv sync --all-packages
scripts/check
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
uv run mkdocs serve
uv run mkdocs build
uv build
```

Install git hooks (after `uv sync --all-packages`):

```bash
uv run pre-commit install
```

`dev` and `docs` are dependency groups ([PEP 735](https://peps.python.org/pep-0735/)), not extras, so they never appear in the published package. uv installs both by default. With pip 25.1 or later, use `pip install -e . --group dev --group docs`.

`scripts/check` runs ruff, the format check, pyright, pytest with the 80% coverage floor, and `mkdocs build --strict`, the same list as the CI `check` job.

## Layout

Editable install from `src/mtqt`. Gallery: `uv run python examples/gallery.py`. Confirm `.qss` files are inside the wheel after `uv build` (zip listing of `dist/*.whl`).

## Run one widget

Run a single launcher without opening the gallery:

```bash
uv run python examples/labeled_line_edit.py
```

`examples/properties_panel.py` shows `PropertiesPanel` with a small pydantic model. `examples/timeline_bar.py` drives a `TimelineBar` from a timer and logs its signals.

`apps/scene_viewport` is a separate, unpublished app in the uv workspace. It draws with rendercanvas and pygfx. Numba builds mesh primitives. The default scene is a directional key light plus a Maya-style XZ viewport grid (chrome only). Toolbar tools: Select / Move / Rotate / Scale (Q/W/E/R) and World/Local (default **world** for new objects and editing). Use Create / Delete for empties, meshes, and lights. File menu saves/loads JSON scenes; Edit covers duplicate, rename, isolate, and frame. Selectable objects are **transforms**; shapes attach as components. `PropertiesBook` tabs show Transform / Mesh / Light. **Settings → Preferences** edits viewport chrome and lighting prefs (persisted under `~/.config/mtqt/scene_viewport.json`). A plain `uv sync` installs only mtqt, so add the app with `--all-packages`:

```bash
uv sync --all-packages
uv run --package scene-viewport scene-viewport
```

### Scene viewport gizmos

Maya-style tools via `MayaTransformGizmo` (`apps/scene_viewport/src/scene_viewport/gizmo.py`), wrapping `pygfx.TransformGizmo`:

- **Tools** (toolbar + hotkeys): **Q** select, **W** move, **E** rotate, **R** scale; **D** edit pivot (affect pivot only).
- **Handles**: as in Maya, you can grab anywhere on an axis line, not only its tip, to move along it (or scale, with the scale tool). An invisible 12px strip over each line takes the pick. Axis and plane moves stay exactly on the axis or plane under a perspective camera; stock pygfx let an X drag drift in Y.
- **Shading** (**1**–**6**): bounding box, wireframe on shaded, flat, wireframe, shaded, textured (an imported model's base color texture, or the Mesh tab's `albedo_map`, which wins when set).
- **Document model**: `SceneWorld` owns transform / mesh / light / camera / clip tables and indexes; not a full ECS. Since the stage-world change it also mirrors every component onto a live in-memory `Usd.Stage` (`world.stage`) through `scene_viewport/usd_prims.py`, the same component-to-prim mapping the USD files use: each add, set, remove, rename, reparent, and reorder authors the prim, moving subtrees with `Sdf.CopySpec` when a parent or name changes, and clips are anonymous sublayers that stay muted so the composed stage shows the rest pose. The tables (`world.transforms[name] = component`) are live views whose writes go through the same methods, so the stage is never behind. File meshes have their triangles embedded in the stage when added (about one second for VirtualCity's 167 meshes). The stage is the data source the viewport will move to next; today the viewport still mirrors the components.
- **Space**: toolbar World / Local (default world). Session preference drives the gizmo and Transform tab; TRS stays stored local.
- **Pivot**: each transform stores a local `pivot`; the gizmo sits on a proxy at that offset (average of selection for multi-select; orientation from the last selected). Edit Pivot moves the offset without moving geometry.
- **Cameras**: Create → Cameras adds a perspective or orthographic camera on a transform aimed at the scene; it draws a frustum helper, lists under the `cam` folder, and gets a Camera tab (field of view, ortho size, near and far). The camera looks along its transform's -Z, so aim it by rotating the transform. View → Look Through Camera (**Ctrl+Shift+L**) renders from the selected camera and pauses orbit navigation and the gizmo; toggle again, frame, or pick a preset view to return to the free camera. Cameras duplicate, rename, delete, undo, and save like other shapes.
- **Hierarchy**: Edit → Parent to Last Selected (**P**) / Unparent (**Shift+P**) keep world pose. So does dragging rows in the tree: drop on a transform to parent under it, between rows to reorder, or on the `geo` / `lgt` folder to unparent. Folders and shape leaves don't move, lights stay under `lgt`, and the order is saved with the scene.
- **Undo**: Ctrl+Z / Ctrl+Y for create, delete, TRS, pivot, reparent, reorder, rename, and visibility.
- **Session**: File → New / Open / Save. Open takes a scene file (JSON, or USD: `.usda`, `.usdc`, `.usd`) or a model file; a model opens as a new, untitled scene. Save As offers JSON, USD ASCII, and USD binary. Open Recent lists the last ten scenes and models opened, saved, or imported (stored in `~/.config/mtqt/scene_viewport_recent.json`). New, Open, and Quit ask before dropping unsaved changes. `scene-viewport <file> [models…]` opens the first file and imports the rest. Ctrl+D duplicate; F2 rename; Ctrl+A select all, Alt+A deselect; Ctrl+H hide, Ctrl+I isolate, Ctrl+Shift+I or Ctrl+Shift+H show all; `visible` on the Transform tab.
- **Camera**: Preferences → Navigation picks Maya (Alt+LMB tumble, Alt+MMB track, Alt+RMB dolly; the default), Blender (MMB orbit, Shift+MMB pan, Ctrl+MMB zoom), or stock pygfx bindings. The status bar shows the active bindings. View → **F** frame selection, **A** / Home frame all (Home goes to the tree instead when it has focus), numpad **7** / **1** / **3** orthographic top / front / side, numpad **5** perspective.
- **Selection**: Maya click modifiers. Shift toggles, Ctrl removes, Ctrl+Shift adds. Alt+click never changes the selection. Left-drag on the viewport draws a selection box: anything it touches is selected, with the same modifiers, and a plain drag over empty space clears the selection. A drag that starts on a manipulator handle moves the selection instead, even with Shift or Ctrl held. A child is selected without its parent. Hidden objects are skipped. Box selection is off under the pygfx preset, where left-drag orbits.
- **Snapping**: toolbar Snap (**X**) rounds move and rotate drags to the Preferences steps (0.5 units and 15° by default). Hold Ctrl during a drag to invert it; the state at the last mouse move wins, so letting go of Ctrl just before the button keeps it. Local-space moves snap along the local axes, including the slanted axes of a child under a non-uniformly scaled parent.
- **Import**: File → Import… or drop files on the window to bring in glTF/GLB, OBJ, STL, and PLY models (read with `trimesh`). Each file gets a root transform named after it; glTF keeps its node hierarchy and names, and a single-mesh file puts the mesh on the root. Base color, roughness, and metalness come from the file's material (or its vertex colors) when it has one, and in textured mode (**6**) the base color texture (glTF embedded or external, OBJ `map_Kd`) shows along with glTF roughness/metal, normal, emissive, and occlusion maps, each with its `KHR_texture_transform`, sampler wrap modes, and UV set (`TEXCOORD_1` is read); images are decoded with Pillow. `KHR_materials_clearcoat`, `ior`, `specular`, `sheen`, `iridescence`, `anisotropy`, and `emissive_strength` render through pygfx's physical material; those values and the maps are read from the file at draw time, not stored on the mesh. Vertex colors show when no base color texture is in use. glTF `alphaMode` (mask and blend), `alphaCutoff`, base color alpha, `doubleSided`, and `KHR_materials_unlit` carry over to the Mesh tab's Opacity, Alpha Mode, Alpha Cutoff, Double Sided, and Unlit fields, which also work on primitives. Normal-mapped meshes get computed tangents, so normal maps also work in the orthographic views. Preferences → Import Up Axis / Import Units convert OBJ, STL, and PLY files (Auto treats STL and PLY as Z-up); glTF is always Y-up metres. Files edited outside the app (models, `.mtl` files, textures, albedo maps) reload when the window is activated again, or with File → Reload Changed Files; a texture newly named in an edited `.mtl` is picked up too. Missing or unreadable texture files, and glTF files behind `http:` URIs (which are never fetched), are listed in a warning after Import and Open. OBJ `mtllib` lines may name several libraries with any file suffix, and library and map file names may contain spaces; map options such as `-bm 1` are skipped before the file name, including options the app doesn't know. The import is one undo step and frames the new models. Meshes reference their file (`kind: file`, `source`, `source_mesh`) rather than copying it; Save writes `source` relative to the scene file. A missing or unreadable file opens as a placeholder box with a warning. `KHR_lights_punctual` lights import as light shapes on their nodes: a directional light's node is placed so the viewport's aim at the origin reproduces its direction, a spot light keeps its position but loses the file's direction (warned), and intensities above 20 are clamped (warned). File → Export Scene… / Export Selection… write `.glb` or `.gltf`: nodes with TRS, tessellated primitives, re-embedded imported meshes, Mesh tab materials with the file's core maps, alpha mode, sides, unlit, and lights as `KHR_lights_punctual` aimed at the origin. glTF cameras import as camera shapes on their nodes and export with their lens. Hidden transforms are left out with a note; `KHR_materials_*` textures are not exported.
- **Look**: meshes use Standard/PBR (roughness/metalness + procedural IBL); optional albedo path; selection is outline-only; Preferences cover exposure, gamma (2.2 = standard sRGB), solid/gradient/skybox background (solid Maya gray `#5c5c5c` by default, unlit and separate from IBL), and shadow bias / extent / cull (keep bias tiny so contact shadows stay attached). Point/spot shadows pin perspective near/far after pygfx's per-frame reset. No shared material library.
- **Write-back**: Drag updates the local store; the properties book refreshes on pointer release.
- **USD scenes**: `scene_viewport/usd_io.py` writes the document as an OpenUSD stage (`usd-core`) and reads it back unchanged, using the component-to-prim mapping in `scene_viewport/usd_prims.py`. Transforms are `UsdGeom.Xform` prims with explicit `translate`, `translate:pivot`, `orient`, `scale`, and inverse-pivot ops (names made valid identifiers, the original kept in `customData`); primitives are `Cube`, `Sphere`, and `Plane`; file meshes are `UsdGeom.Mesh` with the triangles embedded for other tools plus the model file as an asset path, and the viewport keeps drawing from the model file; the Mesh tab becomes a `UsdPreviewSurface` under `/World/Looks`; lights are `UsdLux` distant and sphere lights; cameras are `UsdGeom.Camera`. Each clip is a sublayer of time samples in `<stem>_clips/`; USD interpolates samples linearly, so STEP and CUBICSPLINE channels are baked at 60 Hz for other tools and kept exactly in the layer's `customLayerData` for the viewport. Fields USD has no schema for (edit space, light distance and decay, shadow flags, unlit) ride in `customData` under `mtqt`. Stages from other tools open too: `Xform` hierarchies, `Cube` / `Sphere` / `Plane`, lights, and cameras come through, `Mesh` prims without a model file are skipped. `typings/pxr/` holds `Any` stubs because `usd-core` ships no type information. USD is the planned backbone of the scene; this is the first step, with JSON still the default format.
- **Animation**: glTF `animations` import as clips (`SceneDocument.animations`, `SceneWorld.animations`) that animate transform position, rotation, and scale with STEP, LINEAR, and CUBICSPLINE keys; rotations are kept as quaternions and slerped. Channel node indices are mapped to the transform names the import created, so renamed nodes stay targeted. A scene with clips shows a `TimelineBar` under the viewport: play / pause (**Space**, also Animation → Play / Pause), stop, loop, clip, scrub, and speed. Playback is view state like look-through: each tick poses the viewport from the stored rest pose and never writes the store, pushes history, or dirties the document; Stop restores the rest pose. While a pose is shown, the Transform tab is read-only for animated transforms and their descendants, and starting a gizmo drag or a reparent on one stops playback first (status bar hint) so the edit applies to the rest pose. Deleting an animated transform drops its channels (a clip left empty goes too) and undo brings them back; renaming retargets them. Clips save with the scene. `weights` (morph target) channels, `KHR_animation_pointer` targets (which cover `KHR_node_visibility`), and channels on nodes that were not imported are skipped with one warning per clip. Skins still import as plain joint nodes, so their clips move the joints but not the mesh. Export ignores clips.
- **Sample survey**: `uv run --package scene-viewport python apps/scene_viewport/scripts/survey_gltf_samples.py <glTF-Sample-Assets/Models> report.md` loads every Khronos sample model headless and writes a Markdown report: which load, which fail and why, and how often each glTF feature appears. Use it to pick the next import feature and to check for regressions.
- **Not in scope yet**: scale snapping, screen space, custom HDRIs; `KHR_materials_transmission`, `volume`, `dispersion`, and `diffuse_transmission` (pygfx has no transmission yet), KTX2 textures, Meshopt compression, points and lines primitives, skins (skinned meshes), morph targets, `KHR_animation_pointer` (material, camera, and visibility animation), animation export, and keyframe editing, inside model files; spot light directions from files.

Run and Debug in VS Code or Cursor lists these configurations (`.vscode/launch.json`, debugpy, integrated terminal):

- Gallery — `examples/gallery.py`
- LabeledLineEdit — `examples/labeled_line_edit.py`
- PathPicker — `examples/path_picker.py`
- PropertiesPanel — `examples/properties_panel.py`
- SceneGraphTree — `examples/scene_graph_tree.py`
- TimelineBar — `examples/timeline_bar.py`
- SceneViewport — `scene_viewport` (module)
- NodeGraph — `examples/node_graph.py`
- DropWidget — `examples/drop_widget.py`
- DropWidget module — `src/mtqt/widgets/drag_n_drop.py`
- pytest — `tests/`

`uv run pytest` runs the suite. Tests use the `qtbot` fixture. `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`. Do not construct `QApplication` in tests.

## Conventions

- Google-style docstrings on public classes, functions, and methods that are mtqt's own API. Summary line, then Args / Returns / Raises when they apply. Match `src/mtqt/widgets/labeled_line_edit.py`. Qt virtual overrides (`paintEvent`, `rowCount`, `data`, and similar) do not need a docstring when they only implement the Qt contract. `__init__` is documented on the class. Ruff enforces this with pydocstyle convention `google` (see `pyproject.toml`). `D102` is ignored because Qt overrides are public methods; still document new public Python methods.
- `pathlib.Path` at Python boundaries
- `logging.getLogger(__name__)` — no `print` in the library
- ruff select `E`, `F`, `I`, `UP`, `D`, `B`, `C4`, `SIM`, `RUF`, `PT`, `T20`, then `ruff format`. `uv run pyright` (mode `standard`) also runs in CI.
- Do not add a node-type plugin loader, evaluator, or palette inside mtqt. Node catalogs belong in the host tool.

## Claude Code

This project is managed with Claude Code.

- `AGENTS.md` holds the project rules. Claude Code reads it only when no `CLAUDE.md` exists, so don't add one; if you must, make its first line `@AGENTS.md`.
- `.claude/rules/` holds path-scoped notes that load only when Claude touches matching files: `library.md` for `src/mtqt/` and `scene-viewport.md` for `apps/scene_viewport/`.
- `.claude/skills/pyside6/` is the Qt safety skill. `.claude/skills/ship/` is `/ship`, which takes a change from `scripts/check` to a reviewed PR.
- `.claude/agents/pr-reviewer.md` reviews and squash-merges PRs. It runs on Sonnet in its own worktree and can't edit files.
- `.claude/settings.json` sets shared permissions and three hooks:
  - SessionStart runs `uv sync --all-packages --locked`, and warns when `uv.lock` is stale.
  - PreToolUse runs `.claude/hooks/guard_merge.py`, which blocks `gh pr merge` and the REST merge endpoint everywhere except `pr-reviewer`, including inside `bash -c` and `eval`. It tokenizes commands, so quoted text and heredocs that mention merging pass, and it blocks a merge command it can't parse. It's a guardrail, not a security boundary: a merge run from a script file isn't detected. It needs `python3`, and `tests/test_guard_merge.py` covers it. A deny rule can't do this job, because deny rules also bind subagents. Your own terminal merges aren't affected.
  - PostToolUse runs `ruff format` on edited Python files.

Put personal overrides in `.claude/settings.local.json`, which is gitignored.

## Pull requests

Before a pull request is marked ready for review:

- Google-style docstrings on new public API, as in Conventions above.
- pytest-qt tests under `tests/` for every public widget, using the `qtbot` fixture. Run `uv run pytest`.
- `uv run ruff check` and `uv run ruff format` are clean.
- MkDocs pages under `docs/` are updated for anything a user or contributor would notice (new widgets, examples, commands, or the PR process). `mkdocs.yml` uses `docstring_style: google`. Verify with `uv run mkdocs build --strict` after `uv sync --all-packages`.
- `CHANGELOG.md` is updated under `## Unreleased` in the same PR, before merge. Do not add a follow-up commit after merge. Dependabot PRs need no changelog or docs update, and the `gate` job skips them.

When a pull request is ready for review (not a draft), the `pr-reviewer` agent reviews it and squash-merges it if the review passes and every CI check has finished and passed. A local run does not replace CI. Do not merge a ready PR in place of `pr-reviewer`.

CI (`.github/workflows/ci.yml`) runs `uv sync --all-packages --locked`, then `scripts/check`. `--locked` fails when `uv.lock` is out of date with `pyproject.toml`, so run `uv lock` after changing dependencies. Without `--all-packages`, the scene viewport tests skip locally. Pyright checks both `src` and `apps/scene_viewport/src`. Its `platforms` job runs the tests on macOS and Windows. Branch protection requires the `check` job by name, so do not turn `check` into a matrix.

`.github/workflows/qt-latest.yml` tests the newest PySide6 weekly as an early warning, and never blocks a PR. Dependabot opens weekly update PRs, and leaves Python and PySide6 alone because they follow the VFX Reference Platform.

Tests fail on Qt warnings (`qt_log_level_fail`). Add a pattern to `qt_log_ignore` in `pyproject.toml` only for noise from the test platform. `.github/workflows/pr-ready.yml` fails a ready PR that does not update `CHANGELOG.md`, or that changes `src/`, `examples/`, `apps/`, or `pyproject.toml` without updating `docs/`. Dependabot PRs are exempt. That workflow does not merge.
