"""Graphics items for a node, its ports, and the edges between them."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from mtqt.widgets.node_graph.graph import PortDirection

# Matches the tokens in mtqt.style qss (panel, text, accent, border).
_PANEL = QColor("#2b2d31")
_HEADER = QColor("#34363c")
_TEXT = QColor("#e8e8ea")
_MUTED = QColor("#c8c8cc")
_ACCENT = QColor("#5b8def")
_BORDER = QColor("#3f4147")
_EDGE = QColor("#8aa4c8")
_REJECT = QColor("#c0392b")

_TITLE_HEIGHT = 28.0
_PADDING = 8.0
_PORT_RADIUS = 5.0
_MIN_WIDTH = 180.0
_MIN_CONTENT = 28.0
_PORT_SLOT = 22.0


def _is_selected(option: QStyleOptionGraphicsItem) -> bool:
    state = getattr(option, "state", None)
    if state is None:
        return False
    try:
        return bool(state & QStyle.StateFlag.State_Selected)
    except TypeError:
        return False


class PortItem(QGraphicsObject):
    """Socket drawn on the left or right edge of a node."""

    def __init__(
        self,
        node_id: str,
        port_name: str,
        direction: PortDirection,
        data_type: str,
        parent: QGraphicsObject,
    ) -> None:
        super().__init__(parent)
        self.node_id = node_id
        self.port_name = port_name
        self.direction = direction
        self.data_type = data_type
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{direction.value}: {port_name} ({data_type})")
        self._hovered = False

    def boundingRect(self) -> QRectF:
        radius = _PORT_RADIUS + 3.0
        return QRectF(-radius, -radius, radius * 2.0, radius * 2.0)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = _ACCENT if self.direction is PortDirection.OUT else _MUTED
        if self._hovered:
            color = color.lighter(120)
        painter.setPen(QPen(_BORDER, 1))
        painter.setBrush(color)
        painter.drawEllipse(
            QRectF(-_PORT_RADIUS, -_PORT_RADIUS, _PORT_RADIUS * 2, _PORT_RADIUS * 2)
        )

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if (
            self.direction is PortDirection.OUT
            and event.button() == Qt.MouseButton.LeftButton
        ):
            begin = getattr(self.scene(), "begin_draft", None)
            if callable(begin):
                begin(self)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        update = getattr(self.scene(), "update_draft", None)
        if callable(update):
            update(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        finish = getattr(self.scene(), "finish_draft", None)
        if callable(finish):
            finish(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class EdgeItem(QGraphicsPathItem):
    """Cubic curve from an output anchor to an input anchor."""

    def __init__(self, edge_id: str) -> None:
        super().__init__()
        self.edge_id = edge_id
        self._draft = False
        self._rejected = False
        self.setZValue(-1)
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._apply_pen()

    def set_draft(self, draft: bool) -> None:
        """Mark this curve as the in-progress connection, which is not selectable."""
        self._draft = draft
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, not draft)
        self._apply_pen()

    def set_rejected(self, rejected: bool) -> None:
        """Tint the draft curve when the port under the cursor cannot connect."""
        if rejected == self._rejected:
            return
        self._rejected = rejected
        self._apply_pen()

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        """Rebuild the curve between scene points."""
        path = QPainterPath(start)
        distance = max(abs(end.x() - start.x()) * 0.5, 48.0)
        path.cubicTo(
            QPointF(start.x() + distance, start.y()),
            QPointF(end.x() - distance, end.y()),
            end,
        )
        self.setPath(path)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(self.path())

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if _is_selected(option) and not self._draft:
            pen = QPen(_ACCENT, 2.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())
            return
        super().paint(painter, option, widget)

    def _apply_pen(self) -> None:
        color = _REJECT if self._rejected else _EDGE
        pen = QPen(color, 2)
        pen.setCosmetic(True)
        if self._draft:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)


class NodeItem(QGraphicsObject):
    """Framed node with a title, ports, and an embedded widget proxy."""

    def __init__(self, node_id: str, title: str) -> None:
        super().__init__()
        self.node_id = node_id
        self._title = title
        self._size = QRectF(
            0,
            0,
            _MIN_WIDTH,
            _TITLE_HEIGHT + _MIN_CONTENT + _PADDING * 2,
        )
        self._writing = False
        self._ports: dict[tuple[str, PortDirection], PortItem] = {}
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(1)
        self._proxy = QGraphicsProxyWidget(self)
        self._proxy.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable, False)
        self._proxy.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable, False)

    @property
    def title(self) -> str:
        """Header text."""
        return self._title

    def set_title(self, title: str) -> None:
        """Replace the header text."""
        self._title = title
        self.update()

    def set_content(self, widget: QWidget) -> None:
        """Embed ``widget`` and resize the frame around it."""
        previous = self._proxy.widget()
        self._proxy.setWidget(widget)
        if previous is not None:
            previous.deleteLater()
        self.relayout()

    def content_widget(self) -> QWidget | None:
        """Embedded widget, if one has been set."""
        return self._proxy.widget()

    def port_item(self, name: str, direction: PortDirection) -> PortItem | None:
        """Return the socket item for a port name."""
        return self._ports.get((name, direction))

    def set_ports(
        self,
        inputs: tuple[tuple[str, str], ...],
        outputs: tuple[tuple[str, str], ...],
    ) -> None:
        """Replace sockets. Each entry is ``(name, data_type)``."""
        for item in self._ports.values():
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
            item.deleteLater()
        self._ports.clear()
        for name, data_type in inputs:
            self._add_port(name, PortDirection.IN, data_type)
        for name, data_type in outputs:
            self._add_port(name, PortDirection.OUT, data_type)
        self.relayout()

    def anchor(self, name: str, direction: PortDirection) -> QPointF:
        """Scene position of a port center."""
        item = self._ports.get((name, direction))
        if item is None:
            return self.scenePos()
        return item.scenePos()

    def relayout(self) -> None:
        """Size the frame from the embedded widget and stack the ports."""
        content_w = _MIN_WIDTH - _PADDING * 2
        content_h = _MIN_CONTENT
        widget = self._proxy.widget()
        if widget is not None:
            hint = widget.sizeHint()
            content_w = max(content_w, float(hint.width()))
            content_h = max(content_h, float(hint.height()))
        rows = max(self._count(PortDirection.IN), self._count(PortDirection.OUT), 1)
        content_h = max(content_h, rows * _PORT_SLOT)
        width = content_w + _PADDING * 2
        height = _TITLE_HEIGHT + content_h + _PADDING * 2
        self.prepareGeometryChange()
        self._size = QRectF(0, 0, width, height)
        self._proxy.setPos(_PADDING, _TITLE_HEIGHT + _PADDING)
        self._proxy.resize(content_w, content_h)
        if widget is not None:
            widget.resize(int(content_w), int(content_h))
        self._place_ports(PortDirection.IN, 0.0)
        self._place_ports(PortDirection.OUT, width)
        self.update()

    def apply_position(self, position: QPointF) -> None:
        """Move the item without writing the position back to the graph."""
        self._writing = True
        self.setPos(position)
        self._writing = False

    def boundingRect(self) -> QRectF:
        margin = _PORT_RADIUS + 2.0
        return self._size.adjusted(-margin, -margin, margin, margin)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = self._size
        selected = _is_selected(option)
        painter.setPen(QPen(_ACCENT if selected else _BORDER, 2 if selected else 1))
        painter.setBrush(_PANEL)
        painter.drawRoundedRect(body, 6, 6)

        header = QRectF(body.left(), body.top(), body.width(), _TITLE_HEIGHT)
        painter.setBrush(_HEADER)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(header, 6, 6)
        painter.fillRect(
            QRectF(body.left(), body.top() + _TITLE_HEIGHT - 6, body.width(), 6),
            _HEADER,
        )
        painter.setPen(QPen(_ACCENT if selected else _BORDER, 2 if selected else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, 6, 6)

        painter.setPen(_TEXT)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            header.adjusted(10, 0, -10, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._title,
        )

    def itemChange(
        self,
        change: QGraphicsObject.GraphicsItemChange,
        value: object,
    ) -> object:
        result = super().itemChange(change, value)
        if (
            change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged
            and not self._writing
            and isinstance(value, QPointF)
        ):
            moved = getattr(self.scene(), "node_moved", None)
            if callable(moved):
                moved(self.node_id, (value.x(), value.y()))
        return result

    def _add_port(self, name: str, direction: PortDirection, data_type: str) -> None:
        self._ports[(name, direction)] = PortItem(
            self.node_id,
            name,
            direction,
            data_type,
            self,
        )

    def _count(self, direction: PortDirection) -> int:
        return sum(1 for _name, side in self._ports if side is direction)

    def _place_ports(self, direction: PortDirection, x: float) -> None:
        ports = [
            item for (_name, side), item in self._ports.items() if side is direction
        ]
        if not ports:
            return
        top = _TITLE_HEIGHT + _PADDING
        bottom = self._size.height() - _PADDING
        span = max(bottom - top, 1.0)
        for index, item in enumerate(ports):
            if len(ports) == 1:
                y = (top + bottom) / 2.0
            else:
                y = top + span * ((index + 0.5) / len(ports))
            item.setPos(x, y)
