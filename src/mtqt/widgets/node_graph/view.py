"""Pannable, zoomable view over a :class:`NodeGraphScene`."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QGraphicsView, QMenu, QWidget

from mtqt.widgets.node_graph.scene import NodeGraphScene

_MIN_ZOOM = 0.25
_MAX_ZOOM = 2.5
_GRID = 24.0


class NodeGraphView(QGraphicsView):
    """Canvas for a node graph.

    Wheel zooms toward the cursor. Middle-drag, Alt-drag, and Space-drag pan.
    Space-drag applies only while this view or its viewport has focus, so a
    focused editor still receives Space. Delete and Backspace remove the
    selection through the graph. While a header editor has focus, the view
    leaves keys to that editor, so those keys edit its text. ``F`` frames the
    selection, or every item when nothing is selected. Double-click a node
    header to rename it. Enter or a
    click elsewhere commits the title, and Escape cancels. Right-click on
    empty canvas opens a menu that adds a node.
    Embedded widgets keep their own key events while they have focus.

    Args:
        scene: Scene to display.
        parent: Qt parent.
    """

    def __init__(self, scene: NodeGraphScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setObjectName("NodeGraphView")
        self.setAccessibleName("Node graph view")
        self._scene = scene
        self._zoom = 1.0
        self._panning = False
        self._pan_button = Qt.MouseButton.NoButton
        self._space_down = False
        self._restore_interactive = True
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    @property
    def zoom(self) -> float:
        """Current scale factor."""
        return self._zoom

    @property
    def space_down(self) -> bool:
        """Whether Space is held so a left drag pans the canvas."""
        return self._space_down

    def set_zoom(self, factor: float) -> None:
        """Set the scale factor, clamped to the view's zoom range."""
        clamped = min(_MAX_ZOOM, max(_MIN_ZOOM, factor))
        if self._zoom == 0:
            return
        self.scale(clamped / self._zoom, clamped / self._zoom)
        self._zoom = clamped

    def frame_all(self) -> None:
        """Fit every item in the viewport, with a margin."""
        self._frame_rect(self._scene.itemsBoundingRect())

    def frame_selection(self) -> None:
        """Center and zoom so the selected items fill the viewport.

        With nothing selected, frames every item in the scene. A scene with
        no items is left as it is.
        """
        rect = QRectF()
        for item in self._scene.selectedItems():
            rect = rect.united(item.sceneBoundingRect())
        if not rect.isValid():
            rect = self._scene.itemsBoundingRect()
        self._frame_rect(rect)

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        super().drawBackground(painter, rect)
        if isinstance(rect, QRect):
            rect = QRectF(rect)
        painter.setPen(QPen(QColor("#3f4147"), 1))
        left = int(rect.left()) - (int(rect.left()) % int(_GRID))
        top = int(rect.top()) - (int(rect.top()) % int(_GRID))
        x = float(left)
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += _GRID
        y = float(top)
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += _GRID

    def wheelEvent(self, event: QWheelEvent) -> None:
        step = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        self.set_zoom(self._zoom * step)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._should_pan(event):
            self._start_pan(event)
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._panning and event.button() == self._pan_button:
            self._stop_pan(event)
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        viewport_pos = self.viewport().mapFromGlobal(event.globalPos())
        if self.itemAt(viewport_pos) is not None:
            super().contextMenuEvent(event)
            return
        scene_pos = self.mapToScene(viewport_pos)
        self._show_background_menu(event.globalPos(), (scene_pos.x(), scene_pos.y()))
        event.accept()

    def _show_background_menu(
        self,
        global_pos: QPoint,
        scene_position: tuple[float, float],
    ) -> None:
        menu = QMenu(self)
        add_node = menu.addAction("Add node")
        chosen = menu.exec(global_pos)
        if chosen is add_node:
            self._scene.add_node_at(scene_position)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._scene.title_editor_has_focus():
            super().keyPressEvent(event)
            return
        if self._handle_space_press(event):
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._scene.delete_selected()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_F
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and not event.isAutoRepeat()
        ):
            self.frame_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._handle_space_release(event):
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._space_down = False
        super().focusOutEvent(event)

    def _frame_rect(self, rect: QRectF) -> None:
        if not rect.isValid():
            return
        padded = rect.adjusted(-48, -48, 48, 48)
        if padded.width() <= 0 or padded.height() <= 0:
            return
        view_w = max(self.viewport().width(), 1)
        view_h = max(self.viewport().height(), 1)
        zoom = min(view_w / padded.width(), view_h / padded.height())
        zoom = min(_MAX_ZOOM, max(_MIN_ZOOM, zoom))
        self.resetTransform()
        self.scale(zoom, zoom)
        self._zoom = zoom
        self.centerOn(rect.center())

    def _should_pan(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.MiddleButton:
            return True
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return True
        return self._space_down and self._canvas_has_focus()

    def _canvas_has_focus(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is self or focus is self.viewport()

    def _start_pan(self, event: QMouseEvent) -> None:
        self._panning = True
        self._pan_button = event.button()
        self._restore_interactive = self.isInteractive()
        # Items accept a left press before ScrollHandDrag can begin.
        self.setInteractive(False)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mousePressEvent(self._as_left(event, QEvent.Type.MouseButtonPress))
        event.accept()

    def _stop_pan(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(self._as_left(event, QEvent.Type.MouseButtonRelease))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setInteractive(self._restore_interactive)
        self._panning = False
        self._pan_button = Qt.MouseButton.NoButton
        event.accept()

    def _handle_space_press(self, event: QKeyEvent) -> bool:
        if event.key() != Qt.Key.Key_Space:
            return False
        if event.isAutoRepeat():
            if self._space_down:
                event.accept()
            return self._space_down
        if not self._canvas_has_focus():
            return False
        self._space_down = True
        event.accept()
        return True

    def _handle_space_release(self, event: QKeyEvent) -> bool:
        if event.key() != Qt.Key.Key_Space:
            return False
        if event.isAutoRepeat():
            if self._space_down:
                event.accept()
            return self._space_down
        was_down = self._space_down
        self._space_down = False
        if was_down or self._canvas_has_focus():
            event.accept()
            return True
        return False

    def _as_left(self, event: QMouseEvent, kind: QEvent.Type) -> QMouseEvent:
        buttons = (
            Qt.MouseButton.LeftButton
            if kind is QEvent.Type.MouseButtonPress
            else Qt.MouseButton.NoButton
        )
        return QMouseEvent(
            kind,
            event.position(),
            event.globalPosition(),
            Qt.MouseButton.LeftButton,
            buttons,
            event.modifiers(),
        )
