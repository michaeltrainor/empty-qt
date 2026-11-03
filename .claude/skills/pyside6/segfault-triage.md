# PySide6 segfault triage

macOS crash reports often show `EXC_BAD_ACCESS (SIGSEGV)` with a tiny
fault address (`0x0`, `0x10`, `0x270`). That usually means a null or
nearly-null C++ `this` / d-pointer after the Python wrapper outlived the
Qt object, or after re-entrant destruction.

## Map the top frames

| Top of stack | Likely cause | Fix direction |
| --- | --- | --- |
| `QWidget::style` ← `QAbstractSpinBox::stepBy` ← `mousePressEvent` | Spin (or its form) deleted or `setValue`'d while still stepping | Defer clear/rebuild/refresh; in-place update; don't `set_model` rebuild mid-slot |
| `QWidget::event` / `QApplication::notify` after your slot | Slot destroyed `sender()` or a parent of it | `QTimer.singleShot(0, …)` for teardown; keep sender alive until event returns |
| `QComboBox` / `QListView` during `currentIndexChanged` | Model or combo cleared in the same slot | Block signals, defer reset, or update items without resetting the widget |
| `QAbstractItemModel` / view paint or `data` | Model reset or row removal while view is painting / querying | Correct begin/end notify; don't reset from inside `data()` |
| Random QtGui after background work | Widget touched off the GUI thread | Move UI updates to main-thread slots via signals |
| Crash only on window close | Order of destruction; child using parent already gone | Parents own children; stop timers in `closeEvent`; disconnect before delete |

## Confirm re-entrancy

Ask: “Does this stack sit inside a signal emitted by a widget my code just
deleted or mutated?”

If yes:

1. Prefer **in-place** value refresh (`blockSignals` + set).
2. Else **defer** structural changes with `QTimer.singleShot(0, callback)`.
3. Guard programmatic updates with `_updating` so editor signals no-op.
4. Add a pytest-qt test where a handler calls `set_model`/`clear` during
   `valueChanged` (see `test_properties_panel_handler_set_model_keeps_vec_spin`).

## What not to blame first

- QSS / style sheets rarely cause null `style()` crashes; the widget is
  usually already dead.
- “Python GC” is a symptom of missing Qt parent ownership, not a separate
  class of bug.
- Upgrading PySide alone without fixing re-entrancy will not stick.

## Minimal reproduction pattern

```python
def test_no_teardown_during_spin_commit(qtbot) -> None:
    panel = PropertiesPanel()
    qtbot.addWidget(panel)

    def on_field(_f, _v, model) -> None:
        panel.set_model(model)  # must not destroy the spin synchronously

    panel.register_handler(on_field)
    panel.set_model(sample_with_vec)
    spin = panel._form_host.findChild(_VecEditor)._spins[0]
    spin_id = id(spin)
    with qtbot.waitSignal(panel.modelChanged):
        spin.setValue(1.25)
    assert id(panel._form_host.findChild(_VecEditor)._spins[0]) == spin_id
```

If this crashes the interpreter, the panel still tears down mid-commit.
