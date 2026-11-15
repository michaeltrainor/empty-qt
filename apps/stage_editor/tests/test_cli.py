"""usdview-style command line: parsing, masks, and camera lookup."""

from __future__ import annotations

from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom

from stage_editor.cli import build_parser, resolve_camera, split_paths
from stage_editor.document import StageDocument
from stage_editor.hydra_view import stage_cameras


def test_flags_mask_the_stage_and_find_the_camera(tmp_path: Path) -> None:
    asset = tmp_path / "shot.usda"
    stage = Usd.Stage.CreateNew(str(asset))
    for path in ("/Shot/Set/Table", "/Shot/Set/Chair", "/Shot/Cams/main"):
        stage.DefinePrim(path, "Camera" if path.endswith("main") else "Xform")
    payload = stage.DefinePrim("/Shot/Heavy")
    payload.GetPayloads().AddPayload(Sdf.Payload(str(asset), "/Shot/Set"))
    stage.SetDefaultPrim(stage.GetPrimAtPath("/Shot"))
    stage.GetRootLayer().Save()

    flags = ["--mask", "/Shot/Set/Table, /Shot/Cams", "--unloaded", "--camera", "main"]
    args = build_parser().parse_args(
        [str(asset), *flags, "-c", "high", "--draw-mode", "wireframe"]
    )
    assert args.complexity == "high"
    assert args.draw_mode == "wireframe"

    document = StageDocument()
    document.open(args.file, mask=split_paths(args.mask), load=not args.unloaded)
    composed = document.stage
    assert composed.GetPrimAtPath("/World/shot/Set/Table").IsValid()
    assert not composed.GetPrimAtPath("/World/shot/Set/Chair").IsValid()
    assert document.map_source_path("/Shot/Cams/main") == "/World/shot/Cams/main"
    assert (
        resolve_camera(stage_cameras(composed), args.camera) == "/World/shot/Cams/main"
    )
    assert resolve_camera(stage_cameras(composed), "missing") == ""
    assert UsdGeom.GetStageUpAxis(composed) == UsdGeom.GetStageUpAxis(stage)

    with pytest.raises(ValueError, match="not absolute prim paths"):
        document.open(asset, mask=["C:/Program Files/Git/Shot"])

    unmasked = StageDocument()
    unmasked.open(asset, load=False)
    assert not unmasked.stage.GetPrimAtPath("/World/shot/Heavy").IsLoaded()
