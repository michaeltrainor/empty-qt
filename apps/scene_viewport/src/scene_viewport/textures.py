"""Load albedo images and model-file textures into pygfx textures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pygfx as gfx
from numpy.typing import NDArray
from PySide6.QtGui import QImage

from scene_viewport.mesh.importer import (
    ImportedMaps,
    MaterialSurface,
    ModelImportError,
    load_model,
)


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
    return texture_from_rgba(pixels)


def texture_from_rgba(
    pixels: NDArray[np.uint8],
    *,
    color: bool = True,
) -> gfx.Texture:
    """Wrap an RGBA image, top row first, as a ``gfx.Texture``.

    Args:
        pixels: ``(height, width, 4)`` ``uint8`` image as files store it.
        color: ``True`` for color data (sRGB); ``False`` for data maps such
            as normals or roughness, which are linear.

    Returns:
        A 2D texture for a ``TextureMap``.
    """
    # pygfx UV origin is bottom-left; image rows run top to bottom.
    return gfx.Texture(
        np.ascontiguousarray(np.flipud(pixels)),
        dim=2,
        colorspace="srgb" if color else "physical",
    )


def file_material(
    source: Path, mesh: str
) -> tuple[MaterialSurface | None, ImportedMaps | None]:
    """Return the surface and texture images of mesh ``mesh`` in ``source``.

    One read of the model file serves both, so a material build stamps the
    file once.

    Args:
        source: Model file, as stored on a ``file`` mesh.
        mesh: Mesh key inside the file.

    Returns:
        ``(surface, maps)``, each ``None`` when the file can't be read or
        has nothing for the mesh.
    """
    try:
        model = load_model(source)
    except ModelImportError:
        return None, None
    return model.materials.get(mesh), model.maps.get(mesh)


def file_maps(source: Path, mesh: str) -> ImportedMaps | None:
    """Return the texture images of mesh ``mesh`` in model file ``source``.

    Args:
        source: Model file, as stored on a ``file`` mesh.
        mesh: Mesh key inside the file.

    Returns:
        The mesh's images, or ``None`` when the file can't be read or the
        mesh has none.
    """
    try:
        return load_model(source).maps.get(mesh)
    except ModelImportError:
        return None
