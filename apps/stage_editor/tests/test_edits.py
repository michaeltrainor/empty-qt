"""Structural edits: create, delete, rename, reparent, duplicate, materials."""

from __future__ import annotations

from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom, UsdShade

from stage_editor.document import StageDocument
from stage_editor.edits import (
    MTLX_SURFACE_ID,
    bind_material,
    create_material,
    create_prim,
    delete_prims,
    duplicate_prims,
    rename_prim,
    reparent_prims,
)
from stage_editor.eject import eject, sidecar_file


def _layer_text(document: StageDocument) -> str:
    return document.stage.GetRootLayer().ExportToString()


def test_edits_apply_and_undo_exactly(tmp_path: Path) -> None:
    document = StageDocument()
    stage = document.stage
    stack = document.undo_stack
    world = Sdf.Path("/World")
    start = _layer_text(document)

    box = create_prim(document, world, "Cube")
    light = create_prim(document, world, "SphereLight")
    assert stage.GetPrimAtPath(box).IsA(UsdGeom.Cube)
    assert stage.GetPrimAtPath(light).GetAttribute("inputs:intensity").Get() == 30.0
    material = create_material(document)
    assert str(material) == "/World/Looks/Material"
    shader = UsdShade.Shader(stage.GetPrimAtPath(material.AppendChild("Surface")))
    assert shader.GetShaderId() == MTLX_SURFACE_ID
    surface = UsdShade.Material(stage.GetPrimAtPath(material)).GetSurfaceOutput("mtlx")
    assert surface.HasConnectedSource()
    bind_material(document, [str(box)], str(material))
    bound, _rel = UsdShade.MaterialBindingAPI(
        stage.GetPrimAtPath(box)
    ).ComputeBoundMaterial()
    assert bound.GetPath() == material

    group = create_prim(document, world, "Xform")
    reparent_prims(document, [str(box)], str(group))
    moved = group.AppendChild("Cube")
    renamed = rename_prim(document, str(moved), "Crate")
    crate = stage.GetPrimAtPath(renamed)
    # The binding target followed nothing (material did not move), and the
    # prim kept its binding through both moves.
    assert (
        UsdShade.MaterialBindingAPI(crate).GetDirectBinding().GetMaterialPath()
        == material
    )
    copies = duplicate_prims(document, [renamed])
    assert copies == ["/World/Xform/Crate2"]
    with pytest.raises(ValueError, match="already exists"):
        rename_prim(document, copies[0], "Crate")
    with pytest.raises(ValueError, match="not a valid"):
        rename_prim(document, copies[0], "1 bad")
    assert delete_prims(document, [renamed, str(group)]) == []
    assert not stage.GetPrimAtPath(group)

    while stack.canUndo():
        stack.undo()
    assert _layer_text(document) == start
    while stack.canRedo():
        stack.redo()
    assert not stage.GetPrimAtPath(group)
    assert stage.GetPrimAtPath(light)


def test_referenced_prims_deactivate_and_duplicate_by_reference(tmp_path: Path) -> None:
    asset = tmp_path / "asset.usda"
    source = Usd.Stage.CreateNew(str(asset))
    UsdGeom.Xform.Define(source, "/Set")
    UsdGeom.Cube.Define(source, "/Set/Box")
    source.SetDefaultPrim(source.GetPrimAtPath("/Set"))
    source.GetRootLayer().Save()
    document = StageDocument()
    document.add_reference(asset)
    stage = document.stage

    assert delete_prims(document, ["/World/asset/Box"]) == ["/World/asset/Box"]
    assert not stage.GetPrimAtPath("/World/asset/Box").IsActive()
    document.undo_stack.undo()
    copies = duplicate_prims(document, ["/World/asset/Box"])
    assert stage.GetPrimAtPath(copies[0]).IsA(UsdGeom.Cube)

    # In a shadowed sidecar, delete always deactivates.
    scene = tmp_path / "scene.usda"
    document.save_as(scene)
    stage = document.stage
    result = eject(
        stage, "/World/asset/Box", sidecar_file(scene, "Box"), stage.GetRootLayer()
    )
    assert result.shadowed
    edit = StageDocument()
    edit.open(result.file)
    UsdGeom.Sphere.Define(edit.stage, "/Box/Ball")
    assert delete_prims(edit, ["/Box/Ball"]) == ["/Box/Ball"]
    assert not document.stage.GetPrimAtPath("/World/asset/Box/Ball").IsActive()
