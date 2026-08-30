# Embedding

mtqt is safe to import from a panel that already has a Qt event loop.

## Existing QApplication

Do not call `ensure_application()` from library code or from a DCC plugin if the host created the app. Call it only from standalone scripts.

Never call `app.exec()` or `sys.exit()` inside `src/mtqt`.

## Scoped QSS

```python
from mtqt import apply_stylesheet

apply_stylesheet(my_tool_window, "base", "widgets")
```

Do not default to `QApplication.setStyleSheet`. Host applications already style menus, docks, and the Maya/Houdini shell.

## GUI thread

All widgets, models, and `setStyleSheet` calls belong on the thread that owns `QApplication` (the GUI thread). v1 has no `QThread` helpers.

## Ownership reminder

Parent every widget. Keep a Python reference to your top-level window for as long as it is visible. See [Getting started](getting-started.md).
