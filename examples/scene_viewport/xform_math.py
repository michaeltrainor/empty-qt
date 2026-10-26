"""Matrix helpers for pivot-aware transform edits."""

from __future__ import annotations

import numpy as np
import pylinalg as la

Vec3 = tuple[float, float, float]
Trs = tuple[Vec3, Vec3, Vec3]


def as_vec3(value: object) -> Vec3:
    """Coerce an array-like value to a float triple."""
    array = np.asarray(value, dtype=np.float64).reshape(-1)
    return (float(array[0]), float(array[1]), float(array[2]))


def mat_from_trs(
    position: Vec3,
    rotation_euler: Vec3,
    scale: Vec3,
) -> np.ndarray:
    """Build a 4x4 matrix from local TRS (euler XYZ radians)."""
    rotation = la.quat_from_euler(rotation_euler, order="xyz")
    return np.asarray(la.mat_compose(position, rotation, scale), dtype=np.float64)


def trs_from_mat(matrix: np.ndarray) -> Trs:
    """Decompose a 4x4 matrix into position, euler XYZ, and scale."""
    position, rotation, scale = la.mat_decompose(np.asarray(matrix, dtype=np.float64))
    euler = la.quat_to_euler(rotation, order="xyz")
    return as_vec3(position), as_vec3(euler), as_vec3(scale)


def translate_mat(offset: Vec3) -> np.ndarray:
    """Return a translation matrix for ``offset``."""
    return np.asarray(la.mat_from_translation(offset), dtype=np.float64)


def pivot_world_matrix(transform_world: np.ndarray, pivot_local: Vec3) -> np.ndarray:
    """World matrix whose origin sits at the local pivot of ``transform_world``."""
    return np.asarray(transform_world, dtype=np.float64) @ translate_mat(pivot_local)


def transform_world_from_proxy(
    proxy_world: np.ndarray,
    pivot_local: Vec3,
) -> np.ndarray:
    """Recover transform world matrix from a pivot-aligned proxy matrix."""
    return np.asarray(proxy_world, dtype=np.float64) @ translate_mat(
        (-pivot_local[0], -pivot_local[1], -pivot_local[2])
    )


def local_pivot_from_world_point(
    transform_world: np.ndarray,
    world_point: Vec3,
) -> Vec3:
    """Express a world-space point as a local pivot under ``transform_world``."""
    inv = la.mat_inverse(np.asarray(transform_world, dtype=np.float64))
    point = np.array(
        [world_point[0], world_point[1], world_point[2], 1.0],
        dtype=np.float64,
    )
    local = inv @ point
    return (float(local[0]), float(local[1]), float(local[2]))


def average_positions(positions: list[Vec3]) -> Vec3:
    """Return the arithmetic mean of world positions."""
    if not positions:
        return (0.0, 0.0, 0.0)
    arr = np.asarray(positions, dtype=np.float64)
    mean = arr.mean(axis=0)
    return as_vec3(mean)


def local_trs_under_parent(
    world_matrix: np.ndarray,
    parent_world: np.ndarray | None,
) -> Trs:
    """Convert a world matrix into local TRS under an optional parent."""
    world = np.asarray(world_matrix, dtype=np.float64)
    if parent_world is None:
        return trs_from_mat(world)
    parent = np.asarray(parent_world, dtype=np.float64)
    local = la.mat_inverse(parent) @ world
    return trs_from_mat(local)
