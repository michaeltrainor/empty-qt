# Stage editor

`apps/stage_editor` edits and inspects an OpenUSD stage. The `Usd.Stage` is
the document and Hydra Storm draws it. Its inspection panels and flags
follow `usdview`; its editing tools (tabs, gizmo, Create menu, eject to
layer) follow the scene viewport. The app is unpublished and lives in the
same uv workspace as mtqt.

## Running it

The viewport needs `pxr.UsdImagingGL`, which the `usd-core` wheel does not
ship. Build OpenUSD with imaging once (see [Development](development.md#stage-editor)),
then:

In PowerShell:

```powershell
$env:PYTHONPATH = "$env:USERPROFILE\dev\openusd\inst\Lib\site-packages"
$env:PATH = "$env:USERPROFILE\dev\openusd\inst\lib;$env:USERPROFILE\dev\openusd\inst\bin;$env:PATH"
uv run --package stage-editor stage-editor path\to\scene.usd
```

In `cmd.exe`:

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

## Editing

**Tabs.** Every open stage is a tab over the viewport. The Outliner and
Layers panels on the left, the Inspector on the right, the timeline, and
the bottom panel all follow the active tab, and so do Undo and Redo. File >
Open replaces the current tab; File > Open in New Tab adds one.

**Tools.** The toolbar's **Select** (Q), **Move** (W), **Rotate** (E), and
**Scale** (R) work like the scene viewport's; **World** and **Local** pick
the axes for move and rotate (scale always uses the prim's own axes). A
gizmo appears on the selected prims; drag an arrow, ring, or box. Each drag
is one undo step. The prim keeps its own transform ops when they are a
translate/rotate/scale/pivot stack or a single matrix; any other stack is
replaced by one matrix op. Animated transforms get a sample at the current
frame.

**Create, rename, delete.** The Create menu (and the outliner's context
menu) adds Xforms, Scopes, primitive shapes, cameras, lights, and MaterialX
materials under the selected prim or the default prim. F2 or a slow
double-click renames; drag rows in the outliner to reparent; Ctrl+D
duplicates; Ctrl+G groups; Delete removes. Renames and moves fix the
relationships and connections that point at the moved prims. A prim that
comes from a referenced file cannot be removed from it, so Delete
deactivates it instead, and Duplicate adds a new prim that references it.

**Materials.** Create > MaterialX Material makes a `standard_surface`
material under `Looks` with its common inputs authored; select its
`Surface` shader to edit them in the Inspector. The outliner's Bind
Material menu binds or unbinds. Storm renders MaterialX directly.

**Eject to layer.** Select a prim and choose Layer > Eject to Layer
(Ctrl+E). The prim moves into its own file, `<scene>_assets/<Name>.usda`
beside the saved scene, the scene references that file at the same path,
and the file opens in a new tab. Edit anything there; both tabs share the
layer, so the scene tab shows each change at once, and Save in the new tab
writes the file. The scene tab's Save writes the reference.

- Composition inside the prim (references, payloads, variants, instancing)
  is copied as authored, not flattened. Relative asset paths are rewritten
  for the new folder.
- Opinions that point outside the prim, such as a material binding to
  `/World/Looks/Wood`, cannot live in the new file, so they stay in the
  scene layer as overs.
- A prim that comes from a referenced file (anything inside a file you
  opened, which the editor references under `/World`) is copied into the
  new file, and that file is marked *shadowed*: the original still
  composes underneath, so Delete there deactivates instead of removing.
- Layer > Open Ejected Layer reopens it, and Layer > Merge Back folds it
  back into the scene layer and removes the reference; the file stays on
  disk. Eject and Merge Back can be undone.

## Panels

The layout follows the scene viewport: the Outliner and Layers on the left,
the viewport tabs in the middle, the Inspector on the right, and the
timeline below. Tools > Validate Stage and Tools > Python Interpreter open
a panel under the viewport; Tools > Hide Bottom Panel closes it. The
splitter sizes and the Display settings are remembered between runs.

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

- **File**: New, Open, Open in New Tab, Add Reference, Save, Save As,
  Save All, Save Flattened As (the composed stage as one layer), Save
  Viewer Image, and Close Tab.
- **Edit**: Undo, Redo, Delete, Duplicate, Rename, Group, Find Prim,
  Copy Prim Path.
- **Create**: Xform, Scope, geometry, camera, lights, MaterialX Material.
- **Layer**: Eject to Layer, Open Ejected Layer, Merge Back.
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
Layers. Save writes the root layer and any dirty sublayers it owns. Every
edit can be undone: attribute values, variant selections, gizmo drags,
create, delete, rename, reparent, duplicate, material binding, eject, and
merge back.

View state goes to the stage's session layer, which is never saved and
does not mark the scene as changed. That covers visibility and active
overrides from the outliner and the Display menu's dome light. Like
usdview's toggles, these overrides are not undoable; use Reset Session
Overrides instead.

## Limits

- Only Storm is built, so there is no renderer switch.
- Arrays and matrices are read-only in the inspector.
- The gizmo moves prims, not pivots; there is no snapping yet.
- Eject needs a saved scene, because the new file goes beside it.
- The dome light has no texture, so Storm logs one warning when it is
  turned on.
- usdview plugins and debug flags (`TfDebug`) are not supported.
