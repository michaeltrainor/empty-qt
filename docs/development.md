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

Run and Debug in VS Code or Cursor lists these configurations (`.vscode/launch.json`, debugpy, integrated terminal):

- Gallery — `examples/gallery.py`
- LabeledLineEdit — `examples/labeled_line_edit.py`
- PathPicker — `examples/path_picker.py`
- SceneGraphTree — `examples/scene_graph_tree.py`
- NodeGraph — `examples/node_graph.py`
- DropWidget — `examples/drop_widget.py`
- DropWidget module — `src/mtqt/widgets/drag_n_drop.py`
- pytest — `tests/`

`uv run pytest` runs the suite. Tests use the `qtbot` fixture. `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`. Do not construct `QApplication` in tests.

## Conventions

- Google-style docstrings on public classes, functions, and methods that are mtqt's own API. Summary line, then Args / Returns / Raises when they apply. Match `src/mtqt/widgets/labeled_line_edit.py`. Qt virtual overrides (`paintEvent`, `rowCount`, `data`, and similar) do not need a docstring when they only implement the Qt contract. `__init__` is documented on the class. Ruff enforces this with pydocstyle convention `google` (see `pyproject.toml`). `D102` is ignored because Qt overrides are public methods; still document new public Python methods.
- `pathlib.Path` at Python boundaries
- `logging.getLogger(__name__)` — no `print` in the library
- ruff select `E`, `F`, `I`, `UP`, `D`, then `ruff format`. `uv run pyright` (mode `standard`) is a local check, not a CI job.
- Do not add a node-type plugin loader, evaluator, or palette inside mtqt. Node catalogs belong in the host tool.

## Pull requests

Before a pull request is marked ready for review:

- Google-style docstrings on new public API, as in Conventions above.
- pytest-qt tests under `tests/` for every public widget, using the `qtbot` fixture. Run `uv run pytest`.
- `uv run ruff check` and `uv run ruff format` are clean.
- MkDocs pages under `docs/` are updated for anything a user or contributor would notice (new widgets, examples, commands, or the PR process). `mkdocs.yml` uses `docstring_style: google`. Verify with `uv run mkdocs build --strict` after `uv sync --extra dev --extra docs`.
- `CHANGELOG.md` is updated under `## Unreleased` in the same PR, before merge. Do not add a follow-up commit after merge.

When a pull request is ready for review (not a draft), the `pr-reviewer` agent reviews it and squash-merges it if the review passes. Do not merge a ready PR in place of `pr-reviewer`.

CI (`.github/workflows/ci.yml`) runs `uv run ruff check`, `uv run ruff format --check`, `uv run pytest`, and `uv run mkdocs build --strict`. `.github/workflows/pr-ready.yml` fails a ready PR that does not update `CHANGELOG.md`, or that changes `src/`, `examples/`, `.github/`, or `pyproject.toml` without updating `docs/`. That workflow does not merge.
