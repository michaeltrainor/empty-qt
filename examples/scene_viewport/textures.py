"""Load albedo images into pygfx textures (Qt QImage, no extra deps)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pygfx as gfx
from PySide6.QtGui import QImage


def load_albedo_texture(path: Path) -> gfx.Texture | None:
    """Load an image file as an RGBA ``gfx.Texture``.

    Args:
        path: Filesystem path to a Qt-readable image (png/jpg/…).

    Returns:
        A 2D texture, or ``None`` when the path is empty or unloadable.
    """
    if not path or not str(path).strip() or not path.is_file():
        return None
    image = QImage(str(path))
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = image.width()
    height = image.height()
    ptr = image.constBits()
    if ptr is None:
        return None
    # QImage may pad rows; copy via bytes then reshape.
    raw = bytes(ptr)
    array = np.frombuffer(raw, dtype=np.uint8)
    expected = width * height * 4
    if array.size < expected:
        return None
    # Prefer tightly packed rows when stride matches.
    bytes_per_line = image.bytesPerLine()
    if bytes_per_line == width * 4:
        pixels = array[:expected].reshape((height, width, 4)).copy()
    else:
        pixels = np.empty((height, width, 4), dtype=np.uint8)
        row_stride = bytes_per_line
        for row in range(height):
            start = row * row_stride
            pixels[row] = array[start : start + width * 4].reshape((width, 4))
    # OpenGL/pygfx UV origin is bottom-left; flip vertically for image files.
    pixels = np.flipud(pixels)
    return gfx.Texture(pixels, dim=2)
