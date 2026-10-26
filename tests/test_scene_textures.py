"""Tests for albedo texture loading."""

from __future__ import annotations

from pathlib import Path

from examples.scene_viewport.mesh.primitives import box, plane, uv_sphere
from examples.scene_viewport.textures import load_albedo_texture
from PySide6.QtGui import QColor, QImage


def test_load_albedo_texture_roundtrip(tmp_path: Path) -> None:
    """QImage-saved PNG becomes a pygfx Texture."""
    path = tmp_path / "swatch.png"
    image = QImage(4, 4, QImage.Format.Format_RGBA8888)
    image.fill(QColor(10, 20, 30, 255))
    assert image.save(str(path))
    texture = load_albedo_texture(path)
    assert texture is not None
    assert texture.size[:2] == (4, 4)


def test_load_albedo_texture_missing() -> None:
    """Missing or empty paths return None."""
    assert load_albedo_texture(Path("")) is None
    assert load_albedo_texture(Path("/no/such/albedo.png")) is None


def test_primitives_have_uvs() -> None:
    """Box, plane, and sphere buffers include matching UV rows."""
    for buffers in (box(), plane(), uv_sphere()):
        assert buffers.texcoords.shape == (buffers.positions.shape[0], 2)
