"""Gizmo handles under an orthographic test projection."""

from __future__ import annotations

import math

import pytest
from pxr import Gf

from stage_editor.gizmo import SIZE, Gizmo

_C = math.sqrt(0.5)


def _project(point: Gf.Vec3d) -> tuple[float, float]:
    # Looking down -Z: x right, y up, 10 pixels per unit, origin at (200, 200).
    return (200.0 + point[0] * 10.0, 200.0 - point[1] * 10.0)


def _start(tool: str) -> tuple[Gizmo, Gf.Matrix4d]:
    world = Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(1, 1, 0))
    gizmo = Gizmo(tool)
    assert gizmo.layout(world, _project)
    return gizmo, world


@pytest.mark.parametrize(
    ("tool", "grab", "to", "check"),
    [
        # Drag the X arrow 50 px right: 5 units along X. Z points at the camera.
        (
            "translate",
            (250, 190),
            (300, 190),
            lambda m: m.ExtractTranslation() == Gf.Vec3d(6, 1, 0),
        ),
        # Drag the Y arrow 30 px up: 3 units along Y.
        (
            "translate",
            (210, 150),
            (210, 120),
            lambda m: m.ExtractTranslation() == Gf.Vec3d(1, 4, 0),
        ),
        # Grab the Z ring at 45 degrees and drag a quarter turn counter-clockwise.
        (
            "rotate",
            (210 + SIZE * _C, 190 - SIZE * _C),
            (210 - SIZE * _C, 190 - SIZE * _C),
            lambda m: Gf.IsClose(
                m.TransformDir(Gf.Vec3d.XAxis()), Gf.Vec3d.YAxis(), 1e-6
            ),
        ),
        # Drag the X scale handle half its length outward: 1.5x along X.
        (
            "scale",
            (210 + SIZE, 190),
            (210 + SIZE * 1.5, 190),
            lambda m: Gf.IsClose(m.GetRow3(0), Gf.Vec3d(1.5, 0, 0), 1e-6),
        ),
    ],
)
def test_dragging_handles(tool, grab, to, check) -> None:
    gizmo, world = _start(tool)
    handle = gizmo.hit(*grab)
    assert handle is not None
    [result] = gizmo.drag(handle, grab, to, [world], Gf.Vec3d(0, 0, 1))
    assert check(result), result


def test_axis_toward_the_camera_has_no_handle() -> None:
    gizmo, _world = _start("translate")
    assert sorted(h.axis for h in gizmo.handles) == [0, 1]
    assert gizmo.hit(400, 400) is None
