"""Composite widget that embeds custom widgets in a DAG."""

from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mtqt.widgets.node_graph.graph import GraphChange, GraphEvent, Node, NodeGraph
from mtqt.widgets.node_graph.items import NodeItem
from mtqt.widgets.node_graph.scene import NodeContentFactory, NodeGraphScene
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


def default_node_content(node: Node, parent: QWidget | None) -> QWidget:
    """Label used when a view has no content factory."""
    label = QLabel(node.title, parent)
    label.setObjectName("NodeGraphDefaultContent")
    return label


class NodeGraphWidget(QWidget):
    """A node canvas that hosts a custom ``QWidget`` inside each node.

    Pass a :class:`~mtqt.widgets.node_graph.graph.NodeGraph` built by the
    tool. The widget observes that graph: adding a node, connecting ports, or
    moving a node updates the canvas. Drag an output socket onto an input to
    connect. Delete removes the selection.

    ``content_factory`` receives the stored node and should return a widget.
    Read ``node.payload`` for tool data. Do not mutate the graph from the
    factory. The optional ``parent`` argument may be ``None``; the canvas
    adopts the widget.

    Args:
        graph: DAG to display. A new empty graph is created when omitted.
        parent: Qt parent. Pass the owning window or layout widget so
            lifetime follows Qt ownership. ``None`` means Python owns this
            widget until it is parented or goes out of scope.
        content_factory: Builds the widget embedded in each node.
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
        frame_on_show: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NodeGraphWidget")
        self.setAccessibleName("Node graph")
        self.setMinimumSize(320, 240)
        self._graph = graph if graph is not None else NodeGraph()
        self._factory = content_factory or default_node_content
        self._frame_on_show = frame_on_show
        self._framed = False
        self._current_node = ""

        self._scene = NodeGraphScene(self._graph, self._factory, self)
        self._view = NodeGraphView(self._scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self._relay = _GraphRelay(self)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.connectionRejected.connect(self.connectionRejected)
        self._graph.subscribe(self._relay)

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
    def current_node_id(self) -> str:
        """Id of the first selected node, or ``""`` when none is selected."""
        return self._current_node

    def content_widget(self, node_id: str) -> QWidget | None:
        """Widget embedded in ``node_id``, or ``None`` if the node is absent."""
        return self._scene.content_widget(node_id)

    def set_content_factory(self, factory: NodeContentFactory) -> None:
        """Replace the factory and rebuild embedded widgets."""
        self._factory = factory
        self._scene.set_content_factory(factory)

    def set_graph(self, graph: NodeGraph) -> None:
        """Display a different graph. The previous graph is left intact."""
        self._unsubscribe()
        self._graph = graph
        self._scene.bind(graph)
        self._graph.subscribe(self._relay)
        self._on_selection_changed()

    def frame_all(self) -> None:
        """Fit every node in the view."""
        self._view.frame_all()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._unsubscribe()
        super().closeEvent(event)

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
