"""Inspector rows: value sources, metadata, prim stack, and composition arcs."""

from __future__ import annotations

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from stage_editor.inspection import (
    composition_rows,
    format_value,
    metadata_rows,
    prim_stack_rows,
    property_rows,
    property_value,
    spec_text,
    variant_rows,
)


def _stage() -> Usd.Stage:
    stage = Usd.Stage.CreateInMemory()
    asset = UsdGeom.Mesh.Define(stage, "/Asset")
    asset.GetPointsAttr().Set(Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (0, 1, 0)] * 4))
    world = UsdGeom.Xform.Define(stage, "/World")
    world.AddTranslateOp().Set(Gf.Vec3d(0, 1, 0), 1.0)
    world.GetPrim().CreateRelationship("lookAt").AddTarget("/Asset")
    chair = stage.DefinePrim("/World/Chair")
    chair.GetReferences().AddInternalReference("/Asset")
    variants = chair.GetVariantSets().AddVariantSet("look")
    for name in ("red", "blue"):
        variants.AddVariant(name)
    variants.SetVariantSelection("red")
    return stage


def test_property_values_report_their_source() -> None:
    stage = _stage()
    world = stage.GetPrimAtPath("/World")
    chair = stage.GetPrimAtPath("/World/Chair")
    cases = [
        (world, "xformOp:translate", ("(0, 1, 0)", "time samples")),
        (world, "xformOpOrder", ("[1] [xformOp:translate]", "default")),
        (world, "visibility", ("inherited", "fallback")),
        (world, "lookAt", ("/Asset", "targets")),
        (world, "proxyPrim", ("", "none")),
        (chair, "doubleSided", ("false", "fallback")),
        (
            chair,
            "World Transform",
            (format_value(Gf.Matrix4d(1).SetTranslate((0, 1, 0))), "computed"),
        ),
    ]
    for prim, name, expected in cases:
        rows = {row.name: row for row in property_rows(prim)}
        assert property_value(prim, rows[name], 1.0) == expected, name
    points = {row.name: row for row in property_rows(chair)}["points"]
    value, source = property_value(chair, points, 1.0)
    assert value.startswith("[12] [(0, 0, 0), (1, 0, 0)")
    assert value.endswith(", ...]")
    assert source == "default"


def test_metadata_stack_and_composition_rows() -> None:
    stage = _stage()
    chair = stage.GetPrimAtPath("/World/Chair")

    metadata = dict(metadata_rows(chair))
    assert metadata["specifier"] == "def"
    assert metadata["typeName"] == "Mesh"
    assert variant_rows(chair) == [("look", ["blue", "red"], "red")]

    stack = prim_stack_rows(chair)
    assert [(r.path, r.specifier) for r in stack] == [
        ("/World/Chair", "def"),
        ("/World/Chair{look=red}", "over"),
        ("/Asset", "def"),
    ]
    text = spec_text(stack[0].identifier, "/World/Chair")
    assert 'def "Chair"' in text
    assert "references = </Asset>" in text

    arcs = [(a.arc, a.target_path) for a in composition_rows(chair)]
    assert arcs == [
        ("root", "/World/Chair"),
        ("variant", "/World/Chair{look=red}"),
        ("reference", "/Asset"),
    ]
    assert Sdf.Path(stack[2].path) == Sdf.Path("/Asset")
