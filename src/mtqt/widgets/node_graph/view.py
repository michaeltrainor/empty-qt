"""Pannable, zoomable view over a :class:`NodeGraphScene`."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QGraphicsView, QWidget

from mtqt.widgets.node_graph.scene import NodeGraphScene

_MIN_ZOOM = 0.25
_MAX_ZOOM = 2.5
_GRID = 24.0


class NodeGraphView(QGraphicsView):
    """Canvas for a node graph.

    Wheel zooms toward the cursor. Middle-drag pans. Delete removes the
    selection through the graph. Embedded widgets keep their own key events
    while they have focus.

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

    def set_zoom(self, factor: float) -> None:
        """Set the scale factor, clamped to the view's zoom range."""
        clamped = min(_MAX_ZOOM, max(_MIN_ZOOM, factor))
        if self._zoom == 0:
            return
        self.scale(clamped / self._zoom, clamped / self._zoom)
        self._zoom = clamped

    def frame_all(self) -> None:
        """Fit every item in the viewport, with a margin."""
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            return
        self.fitInView(
            rect.adjusted(-48, -48, 48, 48),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._zoom = self.transform().m11()

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
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            super().mousePressEvent(self._as_left(event, QEvent.Type.MouseButtonPress))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            super().mouseReleaseEvent(
                self._as_left(event, QEvent.Type.MouseButtonRelease)
            )
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self._panning = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._scene.delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

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
