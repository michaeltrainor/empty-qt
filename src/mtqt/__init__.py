from importlib.metadata import PackageNotFoundError, version

from mtqt._app import ensure_application
from mtqt.models import (
    SCENE_GRAPH_MIME_TYPE,
    MoveFilter,
    ObjectListModel,
    ObjectTableModel,
    SceneGraphModel,
    SceneNode,
)
from mtqt.style import apply_stylesheet, load_stylesheet
from mtqt.widgets import (
    BreadcrumbBar,
    Command,
    CommandPalette,
    Crumb,
    DropWidget,
    GraphHistory,
    LabeledLineEdit,
    NodeGraph,
    NodeGraphWidget,
    PathPicker,
    PropertiesBook,
    PropertiesPanel,
    RangeSlider,
    SceneGraphTree,
    TagInput,
    TimelineBar,
    ToastLevel,
    ToastStack,
)

try:
    __version__ = version("mtqt")
except PackageNotFoundError:
    # Vendored or on PYTHONPATH with no dist-info, as a DCC host may load it.
    __version__ = "0+unknown"

__all__ = [
    "SCENE_GRAPH_MIME_TYPE",
    "BreadcrumbBar",
    "Command",
    "CommandPalette",
    "Crumb",
    "DropWidget",
    "GraphHistory",
    "LabeledLineEdit",
    "MoveFilter",
    "NodeGraph",
    "NodeGraphWidget",
    "ObjectListModel",
    "ObjectTableModel",
    "PathPicker",
    "PropertiesBook",
    "PropertiesPanel",
    "RangeSlider",
    "SceneGraphModel",
    "SceneGraphTree",
    "SceneNode",
    "TagInput",
    "TimelineBar",
    "ToastLevel",
    "ToastStack",
    "__version__",
    "apply_stylesheet",
    "ensure_application",
    "load_stylesheet",
]
