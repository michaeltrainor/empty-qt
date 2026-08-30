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

## Conventions

- Google-style docstrings on public APIs
- `pathlib.Path` at Python boundaries
- `logging.getLogger(__name__)` — no `print` in the library
- ruff `E`, `F`, `I`, `UP` and `ruff format`; pyright `standard`
