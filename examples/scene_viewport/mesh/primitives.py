"""Numba-backed mesh primitives: box, plane, and UV sphere."""

from __future__ import annotations

import numpy as np
from numba import njit
from numpy.typing import NDArray

from examples.scene_viewport.mesh.types import MeshBuffers


@njit(cache=True)
def _box_arrays(
    width: float,
    height: float,
    depth: float,
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.uint32]]:
    hx = width * 0.5
    hy = height * 0.5
    hz = depth * 0.5
    positions = np.empty((24, 3), dtype=np.float32)
    normals = np.empty((24, 3), dtype=np.float32)
    # +X
    positions[0] = (hx, -hy, -hz)
    positions[1] = (hx, hy, -hz)
    positions[2] = (hx, hy, hz)
    positions[3] = (hx, -hy, hz)
    for i in range(4):
        normals[i] = (1.0, 0.0, 0.0)
    # -X
    positions[4] = (-hx, -hy, hz)
    positions[5] = (-hx, hy, hz)
    positions[6] = (-hx, hy, -hz)
    positions[7] = (-hx, -hy, -hz)
    for i in range(4, 8):
        normals[i] = (-1.0, 0.0, 0.0)
    # +Y
    positions[8] = (-hx, hy, -hz)
    positions[9] = (-hx, hy, hz)
    positions[10] = (hx, hy, hz)
    positions[11] = (hx, hy, -hz)
    for i in range(8, 12):
        normals[i] = (0.0, 1.0, 0.0)
    # -Y
    positions[12] = (-hx, -hy, hz)
    positions[13] = (-hx, -hy, -hz)
    positions[14] = (hx, -hy, -hz)
    positions[15] = (hx, -hy, hz)
    for i in range(12, 16):
        normals[i] = (0.0, -1.0, 0.0)
    # +Z
    positions[16] = (-hx, -hy, hz)
    positions[17] = (hx, -hy, hz)
    positions[18] = (hx, hy, hz)
    positions[19] = (-hx, hy, hz)
    for i in range(16, 20):
        normals[i] = (0.0, 0.0, 1.0)
    # -Z
    positions[20] = (hx, -hy, -hz)
    positions[21] = (-hx, -hy, -hz)
    positions[22] = (-hx, hy, -hz)
    positions[23] = (hx, hy, -hz)
    for i in range(20, 24):
        normals[i] = (0.0, 0.0, -1.0)

    indices = np.empty((12, 3), dtype=np.uint32)
    face = 0
    for base in range(0, 24, 4):
        indices[face, 0] = base
        indices[face, 1] = base + 1
        indices[face, 2] = base + 2
        face += 1
        indices[face, 0] = base
        indices[face, 1] = base + 2
        indices[face, 2] = base + 3
        face += 1
    return positions, normals, indices


@njit(cache=True)
def _plane_arrays(
    width: float,
    depth: float,
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.uint32]]:
    hx = width * 0.5
    hz = depth * 0.5
    positions = np.empty((4, 3), dtype=np.float32)
    normals = np.empty((4, 3), dtype=np.float32)
    positions[0] = (-hx, 0.0, -hz)
    positions[1] = (hx, 0.0, -hz)
    positions[2] = (hx, 0.0, hz)
    positions[3] = (-hx, 0.0, hz)
    for i in range(4):
        normals[i] = (0.0, 1.0, 0.0)
    # CCW when viewed from +Y so winding matches the +Y normals.
    indices = np.empty((2, 3), dtype=np.uint32)
    indices[0] = (0, 2, 1)
    indices[1] = (0, 3, 2)
    return positions, normals, indices


@njit(cache=True)
def _uv_sphere_arrays(
    radius: float,
    stacks: int,
    slices: int,
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.uint32]]:
    vertex_count = (stacks + 1) * (slices + 1)
    positions = np.empty((vertex_count, 3), dtype=np.float32)
    normals = np.empty((vertex_count, 3), dtype=np.float32)
    pi = np.pi
    v = 0
    for i in range(stacks + 1):
        v_angle = pi * i / stacks
        y = np.cos(v_angle)
        ring_r = np.sin(v_angle)
        for j in range(slices + 1):
            u_angle = 2.0 * pi * j / slices
            x = ring_r * np.cos(u_angle)
            z = ring_r * np.sin(u_angle)
            positions[v, 0] = radius * x
            positions[v, 1] = radius * y
            positions[v, 2] = radius * z
            normals[v, 0] = x
            normals[v, 1] = y
            normals[v, 2] = z
            v += 1

    tri_count = stacks * slices * 2
    indices = np.empty((tri_count, 3), dtype=np.uint32)
    t = 0
    for i in range(stacks):
        for j in range(slices):
            a = i * (slices + 1) + j
            b = a + slices + 1
            # CCW from outside so winding matches outward normals.
            indices[t, 0] = a
            indices[t, 1] = a + 1
            indices[t, 2] = b
            t += 1
            indices[t, 0] = a + 1
            indices[t, 1] = b + 1
            indices[t, 2] = b
            t += 1
    return positions, normals, indices


def box(width: float = 1.0, height: float = 1.0, depth: float = 1.0) -> MeshBuffers:
    """Axis-aligned box centered at the origin."""
    positions, normals, indices = _box_arrays(width, height, depth)
    return MeshBuffers(positions=positions, normals=normals, indices=indices)


def plane(width: float = 1.0, depth: float = 1.0) -> MeshBuffers:
    """XZ plane centered at the origin, normal +Y."""
    positions, normals, indices = _plane_arrays(width, depth)
    return MeshBuffers(positions=positions, normals=normals, indices=indices)


def uv_sphere(
    radius: float = 0.5,
    stacks: int = 16,
    slices: int = 24,
) -> MeshBuffers:
    """UV sphere centered at the origin."""
    if stacks < 2 or slices < 3:
        msg = "stacks must be >= 2 and slices >= 3"
        raise ValueError(msg)
    positions, normals, indices = _uv_sphere_arrays(radius, stacks, slices)
    return MeshBuffers(positions=positions, normals=normals, indices=indices)
