# mtqt

A small **library** of PySide6 widgets, item models, and Qt Style Sheets. It is meant to be imported by a standalone tool or a DCC-hosted panel — it is not an application.

## Requirements

| Pin | Version | Why |
| --- | --- | --- |
| Python | 3.13.x | [VFX Reference Platform](https://vfxplatform.com/#reference-platform) CY2026 |
| PySide6 | 6.8.x (`>=6.8.0.2`) | Same platform; 6.8.0.2 is the first 6.8 wheel that installs on 3.13 |

CY2025 hosts on Python 3.11 cannot install this package. That is intentional.

## Install

```bash
uv add "mtqt"
pip install mtqt
```

From a source checkout:

```bash
uv sync --extra dev --extra docs
```

## Gallery

```bash
uv run python examples/gallery.py
```

The gallery lives in `examples/` so a pip/uv install of `mtqt` stays library-only.

## License

mtqt is MIT. PySide6 6.8 wheels from PyPI are typically LGPL; see Qt’s licensing for your distribution.

## Later

Custom C++ widgets (Shiboken6 + scikit-build-core), DCC `wrapInstance` helpers, i18n, a theme engine, and hosted docs are out of scope for 0.1.0.
