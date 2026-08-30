# Getting started

Qt is a C++ toolkit with a Python binding. Three rules matter before any widget:

## One QApplication

A process has at most one `QApplication`. Hosts (Maya, Houdini, a larger tool) already created it. **mtqt widgets never construct one.**

Standalone scripts may call `ensure_application()`:

```python
from mtqt import LabeledLineEdit, apply_stylesheet, ensure_application

app = ensure_application()
field = LabeledLineEdit("Name")
apply_stylesheet(field, "base", "widgets")
field.show()
app.exec()
```

`ensure_application()` is a no-op when an instance already exists. It does not call `exec()` or `sys.exit()`. Tests should use pytest-qt’s `qtbot`, which owns the application.

## Parent ownership vs Python GC

A parented `QWidget` is owned by Qt. An unparented widget is owned by Python. If Python garbage-collects the wrapper, the C++ object is destroyed and you get `RuntimeError: Internal C++ object already deleted`.

- Pass `parent=` to every widget (the window or a layout’s host).
- Keep a Python reference to top-level windows (`window.show()` is not enough if `window` is a local that goes out of scope before `exec()`).
- Do not do `QLabel("x").show()` — there is no owner.

## Headless tests

`tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` **before** PySide6 is imported so pytest can run without a display.
