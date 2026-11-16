"""World-space moves land on the right local xformOps."""

from __future__ import annotations

import pytest
from pxr import Gf, Usd, UsdGeom

from stage_editor.manipulate import (
    rotated,
    scaled,
    set_world_matrix,
    translated,
    world_matrix,
)


def _close(a: Gf.Matrix4d, b: Gf.Matrix4d) -> bool:
    return all(Gf.IsClose(a.GetRow(i), b.GetRow(i), 1e-5) for i in range(4))


def _common(prim: Usd.Prim) -> None:
    api = UsdGeom.XformCommonAPI(prim)
    api.SetTranslate((1, 2, 3))
    api.SetRotate((10, 20, 30), UsdGeom.XformCommonAPI.RotationOrderZXY)
    api.SetScale((1, 2, 1))
    api.SetPivot((0.5, 0, 0))


def _matrix(prim: Usd.Prim) -> None:
    UsdGeom.Xformable(prim).AddTransformOp().Set(
        Gf.Matrix4d(1.0).SetTranslate((4, 0, 0))
    )


def _odd(prim: Usd.Prim) -> None:
    xformable = UsdGeom.Xformable(prim)
    xformable.AddRotateXOp().Set(30.0)
    xformable.AddTranslateOp().Set((0, 1, 0))


@pytest.mark.parametrize(
    ("author", "expected"),
    [(_common, "common"), (_matrix, "matrix"), (_odd, "replaced")],
)
def test_moves_keep_the_op_stack_when_they_can(author, expected: str) -> None:
    stage = Usd.Stage.CreateInMemory()
    parent = UsdGeom.Xform.Define(stage, "/Parent")
    parent.AddRotateYOp().Set(45.0)
    parent.AddScaleOp().Set(Gf.Vec3f(2, 2, 2))
    prim = UsdGeom.Xform.Define(stage, "/Parent/Child").GetPrim()
    author(prim)

    start = world_matrix(prim, 0.0)
    center = start.ExtractTranslation()
    goal = rotated(
        translated(start, Gf.Vec3d(1, -2, 0.5)), Gf.Vec3d(0, 0, 1), 25.0, center
    )
    if expected != "common":
        # A matrix op also takes non-uniform scale under a rotated parent.
        goal = scaled(goal, (1.0, 1.5, 1.0))
    assert set_world_matrix(prim, goal, 0.0) == expected
    assert _close(world_matrix(prim, 0.0), goal)
    if expected == "common":
        api = UsdGeom.XformCommonAPI(prim)
        assert api.GetXformVectors(Usd.TimeCode.Default())[4] == (
            UsdGeom.XformCommonAPI.RotationOrderZXY
        )


def test_animated_ops_get_a_sample_at_the_current_time() -> None:
    stage = Usd.Stage.CreateInMemory()
    prim = UsdGeom.Xform.Define(stage, "/Ball").GetPrim()
    op = UsdGeom.Xformable(prim).AddTranslateOp()
    op.Set(Gf.Vec3d(0, 0, 0), 1.0)
    op.Set(Gf.Vec3d(10, 0, 0), 11.0)
    goal = translated(world_matrix(prim, 6.0), Gf.Vec3d(0, 3, 0))
    set_world_matrix(prim, goal, 6.0)
    assert list(op.GetAttr().GetTimeSamples()) == [1.0, 6.0, 11.0]
    assert op.Get(6.0) == Gf.Vec3d(5, 3, 0)
