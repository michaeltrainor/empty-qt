"""Eject a prim to a sidecar layer, reference it back, and merge it back."""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf, Usd, UsdGeom, UsdShade

from stage_editor.eject import (
    eject,
    ejected_info,
    is_shadowed,
    merge_back,
    sidecar_file,
)


def _composed(stage: Usd.Stage, root: str) -> dict[str, object]:
    """Every prim and attribute value under ``root``, for before/after checks."""
    result: dict[str, object] = {}
    for prim in Usd.PrimRange(stage.GetPrimAtPath(root)):
        result[str(prim.GetPath())] = (prim.GetTypeName(), prim.IsActive())
        for attr in prim.GetAttributes():
            if attr.HasAuthoredValue():
                value = attr.Get()
                if isinstance(value, Sdf.AssetPath):
                    value = Path(value.resolvedPath)  # authored text is re-anchored
                result[str(attr.GetPath())] = value
        for rel in prim.GetRelationships():
            result[str(rel.GetPath())] = [str(t) for t in rel.GetForwardedTargets()]
    return result


def _scene(tmp_path: Path) -> tuple[Usd.Stage, Path]:
    textures = tmp_path / "tex"
    textures.mkdir()
    (textures / "wood.png").write_bytes(b"")
    leg = tmp_path / "leg.usda"
    leg_stage = Usd.Stage.CreateNew(str(leg))
    UsdGeom.Cylinder.Define(leg_stage, "/Leg").GetHeightAttr().Set(4.0)
    leg_stage.SetDefaultPrim(leg_stage.GetPrimAtPath("/Leg"))
    leg_stage.GetRootLayer().Save()

    scene = tmp_path / "scene.usda"
    stage = Usd.Stage.CreateNew(str(scene))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    looks = UsdShade.Material.Define(stage, "/World/Looks/Wood")
    chair = UsdGeom.Xform.Define(stage, "/World/Chair")
    chair.AddTranslateOp().Set((1.0, 2.0, 3.0))
    seat = UsdGeom.Cube.Define(stage, "/World/Chair/Seat")
    UsdShade.MaterialBindingAPI.Apply(seat.GetPrim()).Bind(looks)
    seat.GetPrim().CreateAttribute("tex", Sdf.ValueTypeNames.Asset).Set(
        "./tex/wood.png"
    )
    stage.DefinePrim("/World/Chair/Leg").GetReferences().AddReference("./leg.usda")
    variants = chair.GetPrim().GetVariantSets().AddVariantSet("size")
    for name in ("small", "big"):
        variants.AddVariant(name)
    variants.SetVariantSelection("big")
    with variants.GetVariantEditContext():
        seat.GetSizeAttr().Set(3.0)
    stage.GetRootLayer().Save()
    return stage, scene


def test_eject_keeps_the_composed_scene_and_merge_back_restores_it(
    tmp_path: Path,
) -> None:
    stage, scene = _scene(tmp_path)
    before = _composed(stage, "/World")
    root = stage.GetRootLayer()

    file = sidecar_file(scene, "Chair")
    assert file == tmp_path / "scene_assets" / "Chair.usda"
    result = eject(stage, "/World/Chair", file, root)
    assert not result.shadowed
    assert result.warnings == []
    assert _composed(stage, "/World") == before

    sidecar = Sdf.Layer.FindOrOpen(str(file))
    assert ejected_info(sidecar)["path"] == "/World/Chair"
    assert sidecar.defaultPrim == "Chair"
    # Arcs and variants are copied as authored, assets re-anchored.
    text = sidecar.ExportToString()
    assert "@../leg.usda@" in text
    assert "@../tex/wood.png@" in text
    assert 'variantSet "size"' in text
    # The binding points outside the prim, so it stays in the scene layer.
    assert "material:binding" not in text
    seat_over = root.GetPrimAtPath("/World/Chair/Seat")
    assert seat_over.specifier == Sdf.SpecifierOver
    chair_spec = root.GetPrimAtPath("/World/Chair")
    assert [r.assetPath for r in chair_spec.referenceList.prependedItems] == [
        "./scene_assets/Chair.usda"
    ]

    # Editing the sidecar on its own shows up in the scene at once.
    edit = Usd.Stage.Open(sidecar)
    UsdGeom.Sphere.Define(edit, "/Chair/Cushion")
    assert stage.GetPrimAtPath("/World/Chair/Cushion").IsA(UsdGeom.Sphere)
    edit.RemovePrim("/Chair/Cushion")

    merge_back(stage, "/World/Chair", root)
    assert _composed(stage, "/World") == before
    assert not root.GetPrimAtPath("/World/Chair").referenceList.prependedItems


def test_ejecting_referenced_content_is_shadowed(tmp_path: Path) -> None:
    _stage, scene = _scene(tmp_path)
    shot = Usd.Stage.CreateInMemory()
    shot.DefinePrim("/Shot").GetReferences().AddReference(str(scene), "/World")
    before = _composed(shot, "/Shot")
    work = tmp_path / "shot.usda"
    shot.GetRootLayer().Export(str(work))
    shot = Usd.Stage.Open(str(work))

    result = eject(
        shot, "/Shot/Chair", sidecar_file(work, "Chair"), shot.GetRootLayer()
    )
    assert result.shadowed
    assert is_shadowed(Sdf.Layer.FindOrOpen(str(result.file)))
    assert _composed(shot, "/Shot") == before
    # The referenced scene file itself is never changed.
    assert Sdf.Layer.FindOrOpen(str(scene)).GetPrimAtPath("/World/Chair") is not None
