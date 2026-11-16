"""RangeSlider: setters stay silent, drags emit, handles do not cross."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent

from mtqt import RangeSlider


def _slider(qtbot) -> RangeSlider:
    slider = RangeSlider()
    qtbot.addWidget(slider)
    return slider


def _press(slider: RangeSlider, x: float, y: float = 12) -> None:
    local = QPoint(int(x), int(y))
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        local,
        slider.mapToGlobal(local),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(event)


def _move(slider: RangeSlider, x: float, y: float = 12) -> None:
    local = QPoint(int(x), int(y))
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        local,
        slider.mapToGlobal(local),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseMoveEvent(event)


def _release(slider: RangeSlider, x: float, y: float = 12) -> None:
    local = QPoint(int(x), int(y))
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        local,
        slider.mapToGlobal(local),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseReleaseEvent(event)


def test_setters_are_silent_and_order_values(qtbot) -> None:
    slider = _slider(qtbot)
    assert slider.objectName() == "RangeSlider"
    assert slider.accessibleName() == "Range"
    assert slider.minimumSizeHint().width() == 160
    assert slider.minimumSizeHint().height() == 24
    assert slider.bounds == (0.0, 100.0)
    assert slider.values == (0.0, 100.0)
    emitted: list[tuple[float, float]] = []
    slider.rangeChanged.connect(lambda lower, upper: emitted.append((lower, upper)))
    slider.set_values(90, 10)
    assert slider.values == (10.0, 90.0)
    slider.set_values(-5, 140)
    assert slider.values == (0.0, 100.0)
    slider.set_bounds(80, 0)
    assert slider.bounds == (0.0, 80.0)
    assert slider.values == (0.0, 80.0)
    assert emitted == []


def test_set_bounds_clamps_an_existing_range(qtbot) -> None:
    slider = _slider(qtbot)
    slider.set_values(20, 80)
    emitted: list[tuple[float, float]] = []
    slider.rangeChanged.connect(lambda lower, upper: emitted.append((lower, upper)))
    slider.set_bounds(0, 50)
    assert slider.bounds == (0.0, 50.0)
    assert slider.values == (20.0, 50.0)
    assert emitted == []


def test_drag_emits_the_new_pair(qtbot) -> None:
    slider = _slider(qtbot)
    with qtbot.waitSignal(slider.rangeChanged) as blocker:
        slider._drag_to(0, 25.0)
    assert blocker.args == [25.0, 100.0]
    assert slider.values == (25.0, 100.0)
    with qtbot.waitSignal(slider.rangeChanged) as blocker:
        slider._drag_to(1, 80.0)
    assert blocker.args == [25.0, 80.0]
    assert slider.values == (25.0, 80.0)
    emitted: list[tuple[float, float]] = []
    slider.rangeChanged.connect(lambda lower, upper: emitted.append((lower, upper)))
    slider._drag_to(0, 25.0)
    assert emitted == []


def test_drag_cannot_cross_the_other_handle(qtbot) -> None:
    slider = _slider(qtbot)
    slider.set_values(20, 40)
    with qtbot.waitSignal(slider.rangeChanged) as blocker:
        slider._drag_to(0, 90.0)
    assert blocker.args == [40.0, 40.0]
    assert slider.values == (40.0, 40.0)
    emitted: list[tuple[float, float]] = []
    slider.rangeChanged.connect(lambda lower, upper: emitted.append((lower, upper)))
    slider._drag_to(1, 10.0)
    assert slider.values == (40.0, 40.0)
    assert emitted == []


def test_mouse_drag_emits_and_paints(qtbot) -> None:
    slider = _slider(qtbot)
    slider.resize(200, 24)
    slider.show()
    slider.repaint()
    with qtbot.waitSignal(slider.rangeChanged) as blocker:
        _press(slider, 30)
        _move(slider, 120)
        _release(slider, 120)
    assert slider.values[1] == 100.0
    assert slider.values[0] == blocker.args[0]
    assert slider.values[0] > 10.0
