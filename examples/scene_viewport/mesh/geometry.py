"""Convert mesh buffers into pygfx geometry."""

from __future__ import annotations

import pygfx as gfx

from examples.scene_viewport.mesh.primitives import box, plane, uv_sphere
from examples.scene_viewport.mesh.types import MeshBuffers, PrimitiveKind


def to_geometry(buffers: MeshBuffers) -> gfx.Geometry:
    """Build a pygfx ``Geometry`` from ``MeshBuffers``."""
    return gfx.Geometry(
        positions=buffers.positions,
        normals=buffers.normals,
        indices=buffers.indices,
    )


def geometry_for(kind: PrimitiveKind) -> gfx.Geometry:
    """Tessellate a named primitive and return pygfx geometry."""
    if kind == "box":
        return to_geometry(box(1.0, 1.0, 1.0))
    if kind == "plane":
        return to_geometry(plane(1.0, 1.0))
    if kind == "sphere":
        return to_geometry(uv_sphere(0.5, stacks=16, slices=24))
    msg = f"unknown primitive kind: {kind!r}"
    raise ValueError(msg)
