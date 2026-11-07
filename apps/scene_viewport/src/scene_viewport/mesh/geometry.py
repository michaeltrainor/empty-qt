"""Convert mesh buffers into pygfx geometry."""

from __future__ import annotations

from pathlib import Path

import pygfx as gfx

from scene_viewport.mesh.importer import ModelImportError, load_model
from scene_viewport.mesh.primitives import box, plane, uv_sphere
from scene_viewport.mesh.types import MeshBuffers, PrimitiveKind


def to_geometry(buffers: MeshBuffers) -> gfx.Geometry:
    """Build a pygfx ``Geometry`` from ``MeshBuffers``."""
    geometry = gfx.Geometry(
        positions=buffers.positions,
        normals=buffers.normals,
        indices=buffers.indices,
        texcoords=buffers.texcoords,
    )
    if buffers.tangents is not None:
        # With tangents pygfx normal-maps in a fixed frame; without them it
        # derives one from the view, which collapses in orthographic views.
        geometry.tangents = gfx.Buffer(buffers.tangents)
    return geometry


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


def file_geometry(source: Path, mesh: str) -> gfx.Geometry:
    """Return pygfx geometry for mesh ``mesh`` of the model file ``source``.

    Raises:
        ModelImportError: When the file can't be read or has no such mesh.
    """
    buffers = load_model(source).meshes.get(mesh)
    if buffers is None:
        msg = f"{source.name} has no mesh {mesh!r}"
        raise ModelImportError(msg)
    return to_geometry(buffers)
