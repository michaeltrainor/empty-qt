"""StageDocument: new, save, reopen, foreign files, and change notices."""

from __future__ import annotations

from pathlib import Path

import pytest
from pxr import Usd, UsdGeom

from stage_editor.document import LAYER_MARKER, StageDocument


def _foreign_stage(path: Path, *, default_prim: bool) -> None:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    cube = UsdGeom.Cube.Define(stage, "/Asset/Cube")
    if default_prim:
        stage.SetDefaultPrim(stage.GetPrimAtPath("/Asset"))
    cube.GetSizeAttr().Set(2.0)
    stage.SetStartTimeCode(101)
    stage.SetEndTimeCode(129)
    stage.SetTimeCodesPerSecond(30)
    stage.GetRootLayer().Save()


def test_save_as_then_open_edits_the_file_in_place(qtbot, tmp_path: Path) -> None:
    document = StageDocument()
    UsdGeom.Sphere.Define(document.stage, "/World/Ball")
    assert document.has_unsaved_changes()

    target = tmp_path / "scene.usda"
    with qtbot.waitSignal(document.stageReplaced):
        document.save_as(target)
    assert document.path == target
    assert not document.has_unsaved_changes()
    assert document.stage.GetPrimAtPath("/World/Ball").IsA(UsdGeom.Sphere)

    reopened = StageDocument()
    assert reopened.open(target) is True
    assert reopened.path == target
    assert LAYER_MARKER in reopened.stage.GetRootLayer().customLayerData
    assert reopened.stage.GetPrimAtPath("/World/Ball").IsA(UsdGeom.Sphere)


@pytest.mark.parametrize("default_prim", [True, False])
def test_foreign_file_is_referenced_into_an_untitled_scene(
    tmp_path: Path, default_prim: bool
) -> None:
    asset = tmp_path / "asset.usda"
    _foreign_stage(asset, default_prim=default_prim)
    document = StageDocument()

    assert document.open(asset) is False
    assert document.path is None
    prim = document.stage.GetPrimAtPath("/World/asset")
    assert prim.IsValid()
    assert prim.GetTypeName() == ""  # an over: the referenced /Asset is untyped
    assert document.stage.GetPrimAtPath("/World/asset/Cube").IsA(UsdGeom.Cube)
    # The referenced animation range becomes stage time.
    assert document.time_range() == (101.0, 129.0)
    assert document.time_codes_per_second() == 30.0
    assert document.time == 101.0
    # Saving writes our own layer, never the asset.
    document.save_as(tmp_path / "scene.usdc")
    assert Usd.Stage.Open(str(asset)).GetPrimAtPath("/World") is None or not (
        Usd.Stage.Open(str(asset)).GetPrimAtPath("/World").IsValid()
    )
    # A second reference of the same file gets a distinct name.
    assert str(document.add_reference(asset)) == "/World/asset_2"


def test_changed_reports_resynced_and_info_paths(qtbot) -> None:
    document = StageDocument()
    with qtbot.waitSignal(document.changed) as blocker:
        cube = UsdGeom.Cube.Define(document.stage, "/World/Box")
    assert "/World/Box" in blocker.args[0]

    with qtbot.waitSignal(document.changed) as blocker:
        cube.GetSizeAttr().Set(3.0)  # authors the attribute: a property resync
    assert blocker.args[0] == ["/World/Box.size"]
    with qtbot.waitSignal(document.changed) as blocker:
        cube.GetSizeAttr().Set(4.0)  # value change on an existing attribute
    assert blocker.args[0] == []
    assert "/World/Box.size" in blocker.args[1]

    with qtbot.waitSignal(document.timeChanged):
        document.time = 12.0
    assert document.time == 12.0
