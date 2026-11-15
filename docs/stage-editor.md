# Stage editor

`apps/stage_editor` edits and inspects an OpenUSD stage. The `Usd.Stage` is
the document and Hydra Storm draws it. Its panels and flags follow
`usdview`, so anything you look for there has a place here. The app is
unpublished and lives in the same uv workspace as mtqt.

## Running it

The viewport needs `pxr.UsdImagingGL`, which the `usd-core` wheel does not
ship. Build OpenUSD with imaging once (see [Development](development.md#stage-editor)),
then:

```bat
set PYTHONPATH=%USERPROFILE%\dev\openusd\inst\Lib\site-packages
set PATH=%USERPROFILE%\dev\openusd\inst\lib;%USERPROFILE%\dev\openusd\inst\bin;%PATH%
uv run --package stage-editor stage-editor path\to\scene.usd
```

Without the imaging build the window still opens, with every panel except
the viewport.

## Command line

| Flag | Effect |
| --- | --- |
| `file` | USD file to open. A file the editor did not write is referenced under `/World` of a new untitled scene. |
| `--camera`, `--cam` | Look through a camera, by prim path or by name. |
| `--unloaded` | Open with every payload unloaded; load them from the outliner. |
| `--mask` | Comma-separated prim paths to populate (a stage population mask). |
| `--complexity`, `-c` | `low`, `medium`, `high`, or `veryhigh` subdivision. |
| `--draw-mode` | `shaded_smooth`, `wireframe`, `points`, and the other draw modes. |
| `--select` | Comma-separated prim paths to select. |
| `--cf`, `--frame` | Time code to show first. |

Paths given to `--mask`, `--select`, and `--camera` are paths in the file
you open; the editor maps them under `/World` when it references the file.

## Panels

Panels dock with the Qt Advanced Docking System. Drag tabs to rearrange
them; **Window** shows hidden panels and resets the layout. The layout and
the Display settings are remembered between runs.

**Viewport.** Left drag tumbles, middle drag trucks, right drag or the
wheel dollies, and a click picks. **F** frames the selection and **A** the
stage. The heads-up display shows render time, prim counts, the camera,
and the frame.

**Outliner.** Name, type, and computed visibility (click **V**/**I** to
toggle). Colours follow usdview: orange prims have composition arcs,
blue ones are instances, violet ones are prototypes, and grey ones are
inactive. Italic prims are overs, classes, or unloaded payloads. **Show**
lists inactive, undefined, and abstract prims and prototypes; the search
box keeps matches and their ancestors. The context menu frames, looks
through cameras, copies paths, sets visibility (Make Visible, Make
Invisible, Vis Only), activates or deactivates, loads or unloads
payloads, and resets session overrides.

**Inspector.**

- *Properties* lists attributes and relationships at the current time,
  with the computed world transform, world bound, and bound material.
  The Source column says where each value comes from (time samples,
  default, fallback, or none) in usdview's colours. Selecting a row shows
  the whole value, its time samples, and its authored metadata. Double-click
  a value to edit it.
- *Metadata* lists the composed prim metadata and offers each variant set
  as a drop-down.
- *Layer Stack* lists every spec that contributes to the prim, strongest
  first; selecting one shows it as `usda`.
- *Composition* lists the composition arcs and shows the Pcp prim index.

**Layers.** The session and root layer stacks, with sublayers nested, and
every layer the stage reaches through references and payloads.
Double-click a local layer to make it the edit target (the status bar
names it). The context menu mutes, reloads, saves a dirty sublayer, shows
a layer as `usda`, and adds an existing or new sublayer.

**Validation** (Tools > Validate Stage). Runs OpenUSD's `UsdValidation`
validators, the checks `usdchecker` runs, and lists errors, warnings, and
notes. Clicking an issue selects its prims; **Apply Fix** runs a
validator's fixer on the edit target when one exists. **Validators**
chooses which groups run.

**Interpreter** (Tools > Python Interpreter). A Python console with the
USD modules imported and an `api` object: `api.stage`, `api.prim` (the
first selected prim), `api.selection`, `api.time`, and `api.select([...])`.

**Timeline.** Plays, scrubs, and loops stage time. Left and Right step a
frame.

## Menus

- **File**: New, Open, Add Reference, Save, Save As, Save Flattened As
  (the composed stage as one layer), and Save Viewer Image.
- **Edit**: Undo, Redo, Find Prim, Copy Prim Path.
- **Display**: draw mode, complexity, purposes (guide, proxy, render),
  lights (camera light, dome light, scene lights), scene materials,
  backface culling, selection bounds, HUD, render output (AOV), and
  background.
- **Camera**: the free camera or any camera on the stage. Moving the view
  while looking through a camera continues from that camera as the free
  camera.
- **Select**: the pick mode (prims, models, or instances).

## What is saved where

Edits go to the edit target, the root layer unless you choose another in
Layers. Save writes the root layer and any dirty sublayers it owns.
Attribute edits and variant selections can be undone.

View state goes to the stage's session layer, which is never saved and
does not mark the scene as changed. That covers visibility and active
overrides from the outliner and the Display menu's dome light. Like
usdview's toggles, these overrides are not undoable; use Reset Session
Overrides instead.

## Limits

- Only Storm is built, so there is no renderer switch.
- Arrays and matrices are read-only in the inspector.
- The dome light has no texture, so Storm logs one warning when it is
  turned on.
- usdview plugins and debug flags (`TfDebug`) are not supported.
