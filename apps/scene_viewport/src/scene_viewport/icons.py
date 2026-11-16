"""Toolbar icons, rendered from the SVG files in ``scene_viewport/icons``."""

from __future__ import annotations

from importlib.resources import files
from typing import Literal, get_args

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

IconName = Literal[
    "select", "move", "rotate", "scale", "world", "local", "pivot", "snap"
]
ICON_NAMES: tuple[IconName, ...] = get_args(IconName)

# The icons are drawn on a 24-unit grid. Rendering a few sizes up front lets
# QIcon pick a crisp pixmap on standard and high-DPI screens.
_RENDER_SIZES = (16, 24, 32, 48, 64)


def icon_svg(name: IconName) -> bytes:
    """Return the SVG source of a toolbar icon."""
    return files("scene_viewport").joinpath("icons", f"{name}.svg").read_bytes()


def tool_icon(name: IconName) -> QIcon:
    """Build the QIcon for a toolbar action.

    Args:
        name: One of :data:`ICON_NAMES`.

    Returns:
        Icon with pixmaps rendered from the SVG; needs a ``QGuiApplication``.
    """
    renderer = QSvgRenderer(QByteArray(icon_svg(name)))
    icon = QIcon()
    for size in _RENDER_SIZES:
        pixmap = QPixmap(QSize(size, size))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
