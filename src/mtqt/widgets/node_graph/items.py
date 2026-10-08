"""Graphics items for a node, its ports, and the edges between them."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
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
_EDGE_HOVER = QColor("#e7f1ff")
_HOVER_GLOW = QColor(214, 228, 255)
_REJECT = QColor("#c0392b")

_TITLE_HEIGHT = 32.0
_HEADER_INSET = 12.0
_LABEL_INSET = 16.0
_COLUMN_GAP = 32.0
_BODY_PAD = 12.0
_PORT_RADIUS = 5.0
_HOVER_SPREAD = 3.0
_MIN_WIDTH = 180.0
_MIN_CONTENT = 28.0
_PORT_SLOT = 28.0


def _ui_font(*, bold: bool) -> QFont:
    """Font the node is painted with. Matches the 13px QSS face."""
    font = QFont()
    font.setFamilies(["IBM Plex Sans", "Segoe UI"])
    font.setPixelSize(13)
    font.setBold(bold)
    return font


def _label_font() -> QFont:
    return _ui_font(bold=False)


def _title_font() -> QFont:
    return _ui_font(bold=True)


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
        self._press_pos: QPointF | None = None

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
        parent = self.parentItem()
        if isinstance(parent, NodeItem):
            parent.set_hovered(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._hovered = False
        self.update()
        parent = self.parentItem()
        if isinstance(parent, NodeItem):
            parent.pointer_moved(event.scenePos())
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self.direction is PortDirection.OUT:
            begin = getattr(self.scene(), "begin_draft", None)
            if callable(begin):
                begin(self)
                event.accept()
                return
        elif self._input_is_connected():
            self._press_pos = event.scenePos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._press_pos is not None:
            moved = event.scenePos() - self._press_pos
            if abs(moved.x()) + abs(moved.y()) < 6:
                event.accept()
                return
            begin = getattr(self.scene(), "begin_rewire_from_input", None)
            self._press_pos = None
            if callable(begin):
                begin(self)
        update = getattr(self.scene(), "update_draft", None)
        if callable(update):
            update(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._press_pos is not None:
            self._press_pos = None
            event.accept()
            return
        finish = getattr(self.scene(), "finish_draft", None)
        if callable(finish):
            finish(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _input_is_connected(self) -> bool:
        scene = self.scene()
        graph = getattr(scene, "graph", None)
        incoming = getattr(graph, "incoming_edge", None)
        if not callable(incoming):
            return False
        return incoming(self.node_id, self.port_name) is not None


class EdgeItem(QGraphicsPathItem):
    """Cubic curve from an output anchor to an input anchor."""

    def __init__(self, edge_id: str) -> None:
        super().__init__()
        self.edge_id = edge_id
        self._draft = False
        self._rejected = False
        self._hovered = False
        self._press_pos: QPointF | None = None
        self.setZValue(-1)
        self.setAcceptHoverEvents(True)
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
            color = _ACCENT.lighter(140) if self._hovered else _ACCENT
            pen = QPen(color, 2.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())
            return
        super().paint(painter, option, widget)

    @property
    def hovered(self) -> bool:
        """Whether the pointer is over this wire."""
        return self._hovered

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._set_hovered(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._set_hovered(False)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._draft:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.edge_id:
            self._press_pos = event.scenePos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._draft:
            self._follow_draft(event)
            return
        if self._press_pos is not None:
            moved = event.scenePos() - self._press_pos
            if abs(moved.x()) + abs(moved.y()) >= 6:
                begin = getattr(self.scene(), "begin_rewire_from_edge", None)
                self._press_pos = None
                if callable(begin):
                    begin(self)
                self._follow_draft(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._draft:
            finish = getattr(self.scene(), "finish_draft", None)
            if callable(finish):
                finish(event.scenePos())
            event.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _follow_draft(self, event: QGraphicsSceneMouseEvent) -> None:
        update = getattr(self.scene(), "update_draft", None)
        if callable(update):
            update(event.scenePos())
        event.accept()

    def _set_hovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._apply_pen()
        self.update()

    def _apply_pen(self) -> None:
        if self._rejected:
            color = _REJECT
        elif self._hovered and not self._draft:
            color = _EDGE_HOVER
        else:
            color = _EDGE
        pen = QPen(color, 2)
        pen.setCosmetic(True)
        if self._draft:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)


class NodeItem(QGraphicsObject):
    """Framed node with a title and ports."""

    def __init__(self, node_id: str, title: str) -> None:
        super().__init__()
        self.node_id = node_id
        self._title = title
        self._size = QRectF(
            0,
            0,
            _MIN_WIDTH,
            _TITLE_HEIGHT + _MIN_CONTENT + _BODY_PAD * 2,
        )
        self._writing = False
        self._left_label_w = 0.0
        self._right_label_w = 0.0
        self._hovered = False
        self._ports: dict[tuple[str, PortDirection], PortItem] = {}
        self.setAcceptHoverEvents(True)
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

    def set_content(self, widget: QWidget | None) -> None:
        """Embed ``widget`` in the body, or clear it when ``widget`` is ``None``."""
        previous = self._proxy.widget()
        if widget is None:
            # Qt clears the proxy. The stub requires a QWidget.
            self._proxy.setWidget(None)  # type: ignore[arg-type]
        else:
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
        """Size the frame from the port labels and stack the sockets."""
        left_w = self._label_column_width(PortDirection.IN)
        right_w = self._label_column_width(PortDirection.OUT)
        self._left_label_w = left_w
        self._right_label_w = right_w
        rows = max(self._count(PortDirection.IN), self._count(PortDirection.OUT))
        port_h = rows * _PORT_SLOT
        widget = self._proxy.widget()
        if widget is None:
            middle = _COLUMN_GAP if left_w > 0.0 and right_w > 0.0 else 0.0
            content_w = 0.0
            content_h = port_h
            left_gap = 0.0
            right_gap = middle
            min_width = max(_MIN_WIDTH, self._title_width())
        else:
            left_gap = _COLUMN_GAP if left_w > 0.0 else 0.0
            right_gap = _COLUMN_GAP if right_w > 0.0 else 0.0
            content_w = _MIN_WIDTH - _LABEL_INSET * 2
            content_h = _MIN_CONTENT
            hint = widget.sizeHint()
            content_w = max(content_w, float(hint.width()))
            content_h = max(content_h, float(hint.height()), port_h or _PORT_SLOT)
            min_width = max(_MIN_WIDTH, self._title_width())
        labels = _LABEL_INSET + left_w + left_gap + content_w + right_gap + right_w
        width = max(min_width, labels + _LABEL_INSET)
        if widget is None and content_h == 0.0:
            height = _TITLE_HEIGHT
        else:
            height = _TITLE_HEIGHT + content_h + _BODY_PAD * 2
        self.prepareGeometryChange()
        self._size = QRectF(0, 0, width, height)
        self._proxy.setPos(
            _LABEL_INSET + left_w + left_gap,
            _TITLE_HEIGHT + _BODY_PAD,
        )
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
        margin = max(_PORT_RADIUS + 2.0, _HOVER_SPREAD)
        return self._size.adjusted(-margin, -margin, margin, margin)

    @property
    def hovered(self) -> bool:
        """Whether the pointer is over this node or one of its sockets."""
        return self._hovered

    def set_hovered(self, hovered: bool) -> None:
        """Show or hide the hover glow."""
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self.update()

    def pointer_moved(self, scene_pos: QPointF) -> None:
        """Keep the glow while the pointer is still inside the frame."""
        if not self.boundingRect().contains(self.mapFromScene(scene_pos)):
            self.set_hovered(False)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.set_hovered(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.pointer_moved(event.scenePos())
        super().hoverLeaveEvent(event)

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
        self._paint_hover_glow(painter, body)
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
        painter.setFont(_title_font())
        painter.drawText(
            header.adjusted(_HEADER_INSET, 0, -_HEADER_INSET, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._title,
        )
        self._paint_port_labels(painter)

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

    def _paint_hover_glow(self, painter: QPainter, body: QRectF) -> None:
        if not self._hovered:
            return
        color = QColor(_HOVER_GLOW)
        color.setAlpha(210)
        painter.setPen(QPen(color, 1.25))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body.adjusted(-1.5, -1.5, 1.5, 1.5), 7, 7)

    def _title_width(self) -> float:
        metrics = QFontMetrics(_title_font())
        text = float(metrics.horizontalAdvance(self._title))
        return text + _HEADER_INSET * 2 + 4.0

    def _label_column_width(self, direction: PortDirection) -> float:
        names = [name for name, side in self._ports if side is direction]
        if not names:
            return 0.0
        metrics = QFontMetrics(_label_font())
        return float(max(metrics.horizontalAdvance(name) for name in names))

    def _paint_port_labels(self, painter: QPainter) -> None:
        font = _label_font()
        painter.setFont(font)
        painter.setPen(_MUTED)
        text_h = float(QFontMetrics(font).height())
        align_y = int(Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextDontClip)
        for (name, direction), port in self._ports.items():
            if direction is PortDirection.IN:
                column = self._left_label_w
                left = _LABEL_INSET
                align = align_y | int(Qt.AlignmentFlag.AlignLeft)
            else:
                column = self._right_label_w
                left = self._size.width() - _LABEL_INSET - column
                align = align_y | int(Qt.AlignmentFlag.AlignRight)
            if column <= 0.0:
                continue
            top = max(_TITLE_HEIGHT, port.pos().y() - text_h / 2.0)
            painter.drawText(QRectF(left, top, column, text_h), align, name)

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
        top = _TITLE_HEIGHT + _BODY_PAD
        bottom = self._size.height() - _BODY_PAD
        span = max(bottom - top, 1.0)
        for index, item in enumerate(ports):
            if len(ports) == 1:
                y = (top + bottom) / 2.0
            else:
                y = top + span * ((index + 0.5) / len(ports))
            item.setPos(x, y)
