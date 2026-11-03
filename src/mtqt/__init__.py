from importlib.metadata import PackageNotFoundError, version

from mtqt._app import ensure_application
from mtqt.models import ObjectListModel, ObjectTableModel, SceneGraphModel, SceneNode
from mtqt.style import apply_stylesheet, load_stylesheet
from mtqt.widgets import (
    DropWidget,
    GraphHistory,
    LabeledLineEdit,
    NodeGraph,
    NodeGraphWidget,
    PathPicker,
    PropertiesBook,
    PropertiesPanel,
    SceneGraphTree,
)

try:
    __version__ = version("mtqt")
except PackageNotFoundError:
    # Vendored or on PYTHONPATH with no dist-info, as a DCC host may load it.
    __version__ = "0+unknown"

__all__ = [
    "GraphHistory",
    "LabeledLineEdit",
    "NodeGraph",
    "NodeGraphWidget",
    "ObjectListModel",
    "ObjectTableModel",
    "PathPicker",
    "PropertiesBook",
    "PropertiesPanel",
    "SceneGraphModel",
    "SceneGraphTree",
    "SceneNode",
    "DropWidget",
    "apply_stylesheet",
    "ensure_application",
    "__version__",
    "load_stylesheet",
]
