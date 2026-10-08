"""Pan gestures on the node-graph view."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QGraphicsView, QLineEdit

from mtqt import NodeGraph, NodeGraphWidget
from mtqt.widgets.node_graph import Node
from mtqt.widgets.node_graph.view import NodeGraphView


def _host(qtbot, graph: NodeGraph | None = None) -> NodeGraphWidget:
    widget = NodeGraphWidget(graph, frame_on_show=False)
    widget.resize(640, 480)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    widget.view.set_zoom(1.0)
    return widget


def _center(view: NodeGraphView) -> QPointF:
    return view.mapToScene(view.viewport().rect().center())


def _drag(
    qtbot,
    view: NodeGraphView,
    button: Qt.MouseButton,
    start: QPoint,
    delta: QPoint,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> tuple[QPointF, QPointF]:
    before = _center(view)
    viewport = view.viewport()
    end = start + delta
    qtbot.mousePress(viewport, button, modifier, pos=start)
    qtbot.mouseMove(viewport, end)
    qtbot.mouseRelease(viewport, button, modifier, pos=end)
    return before, _center(view)


def _assert_panned(before: QPointF, after: QPointF) -> None:
    assert abs(before.x() - after.x()) > 10 or abs(before.y() - after.y()) > 10


def _assert_still(before: QPointF, after: QPointF) -> None:
    assert abs(before.x() - after.x()) < 2
    assert abs(before.y() - after.y()) < 2


def _focus_canvas(qtbot, view: NodeGraphView) -> None:
    view.activateWindow()
    view.setFocus(Qt.FocusReason.OtherFocusReason)
    qtbot.waitUntil(lambda: view.hasFocus() or view.viewport().hasFocus())


def test_alt_left_drag_pans_even_when_press_starts_on_a_node(qtbot) -> None:
    graph = NodeGraph()
    graph.add_node(Node("a", "A", position=(40, 30)))
    widget = _host(qtbot, graph)
    view = widget.view
    item = widget.scene.node_item("a")
    assert item is not None
    view.centerOn(item)
    origin = QPointF(item.pos())
    start = view.mapFromScene(item.sceneBoundingRect().center())
    before, after = _drag(
        qtbot,
        view,
        Qt.MouseButton.LeftButton,
        start,
        QPoint(160, 40),
        Qt.KeyboardModifier.AltModifier,
    )
    _assert_panned(before, after)
    assert abs(item.pos().x() - origin.x()) < 1
    assert abs(item.pos().y() - origin.y()) < 1
    assert view.dragMode() == QGraphicsView.DragMode.RubberBandDrag
    assert view.isInteractive()


def test_space_left_drag_pans_when_the_view_has_focus(qtbot) -> None:
    widget = _host(qtbot)
    view = widget.view
    _focus_canvas(qtbot, view)
    qtbot.keyPress(view, Qt.Key.Key_Space)
    assert view.space_down
    start = view.viewport().rect().center()
    before, after = _drag(
        qtbot,
        view,
        Qt.MouseButton.LeftButton,
        start,
        QPoint(-120, 70),
    )
    _assert_panned(before, after)
    qtbot.keyRelease(view, Qt.Key.Key_Space)
    assert not view.space_down
    assert view.dragMode() == QGraphicsView.DragMode.RubberBandDrag


def test_left_drag_without_alt_or_space_does_not_pan(qtbot) -> None:
    widget = _host(qtbot)
    view = widget.view
    start = view.viewport().rect().center()
    before, after = _drag(
        qtbot,
        view,
        Qt.MouseButton.LeftButton,
        start,
        QPoint(30, 18),
    )
    _assert_still(before, after)


def test_middle_drag_still_pans(qtbot) -> None:
    widget = _host(qtbot)
    view = widget.view
    start = view.viewport().rect().center()
    before, after = _drag(
        qtbot,
        view,
        Qt.MouseButton.MiddleButton,
        start,
        QPoint(80, -60),
    )
    _assert_panned(before, after)
    assert view.dragMode() == QGraphicsView.DragMode.RubberBandDrag


def test_space_in_a_line_edit_does_not_arm_pan(qtbot) -> None:
    widget = _host(qtbot)
    view = widget.view
    edit = QLineEdit(widget)
    edit.move(8, 8)
    edit.resize(160, 24)
    edit.show()
    edit.setFocus(Qt.FocusReason.OtherFocusReason)
    qtbot.waitUntil(edit.hasFocus)
    qtbot.keyClick(edit, Qt.Key.Key_Space)
    assert edit.text() == " "
    assert not view.space_down
    qtbot.keyPress(view, Qt.Key.Key_Space)
    assert not view.space_down
    start = view.viewport().rect().center()
    before, after = _drag(
        qtbot,
        view,
        Qt.MouseButton.LeftButton,
        start,
        QPoint(140, 40),
    )
    _assert_still(before, after)


def test_focus_out_clears_space_pan(qtbot) -> None:
    widget = _host(qtbot)
    view = widget.view
    edit = QLineEdit(widget)
    edit.show()
    _focus_canvas(qtbot, view)
    qtbot.keyPress(view, Qt.Key.Key_Space)
    assert view.space_down
    edit.setFocus(Qt.FocusReason.OtherFocusReason)
    qtbot.waitUntil(lambda: not view.space_down)
    start = view.viewport().rect().center()
    before, after = _drag(
        qtbot,
        view,
        Qt.MouseButton.LeftButton,
        start,
        QPoint(140, 40),
    )
    _assert_still(before, after)
