"""Screen-space transform gizmo: handles, hit testing, and drag maths.

The gizmo is drawn by the view with ``QPainter`` over the Hydra image, so
it needs no scene prims. Everything here works from a projection function
(world point to widget pixels), which keeps it testable without a GL
context. Dragging turns mouse motion into new world matrices through
:mod:`stage_editor.manipulate`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pxr import Gf

from stage_editor.manipulate import axes, rotated, scaled, translated

#: World point -> (x, y) in widget pixels, or ``None`` behind the camera.
Projector = Callable[[Gf.Vec3d], tuple[float, float] | None]

TOOLS = ("select", "translate", "rotate", "scale")
SIZE = 90.0  # handle length in pixels
HIT = 8.0  # pick tolerance in pixels
RING_SEGMENTS = 48


@dataclass
class Handle:
    """One grabbable part of the gizmo.

    Attributes:
        axis: 0, 1, 2 for X, Y, Z; 3 for the uniform-scale centre.
        points: Screen polyline (or one point for a box), in pixels.
        direction: World axis the handle acts along or around.
        pixels_per_unit: Screen length of one world unit along ``direction``.
    """

    axis: int
    points: list[tuple[float, float]]
    direction: Gf.Vec3d = field(default_factory=Gf.Vec3d)
    pixels_per_unit: float = 0.0


def _distance_to_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = (
        0.0
        if length == 0
        else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length))
    )
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


class Gizmo:
    """Handles for one tool around a prim's pivot.

    Args:
        tool: ``translate``, ``rotate``, or ``scale``.
        space: ``world`` or ``local`` axes; scale always uses local axes.
    """

    def __init__(self, tool: str, space: str = "world") -> None:
        self.tool = tool
        self.space = space
        self.center = Gf.Vec3d()
        self.center_px: tuple[float, float] | None = None
        self.handles: list[Handle] = []

    def layout(self, world: Gf.Matrix4d, project: Projector) -> bool:
        """Place the handles for a prim at ``world``.

        Args:
            world: The prim's world matrix; the gizmo sits at its origin.
            project: Maps world points to widget pixels.

        Returns:
            Whether anything is visible (the pivot is in front of the camera).
        """
        self.handles = []
        self.center = world.ExtractTranslation()
        self.center_px = project(self.center)
        if self.center_px is None or self.tool not in TOOLS[1:]:
            return False
        space = "local" if self.tool == "scale" else self.space
        cx, cy = self.center_px
        frame = axes(world, space)
        # Pixels per world unit at the pivot: the longest projected unit axis
        # (orthogonal axes cannot all point at the camera).
        lengths = []
        for axis in frame:
            tip = project(self.center + axis)
            if tip is not None:
                lengths.append(math.hypot(tip[0] - cx, tip[1] - cy))
        per_unit = max(lengths, default=0.0)
        if per_unit < 1e-9:
            return False
        world_size = SIZE / per_unit  # world length that spans SIZE pixels
        for index, axis in enumerate(frame):
            if self.tool == "rotate":
                ring = self._ring(axis, world_size, project)
                if ring:
                    self.handles.append(Handle(index, ring, axis, per_unit))
                continue
            end = project(self.center + axis * world_size)
            if end is None:
                continue
            visible = math.hypot(end[0] - cx, end[1] - cy)
            # An axis pointing at the camera is too short to grab; skip it.
            if visible < SIZE * 0.2:
                continue
            self.handles.append(
                Handle(index, [(cx, cy), end], axis, visible / world_size)
            )
        if self.tool == "scale":
            self.handles.append(Handle(3, [(cx, cy)]))
        return True

    def _ring(
        self, axis: Gf.Vec3d, radius: float, project: Projector
    ) -> list[tuple[float, float]]:
        helper = Gf.Vec3d.XAxis() if abs(axis[0]) < 0.9 else Gf.Vec3d.YAxis()
        u = Gf.Cross(axis, helper).GetNormalized()
        v = Gf.Cross(axis, u)
        points = []
        for i in range(RING_SEGMENTS + 1):
            angle = 2 * math.pi * i / RING_SEGMENTS
            point = project(
                self.center + (u * math.cos(angle) + v * math.sin(angle)) * radius
            )
            if point is None:
                return []
            points.append(point)
        return points

    def hit(self, x: float, y: float) -> Handle | None:
        """Return the handle under widget pixel ``(x, y)``, or ``None``."""
        best: tuple[float, Handle] | None = None
        for handle in self.handles:
            if len(handle.points) == 1:
                hx, hy = handle.points[0]
                distance = max(abs(x - hx), abs(y - hy)) - HIT
            else:
                distance = min(
                    _distance_to_segment((x, y), a, b)
                    for a, b in zip(handle.points, handle.points[1:], strict=False)
                )
            if distance <= HIT and (best is None or distance < best[0]):
                best = (distance, handle)
        return best[1] if best else None

    def drag(
        self,
        handle: Handle,
        start: tuple[float, float],
        current: tuple[float, float],
        worlds: Sequence[Gf.Matrix4d],
        toward_camera: Gf.Vec3d,
    ) -> list[Gf.Matrix4d]:
        """Return the world matrices after dragging ``handle`` from ``start``.

        Args:
            handle: The handle grabbed at mouse press.
            start: Mouse position at the press, in pixels.
            current: Mouse position now, in pixels.
            worlds: The dragged prims' world matrices at the press.
            toward_camera: World direction from the pivot to the eye.
        """
        dx, dy = current[0] - start[0], current[1] - start[1]
        if self.tool == "translate" and len(handle.points) == 2:
            (ax, ay), (bx, by) = handle.points
            length = math.hypot(bx - ax, by - ay) or 1.0
            along = (dx * (bx - ax) + dy * (by - ay)) / length
            delta = handle.direction * (along / handle.pixels_per_unit)
            return [translated(world, delta) for world in worlds]
        if self.tool == "rotate" and self.center_px is not None:
            cx, cy = self.center_px
            v1 = (start[0] - cx, cy - start[1])
            v2 = (current[0] - cx, cy - current[1])
            angle = math.degrees(
                math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1])
            )
            facing = Gf.Dot(handle.direction, toward_camera) >= 0
            degrees = angle if facing else -angle
            return [rotated(w, handle.direction, degrees, self.center) for w in worlds]
        if self.tool == "scale":
            if handle.axis == 3:
                factor = max(0.01, 1.0 + dx / SIZE)
                factors = (factor, factor, factor)
            else:
                (ax, ay), (bx, by) = handle.points
                length = math.hypot(bx - ax, by - ay) or 1.0
                along = (dx * (bx - ax) + dy * (by - ay)) / length
                factor = max(0.01, 1.0 + along / SIZE)
                factors = tuple(factor if i == handle.axis else 1.0 for i in range(3))
            return [scaled(world, factors) for world in worlds]
        return list(worlds)
