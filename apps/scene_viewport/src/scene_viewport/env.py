"""Procedural environment cubemap for IBL and skybox."""

from __future__ import annotations

import numpy as np
import pygfx as gfx

from scene_viewport.xform_math import as_vec3


def _parse_color(color: str) -> tuple[float, float, float]:
    """Parse ``#rgb`` / ``#rrggbb`` into a linear-ish float RGB triple."""
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (0.5, 0.55, 0.65)
    red = int(value[0:2], 16) / 255.0
    green = int(value[2:4], 16) / 255.0
    blue = int(value[4:6], 16) / 255.0
    return (red, green, blue)


def make_studio_cubemap(
    *,
    sky: str = "#6b7c95",
    ground: str = "#2a2e36",
    horizon: str = "#4a5360",
    size: int = 32,
) -> gfx.Texture:
    """Build a soft studio cubemap from sky / horizon / ground colors.

    Faces are solid-ish gradients (no image assets). ``generate_mipmaps``
    is enabled so Standard-material roughness filtering works.

    Args:
        sky: Color for the +Y face.
        ground: Color for the -Y face.
        horizon: Color for the four side faces.
        size: Face resolution in pixels.

    Returns:
        A cube ``Texture`` (``dim=2``, depth 6).
    """
    sky_rgb = np.asarray(_parse_color(sky), dtype=np.float32)
    ground_rgb = np.asarray(_parse_color(ground), dtype=np.float32)
    horizon_rgb = np.asarray(_parse_color(horizon), dtype=np.float32)
    # Face order: +X, -X, +Y, -Y, +Z, -Z
    faces = np.zeros((6, size, size, 3), dtype=np.float32)
    faces[0] = horizon_rgb
    faces[1] = horizon_rgb
    faces[2] = sky_rgb
    faces[3] = ground_rgb
    faces[4] = horizon_rgb
    faces[5] = horizon_rgb
    # Soften side faces toward sky at the top edge.
    ramp = np.linspace(0.0, 1.0, size, dtype=np.float32).reshape(size, 1, 1)
    for index in (0, 1, 4, 5):
        faces[index] = horizon_rgb * (1.0 - ramp) + sky_rgb * ramp
    return gfx.Texture(
        faces,
        dim=2,
        size=(size, size, 6),
        generate_mipmaps=True,
    )


def average_horizon_color(sky: str, ground: str) -> str:
    """Return a hex color between sky and ground for side faces.

    Args:
        sky: Top color.
        ground: Bottom color.
    """
    sky_rgb = np.asarray(_parse_color(sky), dtype=np.float64)
    ground_rgb = np.asarray(_parse_color(ground), dtype=np.float64)
    mid = as_vec3((sky_rgb + ground_rgb) * 0.5)
    return f"#{int(mid[0] * 255):02x}{int(mid[1] * 255):02x}{int(mid[2] * 255):02x}"
