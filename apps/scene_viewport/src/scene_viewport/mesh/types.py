"""Shared mesh buffer container."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from scene_viewport.components import AlphaMode, MapKind, PrimitiveKind

__all__ = ["AlphaMode", "MapKind", "MeshBuffers", "PrimitiveKind"]


@dataclass(frozen=True, slots=True)
class MeshBuffers:
    """Triangle mesh as contiguous float32 / uint32 arrays.

    Args:
        positions: Vertex positions, shape ``(N, 3)``.
        normals: Per-vertex normals, shape ``(N, 3)``.
        indices: Triangle indices, shape ``(T, 3)``.
        texcoords: UV coordinates, shape ``(N, 2)``.
        tangents: Optional ``(N, 4)`` tangents for normal mapping: the
            direction of +U, and in ``w`` the sign that gives the bitangent
            as ``cross(normal, tangent) * w``.
    """

    positions: NDArray[np.float32]
    normals: NDArray[np.float32]
    indices: NDArray[np.uint32]
    texcoords: NDArray[np.float32]
    tangents: NDArray[np.float32] | None = None
