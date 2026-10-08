"""Composite widget that embeds custom widgets in a DAG."""

from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QMouseEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from mtqt.widgets.node_graph.graph import GraphChange, GraphEvent, Node, NodeGraph
from mtqt.widgets.node_graph.history import GraphHistory
from mtqt.widgets.node_graph.items import NodeItem
from mtqt.widgets.node_graph.scene import (
    NodeContentFactory,
    NodeFactory,
    NodeGraphScene,
)
from mtqt.widgets.node_graph.view import NodeGraphView


class _GraphRelay:
    """Listener that does not keep the widget alive."""

    def __init__(self, widget: NodeGraphWidget) -> None:
        self._widget = weakref.ref(widget)

    def __call__(self, event: GraphEvent) -> None:
        widget = self._widget()
        if widget is None or not shiboken6.isValid(widget):
            return
        widget._on_graph_event(event)


class NodeGraphWidget(QWidget):
    """A node canvas for a directed acyclic graph.

    Pass a :class:`~mtqt.widgets.node_graph.graph.NodeGraph` built by the
    tool. The widget observes that graph: adding a node, connecting ports, or
    moving a node updates the canvas. Each node is a title and its ports.
    Drag an output socket onto an input to connect. Drag a connected input,
    or the wire itself, to move that connection; drop it on empty canvas to
    disconnect. Right-click empty canvas to add a node. Double-click a node
    header to rename it. Enter or a click elsewhere commits the title, and
    Escape cancels. A blank title is left unchanged. Middle-drag, Alt-drag,
    and Space-drag pan. Delete and Backspace remove the selection. While a
    header editor has focus, those keys edit the title instead. Ctrl+Z undoes
    the last gesture when the view or its viewport has focus. Ctrl+Y and
    Ctrl+Shift+Z redo it.

    ``content_factory`` is optional. When set, it receives the stored node
    and should return a widget embedded in the body. Read ``node.payload``
    for tool data. Do not mutate the graph from the factory. The optional
    ``parent`` argument may be ``None``; the canvas adopts the widget.

    Args:
        graph: DAG to display. A new empty graph is created when omitted.
        parent: Qt parent. Pass the owning window or layout widget so
            lifetime follows Qt ownership. ``None`` means Python owns this
            widget until it is parented or goes out of scope.
        content_factory: Builds the widget embedded in each node. Omit it
            for a title and ports only.
        node_factory: Builds the node inserted from the background menu.
            Defaults to one ``in`` port and one ``out`` port of type ``any``.
        frame_on_show: Fit the graph in the view the first time it is shown.
    """

    currentNodeChanged = Signal(str)
    connectionRejected = Signal(str)
    graphChanged = Signal()

    def __init__(
        self,
        graph: NodeGraph | None = None,
        parent: QWidget | None = None,
        *,
        content_factory: NodeContentFactory | None = None,
        node_factory: NodeFactory | None = None,
        frame_on_show: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NodeGraphWidget")
        self.setAccessibleName("Node graph")
        self.setMinimumSize(320, 240)
        self._graph = graph if graph is not None else NodeGraph()
        self._factory = content_factory
        self._frame_on_show = frame_on_show
        self._framed = False
        self._current_node = ""

        self._scene = NodeGraphScene(
            self._graph,
            self._factory,
            self,
            node_factory=node_factory,
        )
        self._view = NodeGraphView(self._scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self._seal_queued = False
        self._history = GraphHistory(self._graph)
        self._relay = _GraphRelay(self)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.connectionRejected.connect(self.connectionRejected)
        self._graph.subscribe(self._relay)
        self._view.installEventFilter(self)
        self._view.viewport().installEventFilter(self)

    @property
    def graph(self) -> NodeGraph:
        """DAG displayed by this widget."""
        return self._graph

    @property
    def scene(self) -> NodeGraphScene:
        """Graphics scene. Prefer :attr:`graph` for structural edits."""
        return self._scene

    @property
    def view(self) -> NodeGraphView:
        """Inner canvas."""
        return self._view

    @property
    def history(self) -> GraphHistory:
        """Undo stack for gestures applied to :attr:`graph`."""
        return self._history

    @property
    def current_node_id(self) -> str:
        """Id of the first selected node, or ``""`` when none is selected."""
        return self._current_node

    def content_widget(self, node_id: str) -> QWidget | None:
        """Widget embedded in ``node_id``, or ``None`` if the node is absent."""
        return self._scene.content_widget(node_id)

    def set_content_factory(self, factory: NodeContentFactory | None) -> None:
        """Replace the factory and rebuild node bodies.

        ``None`` clears the embedded widgets and leaves each node as a title
        and its ports.
        """
        self._factory = factory
        self._scene.set_content_factory(factory)

    def set_node_factory(self, factory: NodeFactory | None) -> None:
        """Replace the factory used by the background menu's Add node action.

        ``None`` restores the default node: one ``in`` port, one ``out`` port,
        type ``any``.
        """
        self._scene.set_node_factory(factory)

    def add_node_at(self, position: tuple[float, float]) -> Node:
        """Insert a node at a scene position. See :meth:`NodeGraphScene.add_node_at`."""
        return self._scene.add_node_at(position)

    def set_graph(self, graph: NodeGraph) -> None:
        """Display a different graph. The previous graph is left intact.

        Undo history is rebound to ``graph`` and its stacks are cleared.
        """
        self._unsubscribe()
        self._graph = graph
        self._history.bind(graph)
        self._scene.bind(graph)
        self._graph.subscribe(self._relay)
        self._on_selection_changed()

    def undo(self) -> bool:
        """Undo the last sealed gesture. Returns ``False`` when none exists."""
        return self._history.undo()

    def redo(self) -> bool:
        """Redo the last undone gesture. Returns ``False`` when none exists."""
        return self._history.redo()

    def frame_all(self) -> None:
        """Fit every node in the view."""
        self._view.frame_all()

    def frame_selection(self) -> None:
        """Center and zoom to the selection. ``F`` does this.

        With nothing selected, frames every item in the scene.
        """
        self._view.frame_selection()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._unsubscribe()
        self._history._unlisten()
        super().closeEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._view or watched is self._view.viewport():
            if self._handle_canvas_event(event):
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._frame_on_show and not self._framed:
            self._framed = True
            self.frame_all()

    def _on_graph_event(self, event: GraphEvent) -> None:
        if event.kind in (
            GraphChange.NODE_ADDED,
            GraphChange.NODE_REMOVED,
            GraphChange.EDGE_ADDED,
            GraphChange.EDGE_REMOVED,
        ):
            self.graphChanged.emit()
        self._queue_seal()

    def _queue_seal(self) -> None:
        if self._seal_queued or not self._history.has_pending:
            return
        self._seal_queued = True
        QTimer.singleShot(0, self._seal_pending)

    def _seal_pending(self) -> None:
        self._seal_queued = False
        if not shiboken6.isValid(self):
            return
        self._history.seal()

    def _handle_canvas_event(self, event: QEvent) -> bool:
        if event.type() is QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            return self._handle_undo_shortcut(event)
        if not isinstance(event, QMouseEvent):
            return False
        if event.type() is QEvent.Type.MouseButtonPress:
            self._handle_press(event)
        elif event.type() is QEvent.Type.MouseButtonRelease:
            self._handle_release(event)
        return False

    def _handle_undo_shortcut(self, event: QKeyEvent) -> bool:
        focus = QApplication.focusWidget()
        if focus is not self._view and focus is not self._view.viewport():
            return False
        modifiers = event.modifiers()
        if not modifiers & Qt.KeyboardModifier.ControlModifier:
            return False
        if modifiers & Qt.KeyboardModifier.AltModifier:
            return False
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()
        if key == Qt.Key.Key_Z and not shift:
            self.undo()
            event.accept()
            return True
        if key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift):
            self.redo()
            event.accept()
            return True
        return False

    def _handle_press(self, event: QMouseEvent) -> None:
        if self._is_node_drag(event):
            self._history._begin_move_group()
            return
        if self._is_pan(event):
            self._history._end_move_group()

    def _handle_release(self, event: QMouseEvent) -> None:
        if event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        ):
            self._history._end_move_group()

    def _is_node_drag(self, event: QMouseEvent) -> bool:
        return event.button() == Qt.MouseButton.LeftButton and not self._is_pan(event)

    def _is_pan(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.MiddleButton:
            return True
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return True
        return self._view.space_down

    def _on_selection_changed(self) -> None:
        node_id = ""
        for item in self._scene.selectedItems():
            if isinstance(item, NodeItem):
                node_id = item.node_id
                break
        if node_id == self._current_node:
            return
        self._current_node = node_id
        self.currentNodeChanged.emit(node_id)

    def _unsubscribe(self) -> None:
        self._graph.unsubscribe(self._relay)
        if shiboken6.isValid(self._scene):
            self._graph.unsubscribe(self._scene.apply_event)
