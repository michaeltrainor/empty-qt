"""Shared mesh buffer container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

PrimitiveKind = Literal["box", "plane", "sphere"]


@dataclass(frozen=True, slots=True)
class MeshBuffers:
    """Triangle mesh as contiguous float32 / uint32 arrays.

    Args:
        positions: Vertex positions, shape ``(N, 3)``.
        normals: Per-vertex normals, shape ``(N, 3)``.
        indices: Triangle indices, shape ``(T, 3)``.
    """

    positions: NDArray[np.float32]
    normals: NDArray[np.float32]
    indices: NDArray[np.uint32]
