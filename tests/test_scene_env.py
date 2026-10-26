"""Tests for procedural viewport environment helpers."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pygfx")

from examples.scene_viewport.env import average_horizon_color, make_studio_cubemap


def test_make_studio_cubemap_is_cube_texture() -> None:
    """Cubemap is a 6-face texture with mipmaps enabled."""
    texture = make_studio_cubemap(size=8)
    assert texture.dim == 2
    assert texture.size == (8, 8, 6)
    assert texture.generate_mipmaps is True


def test_average_horizon_color_midpoint() -> None:
    """Horizon hex sits between sky and ground channels."""
    assert average_horizon_color("#000000", "#ffffff") == "#7f7f7f"
