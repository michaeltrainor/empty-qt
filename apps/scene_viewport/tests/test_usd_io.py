"""USD scene files: every component round-trips, other tools' stages open.

No GPU window is opened. The USD stage is written and read with usd-core.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pxr = pytest.importorskip("pxr")
trimesh = pytest.importorskip("trimesh")

from pxr import Sdf, Usd, UsdGeom, UsdLux  # noqa: E402

from scene_viewport.animation import AnimationChannel, AnimationClip  # noqa: E402
from scene_viewport.animation_player import ClipSampler  # noqa: E402
from scene_viewport.components import (  # noqa: E402
    CameraComponent,
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.scene_io import (  # noqa: E402
    SCENE_SUFFIXES,
    SceneDocument,
    is_scene_file,
    load_scene,
    save_scene,
)
from scene_viewport.usd_io import (  # noqa: E402
    USD_SUFFIXES,
    is_usd_file,
    load_usd_scene,
    save_usd_scene,
)
from scene_viewport.window import startup_paths  # noqa: E402
from scene_viewport.xform_math import mat_from_trs  # noqa: E402


def _model(path: Path) -> Path:
    scene = trimesh.Scene()
    scene.add_geometry(
        trimesh.creation.box((1.0, 2.0, 1.0)), node_name="body", geom_name="body"
    )
    path.write_bytes(scene.export(file_type="glb"))
    return path


def _document(tmp_path: Path) -> SceneDocument:
    model = (
        _model(tmp_path / "models" / "car.glb")
        if (tmp_path / "models").mkdir() is None
        else None
    )
    assert model is not None
    return SceneDocument(
        transforms=[
            TransformComponent(name="key", position=(4.0, 8.0, 3.0)),
            TransformComponent(
                name="cube",
                position=(1.0, 0.5, 0.0),
                rotation=(0.1, 0.7, 0.2),
                pivot=(0.5, 0.0, 0.0),
                space="local",
            ),
            TransformComponent(
                name="Wheel.001",
                parent="cube",
                position=(0.0, 1.0, 0.0),
                scale=(2.0, 1.0, 1.0),
                visible=False,
            ),
            TransformComponent(name="ball"),
            TransformComponent(name="floor"),
            TransformComponent(name="car"),
            TransformComponent(name="lamp", position=(0.0, 3.0, 0.0)),
            TransformComponent(name="spot", position=(2.0, 3.0, 0.0)),
            TransformComponent(name="cam", position=(0.0, 2.0, 8.0)),
            TransformComponent(name="ortho"),
        ],
        meshes=[
            MeshComponent(
                name="cubeShape",
                transform="cube",
                kind="box",
                color="#cc3333",
                alpha_mode="mask",
                alpha_cutoff=0.3,
                unlit=True,
                cast_shadow=False,
            ),
            MeshComponent(
                name="ballShape",
                transform="ball",
                kind="sphere",
                roughness=0.1,
                metalness=0.9,
            ),
            MeshComponent(
                name="floorShape", transform="floor", kind="plane", double_sided=False
            ),
            MeshComponent(
                name="carShape",
                transform="car",
                kind="file",
                source=model,
                source_mesh="body",
                opacity=0.5,
                alpha_mode="blend",
                albedo_map=Path("paint.png"),
            ),
        ],
        lights=[
            LightComponent(name="keyShape", transform="key"),
            LightComponent(
                name="lampShape",
                transform="lamp",
                kind="point",
                distance=10.0,
                decay=1.5,
            ),
            LightComponent(
                name="spotShape",
                transform="spot",
                kind="spot",
                color="#ff8800",
                cast_shadow=False,
            ),
        ],
        cameras=[
            CameraComponent(
                name="camShape", transform="cam", fov=35.0, near=0.5, far=200.0
            ),
            CameraComponent(
                name="orthoShape",
                transform="ortho",
                kind="orthographic",
                ortho_size=4.0,
            ),
        ],
        animations=[
            AnimationClip(
                name="Spin Up",
                channels=[
                    AnimationChannel(
                        transform="cube",
                        path="rotation",
                        times=[0.0, 2.0],
                        values=[[0.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0]],
                    ),
                    AnimationChannel(
                        transform="ball",
                        path="translation",
                        interpolation="step",
                        times=[0.0, 1.0, 2.0],
                        values=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                    ),
                    AnimationChannel(
                        transform="Wheel.001",
                        path="scale",
                        interpolation="cubicspline",
                        times=[0.0, 1.0],
                        values=[
                            [0.0] * 3,
                            [1.0] * 3,
                            [0.0] * 3,
                            [0.0] * 3,
                            [2.0] * 3,
                            [0.0] * 3,
                        ],
                    ),
                ],
            ),
            AnimationClip(
                name="slide",
                channels=[
                    AnimationChannel(
                        transform="car",
                        path="translation",
                        times=[0.0, 0.5],
                        values=[[0.0, 0.0, 0.0], [0.0, 0.0, 3.0]],
                    )
                ],
            ),
        ],
    )


def _same_pose(a: TransformComponent, b: TransformComponent) -> bool:
    ma = mat_from_trs(a.position, a.rotation, a.scale)
    mb = mat_from_trs(b.position, b.rotation, b.scale)
    return bool(abs(ma - mb).max() < 1e-9)


@pytest.mark.parametrize("suffix", [".usda", ".usdc"])
def test_usd_round_trip_keeps_every_component(tmp_path: Path, suffix: str) -> None:
    document = _document(tmp_path)
    path = save_scene(document, tmp_path / "scenes" / f"scene{suffix}")
    assert path.suffix == suffix
    assert (tmp_path / "scenes" / "scene_clips" / f"Spin_Up{suffix}").is_file()
    loaded = load_scene(path)
    by_name = {t.name: t for t in loaded.transforms}
    assert set(by_name) == {t.name for t in document.transforms}
    for original in document.transforms:
        back = by_name[original.name]
        assert _same_pose(original, back), original.name
        assert back.parent == original.parent
        assert back.pivot == pytest.approx(original.pivot)
        assert back.visible is original.visible
        assert back.space == original.space
    # Shapes compare field by field; the file mesh source came back absolute.
    assert loaded.meshes == document.meshes
    assert loaded.lights == document.lights
    assert [(c.name, c.kind, c.near, c.far) for c in loaded.cameras] == [
        (c.name, c.kind, c.near, c.far) for c in document.cameras
    ]
    assert loaded.cameras[0].fov == pytest.approx(35.0)
    assert loaded.cameras[1].ortho_size == pytest.approx(4.0)
    # Clips: linear keys exact, STEP and CUBICSPLINE kept from the sidecar.
    assert [c.name for c in loaded.animations] == ["Spin Up", "slide"]
    spin = loaded.animations[0]
    assert {(c.transform, c.path, c.interpolation) for c in spin.channels} == {
        ("cube", "rotation", "linear"),
        ("ball", "translation", "step"),
        ("Wheel.001", "scale", "cubicspline"),
    }
    original_sampler = ClipSampler(document.animations[0])
    loaded_sampler = ClipSampler(spin)
    for i in range(9):
        t = 2.0 * i / 8 + 0.013
        for target, trs in original_sampler.sample(t).items():
            assert loaded_sampler.sample(t)[target] == trs
    assert loaded.animations[1].channels[0].times == pytest.approx([0.0, 0.5])
    # Everything, including the Euler spelling and channel order, is identical.
    assert loaded.transforms == document.transforms
    assert loaded.animations == document.animations


def test_usd_stage_uses_standard_schemas(tmp_path: Path) -> None:
    document = _document(tmp_path)
    path = save_usd_scene(document, tmp_path / "scene.usda")
    stage = Usd.Stage.Open(str(path))
    assert stage.GetDefaultPrim().GetPath() == Sdf.Path("/World")
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y
    cube = stage.GetPrimAtPath("/World/cube")
    assert cube.IsA(UsdGeom.Xform)
    ops = UsdGeom.Xformable(cube).GetXformOpOrderAttr().Get()
    assert list(ops) == [
        "xformOp:translate",
        "xformOp:translate:pivot",
        "xformOp:orient",
        "xformOp:scale",
        "!invert!xformOp:translate:pivot",
    ]
    # Invalid identifiers are renamed; the original name is kept.
    wheel = stage.GetPrimAtPath("/World/cube/Wheel_001")
    assert wheel.GetCustomDataByKey("mtqt:name") == "Wheel.001"
    assert (
        UsdGeom.Imageable(wheel).GetVisibilityAttr().Get() == UsdGeom.Tokens.invisible
    )
    assert stage.GetPrimAtPath("/World/cube/cubeShape").IsA(UsdGeom.Cube)
    assert stage.GetPrimAtPath("/World/ball/ballShape").IsA(UsdGeom.Sphere)
    assert stage.GetPrimAtPath("/World/floor/floorShape").IsA(UsdGeom.Plane)
    car = UsdGeom.Mesh(stage.GetPrimAtPath("/World/car/carShape"))
    assert len(car.GetPointsAttr().Get()) == 8
    assert car.GetPrim().GetAttribute("mtqt:source").Get().path == "models/car.glb"
    assert stage.GetPrimAtPath("/World/key/keyShape").IsA(UsdLux.DistantLight)
    assert stage.GetPrimAtPath("/World/spot/spotShape").HasAPI(UsdLux.ShapingAPI)
    assert stage.GetPrimAtPath("/World/cam/camShape").IsA(UsdGeom.Camera)
    assert stage.GetPrimAtPath("/World/Looks/cubeShape_Material")
    assert [Path(p).name for p in stage.GetRootLayer().subLayerPaths] == [
        "Spin_Up.usda",
        "slide.usda",
    ]
    assert stage.GetEndTimeCode() == 120.0
    # The baked STEP channel is plain time samples for other tools.
    clip = Sdf.Layer.FindOrOpen(str(tmp_path / "scene_clips" / "Spin_Up.usda"))
    attr = clip.GetAttributeAtPath("/World/ball.xformOp:translate")
    assert len(clip.ListTimeSamplesForPath(attr.path)) == 121
    assert clip.customLayerData["mtqt:clip"] == "Spin Up"


def test_foreign_stage_opens_with_xforms_primitives_lights_and_cameras(
    tmp_path: Path,
) -> None:
    path = tmp_path / "other.usda"
    stage = Usd.Stage.CreateNew(str(path))
    world = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(world.GetPrim())
    group = UsdGeom.Xform.Define(stage, "/Root/group")
    UsdGeom.XformCommonAPI(group).SetTranslate((1.0, 2.0, 3.0))
    UsdGeom.XformCommonAPI(group).SetRotate((0.0, 90.0, 0.0))
    UsdGeom.Cube.Define(stage, "/Root/group/box")
    UsdGeom.Mesh.Define(stage, "/Root/group/raw")  # no model file: skipped
    light = UsdLux.SphereLight.Define(stage, "/Root/group/light")
    light.GetIntensityAttr().Set(500.0)
    UsdGeom.Camera.Define(stage, "/Root/group/cam")
    UsdGeom.Camera.Define(stage, "/Root/rootcam")  # no transform parent: dropped
    stage.GetRootLayer().Save()

    loaded = load_usd_scene(path)
    assert [t.name for t in loaded.transforms] == ["group"]
    group_back = loaded.transforms[0]
    assert group_back.position == pytest.approx((1.0, 2.0, 3.0))
    assert group_back.rotation == pytest.approx((0.0, math.pi / 2, 0.0))
    assert [(m.name, m.kind) for m in loaded.meshes] == [("box", "box")]
    assert [(light.name, light.kind, light.intensity) for light in loaded.lights] == [
        ("light", "point", 20.0)
    ]
    assert [c.name for c in loaded.cameras] == ["cam"]
    assert loaded.animations == []


def test_usd_errors_and_suffix_helpers(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_usd_scene(tmp_path / "missing.usda")
    bad = tmp_path / "bad.usda"
    bad.write_text("this is not usd", encoding="utf-8")
    with pytest.raises(ValueError, match="not a USD file"):
        load_scene(bad)
    assert USD_SUFFIXES == (".usda", ".usdc", ".usd")
    assert set(USD_SUFFIXES) < set(SCENE_SUFFIXES)
    assert is_usd_file(Path("x.USDC"))
    assert not is_usd_file(Path("x.json"))
    assert is_scene_file(Path("x.json"))
    assert is_scene_file(Path("x.usd"))
    assert not is_scene_file(Path("x.glb"))
    assert [p.name for p in startup_paths(["a.usda", "b.json", "c.glb", "d.txt"])] == [
        "a.usda",
        "b.json",
        "c.glb",
    ]
