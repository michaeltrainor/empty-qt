# mtqt

Reusable [PySide6](https://doc.qt.io/qtforpython/) widgets, item models, and QSS for production Qt tools. Python **3.13.x** and PySide **6.8.x** follow the [VFX Reference Platform](https://vfxplatform.com/#reference-platform) CY2026.

This package is MIT licensed. PySide6 6.8 is LGPL for typical PyPI wheels; your application must follow Qt’s terms.

## Install

```bash
uv add mtqt
# or
pip install mtqt
```

From a clone:

```bash
uv sync --extra dev --extra docs
```

## Try it

```bash
uv run python examples/gallery.py
uv run mkdocs serve
```

Guides and API: run MkDocs locally (see [development](docs/development.md)).
