"""Screen-space math and selection rules for marquee (box) selection."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

#: Logical pixels a left-drag must travel before it becomes a box.
BOX_THRESHOLD_PX = 4.0

#: ``(left, top, right, bottom)`` in logical pixels, origin top-left.
ScreenRect = tuple[float, float, float, float]


def normalized_rect(
    start: tuple[float, float],
    end: tuple[float, float],
) -> ScreenRect:
    """Return the rectangle spanned by two drag corners, in any order.

    Args:
        start: Pointer position where the drag began.
        end: Current pointer position.

    Returns:
        ``(left, top, right, bottom)`` with ``left <= right`` and
        ``top <= bottom``.
    """
    return (
        min(start[0], end[0]),
        min(start[1], end[1]),
        max(start[0], end[0]),
        max(start[1], end[1]),
    )


def screen_bounds(
    view_projection: np.ndarray,
    points: np.ndarray,
    size: tuple[float, float],
) -> ScreenRect | None:
    """Project world points and return their screen-space bounding rect.

    Points at or behind the camera plane are dropped, so an object that is
    partly behind the camera is judged by its visible part.

    Args:
        view_projection: 4x4 camera matrix (projection @ view).
        points: ``(N, 3)`` world-space points.
        size: Viewport ``(width, height)`` in logical pixels.

    Returns:
        ``(left, top, right, bottom)`` in logical pixels, or ``None`` when
        every point is behind the camera.
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if pts.size == 0:
        return None
    homogeneous = np.hstack([pts, np.ones((len(pts), 1))])
    clip = homogeneous @ np.asarray(view_projection, dtype=np.float64).T
    in_front = clip[:, 3] > 1e-9
    if not np.any(in_front):
        return None
    ndc = clip[in_front, :2] / clip[in_front, 3:4]
    width, height = size
    xs = (ndc[:, 0] + 1.0) * 0.5 * width
    ys = (1.0 - ndc[:, 1]) * 0.5 * height
    return (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))


def rects_overlap(a: ScreenRect, b: ScreenRect) -> bool:
    """Return whether two screen rects touch or overlap.

    Args:
        a: First ``(left, top, right, bottom)`` rect.
        b: Second ``(left, top, right, bottom)`` rect.

    Returns:
        ``True`` when the rects share any area or edge.
    """
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def selection_after_box(
    current: Sequence[str],
    hits: Sequence[str],
    *,
    shift: bool,
    ctrl: bool,
) -> list[str]:
    """Return the selection order after a box drag (Maya rules).

    Args:
        current: Selection order before the drag (last is primary).
        hits: Transform UIDs the box touched, in scene order.
        shift: Shift held — toggle each hit (add with Ctrl).
        ctrl: Ctrl held — remove the hits (add with Shift).

    Returns:
        The new selection order. A plain drag replaces the selection with
        ``hits``, so a drag over empty space clears it.
    """
    order = list(current)
    hit_list = list(dict.fromkeys(hits))
    if shift and ctrl:
        return order + [name for name in hit_list if name not in order]
    if ctrl:
        removed = set(hit_list)
        return [name for name in order if name not in removed]
    if shift:
        toggled = set(hit_list)
        kept = [name for name in order if name not in toggled]
        return kept + [name for name in hit_list if name not in order]
    return hit_list
