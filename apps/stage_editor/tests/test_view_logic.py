"""Viewport logic that runs without the imaging build: cameras and pick modes."""

from __future__ import annotations

import pytest
from pxr import Gf, Kind, Usd, UsdGeom

from stage_editor.display import ViewSettings, pick_target
from stage_editor.hydra_view import OrbitCamera, conform_camera


@pytest.mark.parametrize(
    ("z_up", "theta", "phi"),
    [
        (False, -30.0, 20.0),
        (False, 135.0, -60.0),
        (True, 10.0, 45.0),
        (True, -170.0, 5.0),
    ],
)
def test_adopting_a_camera_keeps_the_view(z_up: bool, theta: float, phi: float) -> None:
    source = OrbitCamera(z_up, fov=40.0)
    source.theta, source.phi = theta, phi
    source.center = Gf.Vec3d(1.0, -2.0, 3.0)
    source.dist = 7.5
    camera = source.camera(1.5)

    free = OrbitCamera(z_up)
    free.adopt(camera)

    assert free.theta == pytest.approx(theta)
    assert free.phi == pytest.approx(phi)
    assert free.fov == pytest.approx(40.0)
    assert Gf.IsClose(free.center, source.center, 1e-9)
    adopted = free.camera(1.5).transform
    assert all(
        Gf.IsClose(adopted.GetRow(i), camera.transform.GetRow(i), 1e-9)
        for i in range(4)
    )
    # Looking through a stage camera keeps its vertical aperture at any aspect.
    conformed = conform_camera(camera, 2.0)
    assert conformed.verticalAperture == pytest.approx(camera.verticalAperture)
    assert conformed.aspectRatio == pytest.approx(2.0)


def test_pick_modes_and_settings_round_trip() -> None:
    stage = Usd.Stage.CreateInMemory()
    asset = UsdGeom.Xform.Define(stage, "/Set/Chair").GetPrim()
    Usd.ModelAPI(stage.GetPrimAtPath("/Set")).SetKind(Kind.Tokens.group)
    Usd.ModelAPI(asset).SetKind(Kind.Tokens.component)
    UsdGeom.Mesh.Define(stage, "/Set/Chair/Seat")
    prototype = UsdGeom.Xform.Define(stage, "/Proto").GetPrim()
    UsdGeom.Mesh.Define(stage, "/Proto/Leg")
    instance = stage.DefinePrim("/Set/Table")
    instance.GetReferences().AddInternalReference("/Proto")
    instance.SetInstanceable(True)

    cases = [
        ("/Set/Chair/Seat", "prims", "/Set/Chair/Seat"),
        ("/Set/Chair/Seat", "models", "/Set/Chair"),
        ("/Set/Table/Leg", "instances", "/Set/Table"),
        ("/Set/Chair/Seat", "instances", "/Set/Chair/Seat"),
        ("", "models", ""),
    ]
    for hit, mode, expected in cases:
        assert pick_target(stage, hit, mode) == expected, (hit, mode)
    assert prototype.IsValid()

    settings = ViewSettings(draw_mode="DRAW_WIREFRAME", background=(1.0, 1.0, 1.0, 1.0))
    assert ViewSettings.from_json(settings.to_json()) == settings
    assert ViewSettings.from_json("not json") == ViewSettings()
