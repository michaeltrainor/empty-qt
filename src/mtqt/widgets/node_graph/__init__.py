"""Node graph widget and the DAG it displays.

Import the graph types and the widget from this package:

.. code-block:: python

    from mtqt.widgets.node_graph import (
        GraphHistory,
        Node,
        NodeGraph,
        NodeGraphWidget,
        Port,
        PortDirection,
    )

``NodeGraph`` is a directed acyclic graph with no Qt dependency. ``NodeGraphWidget``
observes one graph and draws each node as a title and its ports. ``GraphHistory``
records undo steps for that graph.
"""

from mtqt.widgets.node_graph.graph import (
    CycleError,
    Edge,
    GraphChange,
    GraphEvent,
    GraphListener,
    Node,
    NodeGraph,
    NodeGraphError,
    Port,
    PortDirection,
    ports_compatible,
)
from mtqt.widgets.node_graph.history import GraphHistory
from mtqt.widgets.node_graph.scene import NodeContentFactory, NodeFactory
from mtqt.widgets.node_graph.widget import NodeGraphWidget

__all__ = [
    "CycleError",
    "Edge",
    "GraphChange",
    "GraphEvent",
    "GraphHistory",
    "GraphListener",
    "Node",
    "NodeContentFactory",
    "NodeFactory",
    "NodeGraph",
    "NodeGraphError",
    "NodeGraphWidget",
    "Port",
    "PortDirection",
    "ports_compatible",
]
