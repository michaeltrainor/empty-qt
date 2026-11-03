---
paths:
  - "src/mtqt/**"
---

# mtqt library code

- mtqt ships `py.typed`, so type every public signature fully.
- The library never constructs a `QApplication`. Hosts own it, and standalone scripts call `ensure_application()`.
- Log with `logging.getLogger(__name__)`. No `print` (ruff `T20` enforces this).
- Use `pathlib.Path` at Python boundaries. Signals carry `str` paths because `Path` isn't a Qt metatype, so document that on the class.
- A new public widget or model needs all of: an export in its package `__init__` and in `mtqt.__all__`, pytest-qt tests under `tests/`, a section in `docs/widgets.md` or `docs/models.md`, and a changelog entry.
- Read the `pyside6` skill before changing widget lifetime, signals, or models.
