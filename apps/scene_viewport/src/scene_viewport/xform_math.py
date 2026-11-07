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


def look_at_matrix(
    position: Vec3,
    target: Vec3,
    up: Vec3 = (0.0, 1.0, 0.0),
) -> np.ndarray:
    """Build a 4x4 world matrix at ``position`` whose -Z axis points at ``target``.

    This is the camera and light convention in glTF and pygfx. When the
    view direction is parallel to ``up``, +Z is used as the up hint.

    Args:
        position: World position of the node.
        target: World point to look at.
        up: Preferred up direction.

    Returns:
        A rotation-and-translation matrix (no scale).
    """
    eye = np.asarray(position, dtype=np.float64)
    forward = np.asarray(target, dtype=np.float64) - eye
    length = float(np.linalg.norm(forward))
    forward = np.array([0.0, 0.0, -1.0]) if length < 1e-9 else forward / length
    up_vector = np.asarray(up, dtype=np.float64)
    if abs(float(np.dot(forward, up_vector))) > 0.999:
        up_vector = np.array([0.0, 0.0, 1.0])
    # Right-handed frame with +Z pointing back along the view direction.
    z_axis = -forward
    x_axis = np.cross(up_vector, z_axis)
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    matrix = np.eye(4)
    matrix[:3, 0] = x_axis
    matrix[:3, 1] = y_axis
    matrix[:3, 2] = z_axis
    matrix[:3, 3] = eye
    return matrix


def look_at_trs(position: Vec3, target: Vec3, up: Vec3 = (0.0, 1.0, 0.0)) -> Trs:
    """Return TRS for a node at ``position`` looking at ``target``.

    Args:
        position: World position of the node.
        target: World point to look at.
        up: Preferred up direction.

    Returns:
        ``(position, euler XYZ radians, scale)`` with unit scale.
    """
    return trs_from_mat(look_at_matrix(position, target, up))


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


def snap_translation(
    start: np.ndarray,
    current: np.ndarray,
    step: float,
    *,
    local_axes: bool = False,
) -> np.ndarray:
    """Round the translation delta from ``start`` to ``current`` to ``step``.

    Args:
        start: World matrix when the drag began.
        current: World matrix after the latest drag step.
        step: Increment in world units. Non-positive values disable snapping.
        local_axes: Quantize along ``start``'s rotated axes instead of world
            X/Y/Z, so a local-space drag stays on its handle axis.

    Returns:
        ``current`` with its translation replaced by the snapped value.
    """
    start = np.asarray(start, dtype=np.float64)
    out = np.array(current, dtype=np.float64)
    if step <= 0.0:
        return out
    delta = out[:3, 3] - start[:3, 3]
    if local_axes:
        # The gizmo's local axes are these columns. Under shear (a rotated
        # child of a non-uniformly scaled parent) they aren't orthogonal, so
        # solve for the step counts instead of projecting onto each axis.
        # A zero-scale axis has no direction; pinv gives it zero steps.
        linear = start[:3, :3]
        norms = np.linalg.norm(linear, axis=0)
        frame = linear / np.where(norms > 1e-12, norms, 1.0)
        steps = np.linalg.pinv(frame) @ delta
        delta = frame @ (np.round(steps / step) * step)
    else:
        delta = np.round(delta / step) * step
    out[:3, 3] = start[:3, 3] + delta
    return out


def snap_rotation(
    start: np.ndarray,
    current: np.ndarray,
    step: float,
) -> np.ndarray:
    """Round the rotation from ``start`` to ``current`` to ``step`` radians.

    Args:
        start: World matrix when the drag began.
        current: World matrix after the latest drag step.
        step: Angle increment in radians. Non-positive values disable snapping.

    Returns:
        ``current`` with its rotation replaced by the snapped value.
    """
    out = np.array(current, dtype=np.float64)
    if step <= 0.0:
        return out
    _p0, q0, _s0 = la.mat_decompose(np.asarray(start, dtype=np.float64))
    position, q1, scale = la.mat_decompose(out)
    delta = np.asarray(la.quat_mul(q1, la.quat_inv(q0)), dtype=np.float64)
    if delta[3] < 0.0:
        delta = -delta
    angle = 2.0 * float(np.arccos(np.clip(delta[3], -1.0, 1.0)))
    sin_half = float(np.linalg.norm(delta[:3]))
    if sin_half < 1e-9:
        return np.asarray(la.mat_compose(position, q0, scale), dtype=np.float64)
    axis = delta[:3] / sin_half
    snapped = round(angle / step) * step
    rotation = la.quat_mul(la.quat_from_axis_angle(axis, snapped), q0)
    return np.asarray(la.mat_compose(position, rotation, scale), dtype=np.float64)


def closest_axis_offset(
    origin: np.ndarray,
    axis: np.ndarray,
    ray_origin: np.ndarray,
    ray_direction: np.ndarray,
) -> np.ndarray | None:
    """Return the offset along ``axis`` to the point nearest a pointer ray.

    Used to keep a one-axis drag exactly on its axis: the result is
    ``s * axis`` for the ``s`` that brings ``origin + s * axis`` closest to
    the ray.

    Args:
        origin: Point the axis passes through (the grabbed handle).
        axis: Axis direction; need not be unit length.
        ray_origin: Start of the ray under the pointer.
        ray_direction: Direction of that ray.

    Returns:
        The world-space offset from ``origin``, or ``None`` when the ray is
        parallel to the axis and no single nearest point exists.
    """
    u = np.asarray(axis, dtype=np.float64)
    v = np.asarray(ray_direction, dtype=np.float64)
    w0 = np.asarray(origin, dtype=np.float64) - np.asarray(ray_origin, dtype=np.float64)
    a, b, c = float(u @ u), float(u @ v), float(v @ v)
    denom = a * c - b * b
    if a <= 0.0 or c <= 0.0 or abs(denom) <= 1e-9 * a * c:
        return None
    s = (b * float(v @ w0) - c * float(u @ w0)) / denom
    return u * s


def ray_plane_offset(
    origin: np.ndarray,
    normal: np.ndarray,
    ray_origin: np.ndarray,
    ray_direction: np.ndarray,
) -> np.ndarray | None:
    """Return the offset from ``origin`` to where a ray meets its plane.

    Args:
        origin: A point on the plane (the grabbed handle).
        normal: Plane normal; need not be unit length.
        ray_origin: Start of the ray under the pointer.
        ray_direction: Direction of that ray.

    Returns:
        The world-space offset from ``origin``, or ``None`` when the ray is
        parallel to the plane.
    """
    n = np.asarray(normal, dtype=np.float64)
    d = np.asarray(ray_direction, dtype=np.float64)
    o = np.asarray(origin, dtype=np.float64)
    r = np.asarray(ray_origin, dtype=np.float64)
    denom = float(n @ d)
    if abs(denom) <= 1e-9 * float(np.linalg.norm(n) * np.linalg.norm(d)):
        return None
    t = float(n @ (o - r)) / denom
    return r + d * t - o
