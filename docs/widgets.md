# Widgets

v1 ships two composite widgets. Both take `parent: QWidget | None = None`, set `objectName` for QSS, and set an accessible name.

## Composition, not multiple inheritance

`LabeledLineEdit` **contains** a `QLabel` and a `QLineEdit` in a layout. That is the usual way to build a reusable control. Multiple inheritance (`class Foo(QWidget, SomeMixin)`) is rarely worth it in PySide6.

```python
from mtqt import LabeledLineEdit

field = LabeledLineEdit("Shot", parent=window, placeholder="seq010_sh010")
field.textChanged.connect(print)
field.validityChanged.connect(lambda ok: print("valid" if ok else "invalid"))
```

When `required=True` (the default), empty text sets the dynamic property `invalid` so QSS can draw a red border.

## Nested widgets and dialogs

`PathPicker` is a line edit plus Browse. File dialogs return `str`; the public Python API is `pathlib.Path`. `pathChanged` emits `str` because `Path` is not a Qt metatype — read the `path` property in Python.

```python
from pathlib import Path
from mtqt import PathPicker

picker = PathPicker(parent=window, caption="Scene file")
picker.path = Path("/tmp/scene.ma")
picker.pathChanged.connect(lambda s: print(Path(s) if s else None))
```

Do not grow this catalog in a drive-by change; add widgets deliberately.
