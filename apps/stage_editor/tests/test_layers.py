"""Layer rows: stack nesting, muting, edit target, and referenced layers."""

from __future__ import annotations

from pathlib import Path

import pytest
from pxr import Sdf, UsdGeom

from stage_editor.document import StageDocument
from stage_editor.layers import layer_rows


def test_layer_rows_follow_muting_and_the_edit_target(tmp_path: Path) -> None:
    lighting = Sdf.Layer.CreateNew(str(tmp_path / "lighting.usda"))
    nested = Sdf.Layer.CreateNew(str(tmp_path / "nested.usda"))
    lighting.subLayerPaths.append(nested.identifier)
    asset = tmp_path / "asset.usda"
    asset_layer = Sdf.Layer.CreateNew(str(asset))
    Sdf.PrimSpec(asset_layer, "Asset", Sdf.SpecifierDef, "Xform")
    asset_layer.defaultPrim = "Asset"
    asset_layer.Save()
    document = StageDocument()
    stage = document.stage
    stage.GetRootLayer().subLayerPaths.append(lighting.identifier)
    document.add_reference(asset)

    def summary() -> list[tuple[str, str, int, bool, bool]]:
        return [
            (r.group, r.name, r.depth, r.muted, r.edit_target)
            for r in layer_rows(document.stage)
        ]

    rows = summary()
    assert rows[1:] == [
        ("root", "untitled.usda", 0, False, True),
        ("root", "lighting.usda", 1, False, False),
        ("root", "nested.usda", 2, False, False),
        ("used", "asset.usda", 0, False, False),
    ]
    assert rows[0][0] == "session"

    document.set_edit_target(lighting)
    UsdGeom.Xform.Define(stage, "/World/Key")
    assert lighting.GetPrimAtPath("/World/Key") is not None
    assert document.has_unsaved_changes()
    rows = {r.name: r for r in layer_rows(stage)}
    assert rows["lighting.usda"].edit_target
    assert rows["lighting.usda"].dirty

    document.set_layer_muted(nested, True)
    assert {r.name: r.muted for r in layer_rows(stage)}["nested.usda"]
    with pytest.raises(ValueError, match="cannot be muted"):
        document.set_layer_muted(stage.GetRootLayer(), True)
    with pytest.raises(ValueError, match="not in the stage's layer stack"):
        document.set_edit_target(asset_layer)
