"""SceneWorld mirrors every component onto its live USD stage.

No GPU window is opened. Each test checks the tables and the stage agree
after a mutation, including the live-view writes the window uses.
"""

from __future__ import annotations

import pytest

pxr = pytest.importorskip("pxr")

from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdShade  # noqa: E402

from scene_viewport.animation import AnimationChannel, AnimationClip  # noqa: E402
from scene_viewport.components import (  # noqa: E402
    CameraComponent,
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.usd_prims import (  # noqa: E402
    read_camera,
    read_clip,
    read_light,
    read_mesh,
    read_transform,
)
from scene_viewport.world import SceneWorld  # noqa: E402


def _spin(target: str, name: str = "spin") -> AnimationClip:
    return AnimationClip(
        name=name,
        channels=[
            AnimationChannel(
                transform=target,
                path="rotation",
                times=[0.0, 2.0],
                values=[[0.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0]],
            )
        ],
    )


def _world() -> SceneWorld:
    return SceneWorld(
        transforms=[
            TransformComponent(name="rig", position=(1.0, 2.0, 3.0)),
            TransformComponent(
                name="Wheel.001", parent="rig", rotation=(0.1, 0.2, 0.3)
            ),
            TransformComponent(name="Wheel_001", parent="rig"),  # same identifier
            TransformComponent(name="lamp"),
            TransformComponent(name="cam"),
        ],
        meshes=[MeshComponent(name="wheelShape", transform="Wheel.001", kind="box")],
        lights=[LightComponent(name="lampShape", transform="lamp", kind="point")],
        cameras=[CameraComponent(name="camShape", transform="cam", fov=40.0)],
        animations=[_spin("Wheel.001")],
    )


def _prim(world: SceneWorld, name: str) -> Usd.Prim:
    path = world.prim_path(name)
    assert path is not None, name
    prim = world.stage.GetPrimAtPath(path)
    assert prim.IsValid(), path
    return prim


def _names(world: SceneWorld) -> dict[Sdf.Path, str]:
    return {world.prim_path(n): n for n in world.transforms}  # type: ignore[misc]


def test_components_become_prims_under_world() -> None:
    world = _world()
    assert world.stage.GetDefaultPrim().GetPath() == Sdf.Path("/World")
    assert world.prim_path("rig") == Sdf.Path("/World/rig")
    assert world.prim_path("Wheel.001") == Sdf.Path("/World/rig/Wheel_001")
    # The sibling whose name already is that identifier got a suffix.
    assert world.prim_path("Wheel_001") == Sdf.Path("/World/rig/Wheel_001_1")
    assert world.prim_path("wheelShape") == Sdf.Path("/World/rig/Wheel_001/wheelShape")
    assert _prim(world, "Wheel.001").IsA(UsdGeom.Xform)
    assert _prim(world, "wheelShape").IsA(UsdGeom.Cube)
    assert _prim(world, "lampShape").IsA(UsdLux.SphereLight)
    assert _prim(world, "camShape").IsA(UsdGeom.Camera)
    assert world.stage.GetPrimAtPath("/World/Looks/wheelShape_Material").IsA(
        UsdShade.Material
    )
    # Reading the prims back gives the stored components.
    for name, stored in world.transforms.items():
        assert read_transform(_prim(world, name), name, stored.parent) == stored
    assert (
        read_mesh(_prim(world, "wheelShape"), "Wheel.001", None)
        == world.meshes["wheelShape"]
    )
    assert (
        read_light(_prim(world, "lampShape"), "lampShape", "lamp")
        == world.lights["lampShape"]
    )
    assert (
        read_camera(_prim(world, "camShape"), "camShape", "cam")
        == world.cameras["camShape"]
    )


def test_clips_are_muted_sublayers_that_read_back() -> None:
    world = _world()
    layer = world.clip_layer("spin")
    assert layer is not None
    assert layer.identifier in world.stage.GetRootLayer().subLayerPaths
    assert world.stage.IsLayerMuted(layer.identifier)
    assert read_clip(layer, _names(world)) == world.animations["spin"]
    assert world.stage.GetEndTimeCode() == 120.0
    # The composed stage shows the rest pose while the clip is muted.
    xform = UsdGeom.Xformable(_prim(world, "Wheel.001"))
    assert not xform.GetOrderedXformOps()[2].GetAttr().GetNumTimeSamples()
    world.remove_animation("spin")
    assert list(world.stage.GetRootLayer().subLayerPaths) == []
    assert world.stage.GetEndTimeCode() == 0.0


def test_live_view_writes_author_the_stage() -> None:
    world = _world()
    moved = world.transforms["rig"].model_copy(update={"position": (9.0, 0.0, 0.0)})
    world.transforms["rig"] = moved
    assert read_transform(_prim(world, "rig"), "rig", "").position == (9.0, 0.0, 0.0)
    world.meshes["wheelShape"] = world.meshes["wheelShape"].model_copy(
        update={"color": "#ff0000", "alpha_mode": "mask", "alpha_cutoff": 0.3}
    )
    mesh = read_mesh(_prim(world, "wheelShape"), "Wheel.001", None)
    assert mesh is not None
    assert (mesh.color, mesh.alpha_mode, mesh.alpha_cutoff) == ("#ff0000", "mask", 0.3)
    world.meshes["wheelShape"] = mesh.model_copy(update={"alpha_mode": "opaque"})
    shader = UsdShade.Shader(
        world.stage.GetPrimAtPath("/World/Looks/wheelShape_Material/Surface")
    )
    assert not shader.GetInput("opacityThreshold")
    world.lights["lampShape"] = world.lights["lampShape"].model_copy(
        update={"intensity": 3.0}
    )
    assert read_light(_prim(world, "lampShape"), "lampShape", "lamp").intensity == 3.0
    world.cameras["camShape"] = world.cameras["camShape"].model_copy(
        update={"fov": 70.0}
    )
    assert read_camera(_prim(world, "camShape"), "camShape", "cam").fov == 70.0
    # Adding through the view and deleting through it work too.
    world.transforms["extra"] = TransformComponent(name="extra", parent="rig")
    assert world.prim_path("extra") == Sdf.Path("/World/rig/extra")
    del world.transforms["extra"]
    assert world.prim_path("extra") is None
    assert not world.stage.GetPrimAtPath("/World/rig/extra").IsValid()
    with pytest.raises(KeyError, match="does not match"):
        world.transforms["wrong"] = TransformComponent(name="right")
    assert world.animations == {"spin": world.animations["spin"]}


def test_rename_moves_the_subtree_and_retargets_clips() -> None:
    world = _world()
    world.rename_transform("rig", "chassis")
    assert world.prim_path("chassis") == Sdf.Path("/World/chassis")
    assert world.prim_path("Wheel.001") == Sdf.Path("/World/chassis/Wheel_001")
    assert world.prim_path("wheelShape") == Sdf.Path(
        "/World/chassis/Wheel_001/wheelShape"
    )
    assert not world.stage.GetPrimAtPath("/World/rig").IsValid()
    assert _prim(world, "Wheel.001").IsA(UsdGeom.Xform)
    assert world.transforms["Wheel.001"].parent == "chassis"
    layer = world.clip_layer("spin")
    assert layer is not None
    assert layer.GetPrimAtPath("/World/chassis/Wheel_001") is not None
    assert layer.GetPrimAtPath("/World/rig/Wheel_001") is None
    # Renaming the animated node itself retargets the clip and its layer.
    world.rename_transform("Wheel.001", "front")
    assert world.prim_path("front") == Sdf.Path("/World/chassis/front")
    assert world.animations["spin"].channels[0].transform == "front"
    assert layer.GetPrimAtPath("/World/chassis/front") is not None
    assert read_clip(layer, _names(world)) == world.animations["spin"]
    assert _prim(world, "front").GetCustomDataByKey("mtqt:name") is None
    assert _prim(world, "Wheel_001").GetCustomDataByKey("mtqt:name") == "Wheel_001"


def test_reparent_moves_the_prim_and_rejects_cycles() -> None:
    world = _world()
    wheel = world.transforms["Wheel.001"]
    world.set_transform(wheel.model_copy(update={"parent": "lamp"}))
    assert world.prim_path("Wheel.001") == Sdf.Path("/World/lamp/Wheel_001")
    assert world.prim_path("wheelShape") == Sdf.Path("/World/lamp/Wheel_001/wheelShape")
    layer = world.clip_layer("spin")
    assert layer is not None
    assert layer.GetPrimAtPath("/World/lamp/Wheel_001") is not None
    world.set_transform(world.transforms["Wheel.001"].model_copy(update={"parent": ""}))
    assert world.prim_path("Wheel.001") == Sdf.Path("/World/Wheel_001")
    with pytest.raises(ValueError, match="under itself"):
        world.set_transform(
            world.transforms["rig"].model_copy(update={"parent": "Wheel_001"})
        )
    # "Wheel_001" is a child of rig, so that would be a cycle; a sibling is fine.
    world.set_transform(world.transforms["rig"].model_copy(update={"parent": "lamp"}))
    # The sibling's suffix stays with it when its parent moves.
    assert world.prim_path("Wheel_001") == Sdf.Path("/World/lamp/rig/Wheel_001_1")


def test_remove_drops_prims_materials_and_channels() -> None:
    world = _world()
    world.remove_mesh("wheelShape")
    assert not world.stage.GetPrimAtPath("/World/Looks/wheelShape_Material").IsValid()
    world.remove_transform("Wheel.001")
    assert not world.stage.GetPrimAtPath("/World/rig/Wheel_001").IsValid()
    assert world.animations == {}
    assert list(world.stage.GetRootLayer().subLayerPaths) == []
    # Removing a parent lifts its remaining children under the grandparent.
    world.add_transform(TransformComponent(name="child", parent="Wheel_001"))
    world.remove_transform("Wheel_001")
    assert world.transforms["child"].parent == "rig"
    assert world.prim_path("child") == Sdf.Path("/World/rig/child")
    assert _prim(world, "child").IsA(UsdGeom.Xform)


def test_reorder_clear_and_replace_all() -> None:
    world = _world()
    world.reorder_transforms(["cam", "rig"])
    assert list(world.transforms) == ["cam", "rig", "Wheel.001", "Wheel_001", "lamp"]
    assert _prim(world, "cam").GetCustomDataByKey("mtqt:index") == 0
    assert _prim(world, "lamp").GetCustomDataByKey("mtqt:index") == 4
    document = world.to_document()
    world.clear()
    assert len(list(world.stage.GetPrimAtPath("/World").GetChildren())) == 0
    assert world.transforms == {}
    assert list(world.stage.GetRootLayer().subLayerPaths) == []
    world.replace_all(
        document.transforms,
        document.meshes,
        document.lights,
        document.cameras,
        document.animations,
    )
    assert world.to_document() == document
    assert world.prim_path("Wheel.001") == Sdf.Path("/World/rig/Wheel_001")
    assert world.stage.GetEndTimeCode() == 120.0


def test_dangling_parent_goes_under_world() -> None:
    world = SceneWorld(transforms=[TransformComponent(name="orphan", parent="ghost")])
    assert world.prim_path("orphan") == Sdf.Path("/World/orphan")
    assert world.transforms["orphan"].parent == "ghost"
    world.add_transform(TransformComponent(name="Looks"))
    assert world.prim_path("Looks") == Sdf.Path("/World/Looks_1")
