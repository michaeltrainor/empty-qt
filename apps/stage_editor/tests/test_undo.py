"""Undoable edits: text parsing and snapshot-based undo of Sdf specs."""

from __future__ import annotations

import pytest
from pxr import Gf, Sdf, UsdGeom

from stage_editor.document import StageDocument
from stage_editor.undo import parse_value

T = Sdf.ValueTypeNames


@pytest.mark.parametrize(
    ("type_name", "text", "expected"),
    [
        (T.Bool, "true", True),
        (T.Bool, "0", False),
        (T.Int, " 42 ", 42),
        (T.Double, "2.5", 2.5),
        (T.Token, "render", "render"),
        (T.Asset, "@tex/wood.png@", Sdf.AssetPath("tex/wood.png")),
        (T.Float3, "(1, 2, 3)", Gf.Vec3f(1, 2, 3)),
        (T.Color3f, "[0.5, 0.25, 1]", Gf.Vec3f(0.5, 0.25, 1)),
        (T.Quatf, "(1, 0, 0, 0)", Gf.Quatf(1, Gf.Vec3f(0, 0, 0))),
        (T.Float3Array, "[(1, 2, 3)]", ValueError),
        (T.Matrix4d, "((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1))", ValueError),
        (T.Float3, "(1, 2)", ValueError),
        (T.Bool, "maybe", ValueError),
    ],
)
def test_parse_value(type_name: Sdf.ValueTypeName, text: str, expected: object) -> None:
    if expected is ValueError:
        with pytest.raises(ValueError, match="not"):
            parse_value(type_name, text)
    else:
        assert parse_value(type_name, text) == expected


def test_attribute_and_variant_edits_undo_and_redo() -> None:
    document = StageDocument()
    stage = document.stage
    root = stage.GetRootLayer()
    ball = UsdGeom.Sphere.Define(stage, "/World/Group/Ball")
    ball.GetRadiusAttr().Set(1.0)
    spin = ball.AddRotateYOp()
    spin.Set(0.0, 1.0)
    spin.Set(90.0, 10.0)
    variants = ball.GetPrim().GetVariantSets().AddVariantSet("size")
    for name in ("small", "large"):
        variants.AddVariant(name)
    variants.SetVariantSelection("small")
    stack = document.undo_stack

    # An existing default comes back on undo.
    assert document.set_attribute("/World/Group/Ball.radius", 3.0)
    stack.undo()
    assert ball.GetRadiusAttr().Get() == 1.0
    stack.redo()
    assert ball.GetRadiusAttr().Get() == 3.0

    # A time-sampled attribute gets a sample at the current time.
    document.time = 5.0
    assert document.set_attribute(str(spin.GetAttr().GetPath()), 45.0)
    assert list(spin.GetAttr().GetTimeSamples()) == [1.0, 5.0, 10.0]
    stack.undo()
    assert list(spin.GetAttr().GetTimeSamples()) == [1.0, 10.0]

    # In an empty sublayer the edit creates overs; undo removes them all.
    sublayer = Sdf.Layer.CreateAnonymous("edits.usda")
    root.subLayerPaths.append(sublayer.identifier)
    document.set_edit_target(sublayer)
    assert not document.set_attribute("/World/Group/Ball.radius", 5.0)  # root wins
    assert sublayer.GetAttributeAtPath("/World/Group/Ball.radius").default == 5.0
    stack.undo()
    assert sublayer.GetPrimAtPath("/World") is None
    stack.redo()
    assert sublayer.GetPrimAtPath("/World/Group") is not None

    # Variant selections are undoable too.
    document.set_edit_target(root)
    document.set_variant("/World/Group/Ball", "size", "large")
    assert variants.GetVariantSelection() == "large"
    stack.undo()
    assert variants.GetVariantSelection() == "small"
    assert root.GetPrimAtPath("/World/Group/Ball").variantSelections["size"] == "small"

    document.new()
    assert stack.count() == 0
