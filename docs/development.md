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

- Google-style docstrings on public APIs
- `pathlib.Path` at Python boundaries
- `logging.getLogger(__name__)` — no `print` in the library
- ruff `E`, `F`, `I`, `UP` and `ruff format`; pyright `standard`
