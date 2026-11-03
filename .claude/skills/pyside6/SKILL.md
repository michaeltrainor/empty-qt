---
name: pyside6
description: >-
  Write and debug PySide6/Qt widgets safely in mtqt: ownership, signal
  re-entrancy, deferred rebuilds, model/view, threading, and SIGSEGV
  triage. Use when editing PySide6 or Qt code, properties panels, item
  models, pytest-qt tests, or when the user reports a Qt crash, segfault,
  EXC_BAD_ACCESS, or a crash report mentioning QWidget/QAbstractSpinBox.
---

# PySide6 in mtqt

Shiboken wraps C++ Qt objects. Python references do not keep C++ widgets
alive past deletion; touching a freed or mid-event widget is a hard
`SIGSEGV`, not a Python exception.

Library code lives in `src/mtqt`. Project process (tests, lint, docs) is
in `AGENTS.md`. For crash-stack mapping, see [segfault-triage.md](segfault-triage.md).

## Hard rules

1. **Parent every `QObject`/`QWidget`** you create (pass `parent=` or add
   to a layout owned by a parent). Unparented widgets are easy to GC
   while Qt still holds a C++ pointer.
2. **Never destroy or rebuild the widget that is delivering the current
   event** (mouse press, `valueChanged`, `currentIndexChanged`, etc.).
3. **Never call `setValue` / `setText` / `setCurrentIndex` on the emitter
   (or its siblings you are about to tear down) from inside that same
   signal** without `blockSignals` + an `_updating` guard, or defer with
   `QTimer.singleShot(0, ...)`.
4. **UI and `QObject` methods only on the GUI thread.** Cross threads with
   signals/slots (queued), not direct widget calls.
5. **One `QApplication`.** Tests use `qtbot` and
   `QT_QPA_PLATFORM=offscreen` from `tests/conftest.py`. Do not construct
   `QApplication` in tests.
6. Prefer **in-place refresh** over tearing down a form when the model
   type is unchanged. See `PropertiesPanel.set_model`.

## Signal re-entrancy (most common segfault)

### Bad — rebuild while the spin is still in `mousePressEvent`/`stepBy`

```python
def on_value_changed(value: float) -> None:
    # Handler or slot runs during the spin's event.
    panel.clear()  # deletes the spin
    panel.set_model(new)  # or rebuilds the form
```

Classic crash: `QAbstractSpinBox::stepBy` → `QWidget::style()` with
`EXC_BAD_ACCESS` / null-ish address (`0x270`).

### Good — refresh in place, or defer teardown

```python
def set_model(self, model: BaseModel | None) -> None:
    if model is None:
        if self._committing:
            QTimer.singleShot(0, self._apply_clear)
        else:
            self._apply_clear()
        return
    if same_type_and_editors_exist:
        if self._committing:
            QTimer.singleShot(0, lambda m=model: self._apply_refresh(m))
        else:
            self._apply_refresh(model)
        return
    if self._committing:
        QTimer.singleShot(0, lambda m=model: self._apply_rebuild(m))
    else:
        self._apply_rebuild(model)
```

Patterns used in `src/mtqt/widgets/properties_panel.py`:

| Flag / tool | Role |
| --- | --- |
| `_updating` | Ignore editor signals while programmatically pushing values |
| `_committing` | True while handling a user edit; defer structural UI changes |
| `blockSignals(True)` | Stop `valueChanged` while `setValue`/`setText` |
| `QTimer.singleShot(0, …)` | Run clear/rebuild/refresh after the current event finishes |

When pushing values into editors:

```python
spin.blockSignals(True)
spin.setValue(value)
spin.blockSignals(False)
```

## Ownership and lifetime

- Pass `parent` into constructors: `QLabel("x", self)`, `QSpinBox(self)`.
- Layouts take ownership of widgets added to them when the layout is
  installed on a parent widget.
- Use `deleteLater()` instead of immediate delete when Qt may still be
  walking the object (scenes/items, deferred cleanup).
- Do not store raw pointers/ids to widgets and use them after the parent
  form was cleared. Hold the logical model; rebuild editors from it.
- Avoid `sip`/`shiboken` lifetime hacks; fix ownership instead.

## Models and views

- Emit the correct `beginInsertRows` / `endInsertRows` (and remove/reset)
  pairs; never mutate the backing store without notifying the model.
- Do not call into views or edit widgets from inside `data()` /
  `setData()` in ways that re-enter the same index.
- Invalid `QModelIndex`: check `index.isValid()` before use.
- Selection changes often cascade into property panels — treat that path
  like a signal handler (no synchronous teardown of the focused editor).

## Threading

```python
# Bad
worker_thread_fn()  # calls widget.setText(...)


# Good
class Worker(QObject):
    finished = Signal(str)


# Worker lives on worker thread; UI slot on main thread (queued connection).
worker.finished.connect(label.setText)
```

`QTimer`, `QNetworkAccessManager`, and most widgets require the thread
that owns them (almost always the main thread).

## Testing (pytest-qt)

```python
def test_widget_commits(qtbot) -> None:
    w = MyWidget()
    qtbot.addWidget(w)
    with qtbot.waitSignal(w.changed):
        w._spin.setValue(2)
```

- Use `qtbot.addWidget` so the widget is parented and cleaned up.
- After deferred `QTimer.singleShot(0, …)` UI, use `qtbot.waitUntil(...)`
  only when the code under test actually defers.
- Prefer asserting on public signals (`modelChanged`, `fieldChanged`) over
  poking private timers.

## Checklist before merging widget changes

- [ ] Editors created with a parent / added to an owned layout
- [ ] User-edit slots do not delete or rebuild the emitter synchronously
- [ ] Programmatic `setValue`/`setText` use `blockSignals` or `_updating`
- [ ] Same-type model updates refresh in place when possible
- [ ] No widget calls from worker threads
- [ ] pytest-qt coverage for commit paths that call back into `set_model`
- [ ] If fixing a segfault: add a regression test that previously would
      have torn down the editor mid-signal

## Related in this repo

- `src/mtqt/widgets/properties_panel.py` — deferred refresh/rebuild/clear
- `tests/test_properties_panel.py` — handler + vec spin regression
- `tests/conftest.py` — offscreen platform
- [segfault-triage.md](segfault-triage.md) — map crash stacks to causes
