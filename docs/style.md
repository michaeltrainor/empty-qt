# Style

Stylesheets live as `.qss` files next to the Python package, not as giant strings. `uv_build` includes them in the wheel because they sit under `src/mtqt/`.

## Load and apply

```python
from mtqt import apply_stylesheet, load_stylesheet

text = load_stylesheet("base", "widgets")
apply_stylesheet(window, "base", "widgets")
```

`apply_stylesheet` calls `setStyleSheet` on **the target you pass**. Prefer your tool’s top-level widget. Applying to `QApplication` restyles every widget in the process, including DCC chrome.

Sheets: `base` (windows, tabs, views) and `widgets` (`LabeledLineEdit`, `#PathPicker`, `[invalid="true"]`).

## Targeting custom widgets

Composite widgets set `objectName` (`LabeledLineEdit`, `PathPicker`). QSS can use the class name, `#PathPicker`, and dynamic properties. After `setProperty("invalid", True)` the widget is polished so Qt reapplies the sheet.

QSS does **not** restyle custom `paintEvent` drawing or most item-view delegates. Those need a painter or a delegate.

v1 is not a theme engine: colors are literals in the `.qss` files. The palette is neutral mid-gray, close to Maya's UI, and the token list is at the top of `base.qss`.
