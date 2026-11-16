# USD-centric architecture (plan)

Status: proposal, 2026-11-07; build spike passed 2026-11-14 (see [Build spike
results](#build-spike-results)). Not implemented. The scene viewport today is a
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
   it defines what CI must cache per platform. Done, go (results below).
2. New app package beside the old one: stage document, Hydra view, outliner,
   until it reaches parity on the hand-test list.
3. Attribute panel, undo, gizmo as guide prims, converters; then retire the
   old viewport.

## Build spike results

Run on 2026-11-14 on Windows 11, an RTX 3080 Ti (OpenGL 4.6), 32 cores.
`scripts/build_usd_imaging.cmd` builds OpenUSD v26.11.1 with `--usd-imaging
--python --materialx` against the repository's `.venv` interpreter (uv's
CPython 3.13.14, which ships headers and the import library). The build took
13 minutes, two of them for oneTBB, OpenSubdiv, and MaterialX; the install is
about 80 DLLs plus a `pxr` package in `inst\Lib\site-packages`. No PyPI wheel
covers this: `usd-core` 26.11 and the newer `pxr-usd` 26.8 are both core only.

`scripts/hydra_spike.py` is the prototype: a `QOpenGLWidget` hosting
`UsdImagingGL.Engine` the way usdview's `StageView` does (render buffer size,
framing, camera state, `Glf.SimpleLight` lighting, the `color` AOV), with an
orbit camera, click picking through `TestIntersection`, stage-time playback,
and `--bench N` / `--screenshot` flags. Only `HdStormRendererPlugin` is
registered. Numbers from `--bench`, window 1280x800:

| Stage | Prims / meshes | Stage open | First image | `Render()` mean | Playback | Pick |
| --- | --- | --- | --- | --- | --- | --- |
| Kitchen set | 2742 / 1788 | 0.12 s | 0.2 to 0.6 s | 1.5 ms | 119 fps (vsync) | 21 ms |
| HumanFemale.walk (UsdSkel) | 1452 / 89 | 0.10 s | 0.6 to 1.7 s | 7.5 ms | 105 fps | 32 ms |

The pygfx viewport drew the Kitchen set at about 4 fps after a 15 s load. The
skinned walk cycle plays with no app code: UsdSkel is resolved by imaging.
First-image time varies between runs with Storm's shader cache.

Things the prototype had to get right, for step 2:

- Destroy the engine while the GL context is current (`makeCurrent()` in an
  `aboutToQuit` slot). Letting Python drop it after the context is gone
  prints an `HgiGL` error per texture and sampler.
- A `Glf.SimpleLight` dome light without a texture warns on every frame; the
  spike uses a camera light only. The real view should author a `DomeLight`
  prim in the session layer instead.
- `TestIntersection(PickParams, ...)` returns a list of `IntersectionResult`
  objects (`hitPrimPath`, `hitInstanceIndex`), not the legacy tuple.
- `--imaging` in `build_usd.py` means Hd without UsdImaging; `--usd-imaging`
  is the full set. usdview needs PyOpenGL in the build interpreter, so it is
  skipped; the viewer gets PyOpenGL from `uv run --with PyOpenGL`.
- With `PYTHONPATH` pointing at the install, the built `pxr` shadows the
  `usd-core` wheel in `.venv`; the two must never be mixed in one process.

CI consequence: the Windows job needs a cached `inst\` keyed on the OpenUSD
tag, the MSVC version, and the Python version, or the Hydra tests stay
Windows-local until that cache exists. Linux and macOS builds are not tried
yet.
