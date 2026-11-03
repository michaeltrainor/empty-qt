# Development

```bash
uv sync --extra dev --extra docs
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
uv run mkdocs serve
uv run mkdocs build
uv build
```

Install git hooks (after extras are synced):

```bash
uv run pre-commit install
```

`pip` users can `pip install -e ".[dev,docs]"`.

## Layout

Editable install from `src/mtqt`. Gallery: `uv run python examples/gallery.py`. Confirm `.qss` files are inside the wheel after `uv build` (zip listing of `dist/*.whl`).

## Run one widget

Run a single launcher without opening the gallery:

```bash
uv run python examples/labeled_line_edit.py
```

`examples/properties_panel.py` shows `PropertiesPanel` with a small pydantic model.

`examples/scene_viewport` draws with rendercanvas and pygfx. Numba builds mesh primitives. The default scene is a directional key light plus a Maya-style XZ viewport grid (chrome only). Toolbar tools: Select / Move / Rotate / Scale (Q/W/E/R) and World/Local (default **world** for new objects and editing). Use Create / Delete for empties, meshes, and lights. File menu saves/loads JSON scenes; Edit covers duplicate, rename, isolate, and frame. Selectable objects are **transforms**; shapes attach as components. `PropertiesBook` tabs show Transform / Mesh / Light. **Settings → Preferences** edits viewport chrome and lighting prefs (persisted under `~/.config/mtqt/scene_viewport.json`). That dependency group is not part of a normal sync:

```bash
uv sync --extra dev --extra docs --group examples
uv run --group examples python -m examples.scene_viewport
```

### Scene viewport gizmos

Maya-style tools via `MayaTransformGizmo` (`examples/scene_viewport/gizmo.py`), wrapping `pygfx.TransformGizmo`:

- **Tools** (toolbar + hotkeys): **Q** select, **W** move, **E** rotate, **R** scale; **D** edit pivot (affect pivot only).
- **Shading** (**1**–**6**): bounding box, wireframe on shaded, flat, wireframe, shaded, textured (optional `albedo_map` on Mesh).
- **Document model**: `SceneWorld` owns transform/mesh/light tables and indexes; not a full ECS.
- **Space**: toolbar World / Local (default world). Session preference drives the gizmo and Transform tab; TRS stays stored local.
- **Pivot**: each transform stores a local `pivot`; the gizmo sits on a proxy at that offset (average of selection for multi-select; orientation from the last selected). Edit Pivot moves the offset without moving geometry.
- **Hierarchy**: Edit → Parent to Last Selected (**P**) / Unparent (**Shift+P**) keep world pose.
- **Undo**: Ctrl+Z / Ctrl+Y for create, delete, TRS, pivot, reparent, rename, and visibility.
- **Session**: File → New / Open / Save; Ctrl+D duplicate; F2 rename; **F** frame; Ctrl+I isolate / Ctrl+Shift+I show all; `visible` on the Transform tab.
- **Look**: meshes use Standard/PBR (roughness/metalness + procedural IBL); optional albedo path; selection is outline-only; Preferences cover exposure, gamma, skybox/gradient, and shadow bias / extent / cull (keep bias tiny so contact shadows stay attached). Point/spot shadows pin perspective near/far after pygfx's per-frame reset. No shared material library.
- **Write-back**: Drag updates the local store; the properties book refreshes on pointer release.
- **Not in scope yet**: snapping, screen space, custom HDRIs.

Run and Debug in VS Code or Cursor lists these configurations (`.vscode/launch.json`, debugpy, integrated terminal):

- Gallery — `examples/gallery.py`
- LabeledLineEdit — `examples/labeled_line_edit.py`
- PathPicker — `examples/path_picker.py`
- PropertiesPanel — `examples/properties_panel.py`
- SceneGraphTree — `examples/scene_graph_tree.py`
- SceneViewport — `examples.scene_viewport` (module)
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

This project is managed with Claude Code. `AGENTS.md` holds the project rules. `.claude/agents/pr-reviewer.md` is the review and merge agent, `.claude/skills/pyside6/` is the Qt safety skill, and `.claude/settings.json` sets shared permissions and a hook that runs `ruff format` on edited Python files. Put personal overrides in `.claude/settings.local.json`, which is gitignored.

## Pull requests

Before a pull request is marked ready for review:

- Google-style docstrings on new public API, as in Conventions above.
- pytest-qt tests under `tests/` for every public widget, using the `qtbot` fixture. Run `uv run pytest`.
- `uv run ruff check` and `uv run ruff format` are clean.
- MkDocs pages under `docs/` are updated for anything a user or contributor would notice (new widgets, examples, commands, or the PR process). `mkdocs.yml` uses `docstring_style: google`. Verify with `uv run mkdocs build --strict` after `uv sync --extra dev --extra docs`.
- `CHANGELOG.md` is updated under `## Unreleased` in the same PR, before merge. Do not add a follow-up commit after merge.

When a pull request is ready for review (not a draft), the `pr-reviewer` agent reviews it and squash-merges it if the review passes and every CI check has finished and passed. A local run does not replace CI. Do not merge a ready PR in place of `pr-reviewer`.

CI (`.github/workflows/ci.yml`) syncs the `examples` dependency group, then runs `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest --cov-fail-under=80`, and `uv run mkdocs build --strict`. Without that group, the scene viewport tests that need pygfx skip locally. Its `platforms` job runs the tests on macOS and Windows. Branch protection requires the `check` job by name, so do not turn `check` into a matrix.

`.github/workflows/qt-latest.yml` tests the newest PySide6 weekly as an early warning, and never blocks a PR. Dependabot opens weekly update PRs, and leaves Python and PySide6 alone because they follow the VFX Reference Platform.

Tests fail on Qt warnings (`qt_log_level_fail`). Add a pattern to `qt_log_ignore` in `pyproject.toml` only for noise from the test platform. `.github/workflows/pr-ready.yml` fails a ready PR that does not update `CHANGELOG.md`, or that changes `src/`, `examples/`, `.github/`, or `pyproject.toml` without updating `docs/`. That workflow does not merge.
