# USD-centric architecture (plan)

Status: proposal, 2026-11-07. Not implemented. The scene viewport today is a
component store (`SceneWorld`) mirrored onto a live `Usd.Stage` and drawn by
pygfx; USD files are read as model files through
`scene_viewport/mesh/usd_reader.py`. This page records the rethink that came
out of opening the Pixar Kitchen set (1788 meshes): loading was made fast,
but drawing stays at about 4 fps because the viewport is one Python-driven
draw call per mesh, and skinning, subdivision, and instancing would all have
to be rebuilt by hand. The decision is to make the stage the document and
Hydra the renderer, keeping PySide and mtqt for the UI.

## Facts that shape it

- The pip `usd-core` wheel is the non-imaging subset of OpenUSD. Storm and
  `UsdImagingGL` only come from a source build with `--imaging
  --usd-imaging --python`. That build is the first step and the long pole.
- Since OpenUSD 25.11 the scene-index (Hydra 2) path is the default and the
  scene-delegate path is deprecated; extensions are scene index plugins.
- No Rust Hydra render delegate exists yet (`hydra-rs` is early bindings that
  drive Storm), so a Rust game view would consume a scene index that emits
  cooked meshes, or be our own delegate later.

## Principles

- **The stage is the document.** No component tables, no mirror, no JSON. A
  scene is a root layer that composes other layers; every edit is an Sdf edit
  on an edit target.
- **Hydra is the renderer.** Storm draws the stage; our code draws overlays.
- **Foreign files are composed, never converted.** Opening a USD file
  references it. glTF, OBJ, STL, and PLY are converted to a USD layer once,
  beside the source, then composed.
- **PySide is the shell.** mtqt keeps the generic widgets; USD-aware models
  and panels live in the app.

## Document

- Layer stack per scene: the user's edit layer (what Save writes), a session
  layer for view state that is never saved (visibility overrides, muted
  clips, look-through camera, purposes), and the referenced assets. Payloads
  stay unloaded until needed.
- App data stops riding in `customData`. Edit space, pivot preference, and
  panel state become a codeless applied API schema shipped with the app.
  Kind, model hierarchy, and variants are used as intended.
- Undo is a command stack of inverse Sdf edits captured per command inside
  an `Sdf.ChangeBlock`.
- Animation is stage time: clips stay layers of time samples, unmuted when
  playing; UsdSkel and blend shapes are handled by imaging.

## Rendering

- A `QOpenGLWidget` hosting `UsdImagingGL.Engine`, as usdview does. That
  gives instancing, subdivision, skinning, UsdPreviewSurface, UsdLux,
  purposes, draw modes, picking, selection highlight, and time in C++.
- Overlays (gizmo, grid, camera frusta) are session-layer prims with
  `guide` purpose, drawn and picked by Storm. The 2D marquee is Qt painting.
- Lost from pygfx: clearcoat, sheen, iridescence (MaterialX in Storm covers
  part of this later).
- Storm needs OpenGL 4.5 on Windows and Linux and Metal interop on macOS; the
  build and CI matrix carry that.

## Services

- Asset access is an Ar resolver plugin: local cache, remote fetch,
  versioning behind `Ar.ResolvedPath`. This is the "service that creates,
  caches, and delivers data" from the ECS idea, in USD's own extension point.
- Derived data (cooked meshes, baked animation, procedurals) are scene index
  plugins or generated layers written by a worker process, both able to run
  out of process later.

## UI

- Outliner: a `QAbstractItemModel` over prims, updated from
  `Usd.Notice.ObjectsChanged`, filtered by purpose, kind, and active state.
- Properties: generated from prim schemas and attribute types; transforms
  through `XformCommonAPI`.
- Timeline bound to the stage time code range; docks with PySide6-QtAds.

## Carries over / goes

Carries over: `usd_prims.py` as native authoring, `usd_reader.py` as test
oracle and cook path, the glTF importer as a one-shot converter, the timeline
bar, properties widgets, scene tree widget, settings, recent files.

Goes: `SceneWorld` tables, pydantic components as the document, JSON scenes,
the pygfx viewport and its gizmo, snapshot undo.

## Order of work

1. Build spike: OpenUSD with imaging against Python 3.13 and PySide6, Windows
   first. Render the Kitchen set and the walking HumanFemale in a
   `QOpenGLWidget`, pick a prim, measure load time and fps. Go or no-go, and
   it defines what CI must cache per platform.
2. New app package beside the old one: stage document, Hydra view, outliner,
   until it reaches parity on the hand-test list.
3. Attribute panel, undo, gizmo as guide prims, converters; then retire the
   old viewport.
