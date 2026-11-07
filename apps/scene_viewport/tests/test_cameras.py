"""Camera shapes: store, outliner, documents, look-through, import, export.

The GPU window is never opened. Viewport look-through runs against a
``SimpleNamespace`` stand-in holding real pygfx cameras.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pygfx = pytest.importorskip("pygfx")
trimesh = pytest.importorskip("trimesh")

from scene_viewport.components import (  # noqa: E402
    CameraComponent,
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.export_gltf import export_scene  # noqa: E402
from scene_viewport.mesh.importer import load_model  # noqa: E402
from scene_viewport.model_import import import_objects  # noqa: E402
from scene_viewport.scene import FOLDER_NAMES, build_forest  # noqa: E402
from scene_viewport.scene_io import SceneDocument, load_scene, save_scene  # noqa: E402
from scene_viewport.viewport import ViewportController, _frustum_lines  # noqa: E402
from scene_viewport.window import SceneViewportWindow  # noqa: E402
from scene_viewport.world import SceneWorld  # noqa: E402
from scene_viewport.xform_math import look_at_matrix, look_at_trs, mat_from_trs  # noqa: E402


def _world() -> SceneWorld:
    return SceneWorld(
        transforms=[
            TransformComponent(name="cube"),
            TransformComponent(name="key", position=(4.0, 8.0, 3.0)),
            TransformComponent(name="cam", position=(0.0, 2.0, 8.0)),
            TransformComponent(name="rig"),
            TransformComponent(name="ortho", parent="rig"),
        ],
        meshes=[MeshComponent(name="cubeShape", transform="cube", kind="box")],
        lights=[LightComponent(name="keyShape", transform="key")],
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
    )


def test_world_indexes_cameras_like_lights() -> None:
    world = _world()
    assert world.cameras_on("cam") == ["camShape"]
    assert world.resolve_transform("camShape") == "cam"
    assert "camShape" in world.used_names()
    world.set_camera(world.cameras["camShape"].model_copy(update={"fov": 60.0}))
    assert world.cameras["camShape"].fov == 60.0
    world.rename_transform("cam", "hero")
    assert world.cameras["camShape"].transform == "hero"
    assert world.cameras_on("hero") == ["camShape"]
    assert world.resolve_transform("camShape") == "hero"
    world.remove_transform("hero")
    assert "camShape" not in world.cameras
    assert world.remove_camera("camShape") is None
    with pytest.raises(KeyError):
        world.add_camera(CameraComponent(name="x", transform="missing"))
    document = world.to_document()
    assert [c.name for c in document.cameras] == ["orthoShape"]
    world.clear()
    assert world.cameras == {}


def test_forest_puts_camera_transforms_under_cam() -> None:
    world = _world()
    forest = build_forest(
        world.iter_transforms(),
        world.iter_meshes(),
        world.iter_lights(),
        world.iter_cameras(),
    )
    folders = {folder[0]: folder[1:] for folder in forest[0][1:]}
    assert set(folders) == {"geo", "lgt", "cam"}
    assert "cam" in FOLDER_NAMES
    # Folders go by the root transform's own shapes, as for lights.
    assert folders["cam"] == [["cam", ["camShape"]]]
    assert folders["lgt"] == [["key", ["keyShape"]]]
    assert folders["geo"] == [
        ["cube", ["cubeShape"]],
        ["rig", ["ortho", ["orthoShape"]]],
    ]


def test_document_round_trips_cameras_and_loads_old_files(tmp_path: Path) -> None:
    world = _world()
    path = tmp_path / "scene.json"
    save_scene(world.to_document(), path)
    loaded = load_scene(path)
    assert [c.name for c in loaded.cameras] == ["camShape", "orthoShape"]
    assert loaded.cameras[1].kind == "orthographic"
    # A pre-camera scene file has no cameras key.
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["cameras"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_scene(path).cameras == []
    assert SceneDocument().cameras == []


def test_look_at_points_minus_z_at_the_target() -> None:
    matrix = look_at_matrix((0.0, 0.0, 5.0), (0.0, 0.0, 0.0))
    assert np.allclose(matrix[:3, 2], [0.0, 0.0, 1.0])  # -Z toward the origin
    assert np.allclose(matrix[:3, 3], [0.0, 0.0, 5.0])
    position, rotation, scale = look_at_trs((5.5, 4.0, 6.5), (0.0, 0.4, 0.0))
    assert np.allclose(scale, [1.0, 1.0, 1.0])
    rebuilt = mat_from_trs(position, rotation, scale)
    forward = -rebuilt[:3, 2]
    expected = np.array([0.0, 0.4, 0.0]) - np.array([5.5, 4.0, 6.5])
    assert np.allclose(forward, expected / np.linalg.norm(expected), atol=1e-6)
    # Looking straight down still produces a valid frame.
    down = look_at_matrix((0.0, 5.0, 0.0), (0.0, 0.0, 0.0))
    assert np.isfinite(down).all()
    assert np.allclose(np.linalg.det(down[:3, :3]), 1.0)


def test_frustum_lines_follow_the_lens() -> None:
    narrow = _frustum_lines(CameraComponent(name="c", transform="t", fov=20.0))
    wide = _frustum_lines(CameraComponent(name="c", transform="t", fov=90.0))
    assert narrow.shape[1] == 3
    assert narrow.shape[0] % 2 == 0
    assert np.abs(wide[:, 0]).max() > np.abs(narrow[:, 0]).max()
    ortho = _frustum_lines(
        CameraComponent(name="c", transform="t", kind="orthographic", ortho_size=2.0)
    )
    assert np.isclose(np.abs(ortho[:, 1]).max(), 1.6)  # half size 1, up mark at 1.6


def _viewport_stand_in() -> SimpleNamespace:
    free = pygfx.PerspectiveCamera(50, 16 / 9)
    scene_camera = pygfx.PerspectiveCamera(35, 16 / 9)
    helper = pygfx.Line(
        pygfx.Geometry(positions=np.zeros((2, 3), dtype=np.float32)),
        pygfx.LineSegmentMaterial(),
    )
    drawn: list[bool] = []
    ns = SimpleNamespace(
        camera=free,
        _free_camera=free,
        _cameras={"camShape": scene_camera},
        _camera_helpers={"camShape": helper},
        _camera_controller=SimpleNamespace(enabled=True),
        _gizmo=SimpleNamespace(visible=True),
        _selected={"cam"},
        _look_through=None,
        _canvas=SimpleNamespace(request_draw=lambda: drawn.append(True)),
    )
    ns.look_through = lambda name: ViewportController.look_through(ns, name)  # type: ignore[arg-type]
    return ns


def test_look_through_swaps_camera_and_disables_navigation() -> None:
    ns = _viewport_stand_in()
    assert not ns.look_through("missing")
    assert ns.look_through("camShape")
    assert ns.camera is ns._cameras["camShape"]
    assert ns._camera_controller.enabled is False
    assert ns._gizmo.visible is False
    assert ns._camera_helpers["camShape"].visible is False
    assert not ns.look_through("camShape")  # already there
    assert ns.look_through(None)
    assert ns.camera is ns._free_camera
    assert ns._camera_controller.enabled is True
    assert ns._gizmo.visible is True
    assert ns._camera_helpers["camShape"].visible is True


def test_tree_move_filter_handles_the_cam_folder() -> None:
    ns = SimpleNamespace(_world=_world())
    accept = lambda names, parent, row=0: SceneViewportWindow._accept_tree_move(  # noqa: E731
        ns,
        names,
        parent,
        row,  # type: ignore[arg-type]
    )
    assert accept(["cam"], "cam")
    assert accept(["rig"], "geo")  # rig itself carries no camera
    assert not accept(["cam"], "geo")
    assert not accept(["cube"], "cam")
    assert not accept(["key"], "cam")
    assert accept(["cam"], "cube")
    selected = SimpleNamespace(
        _world=_world(), _viewport=SimpleNamespace(selected_names=["cube", "cam"])
    )
    assert SceneViewportWindow._selected_camera(selected) == "camShape"  # type: ignore[arg-type]
    selected._viewport.selected_names = ["cube"]
    assert SceneViewportWindow._selected_camera(selected) is None  # type: ignore[arg-type]


def test_gltf_cameras_import_onto_their_nodes(tmp_path: Path) -> None:
    scene = trimesh.Scene(trimesh.creation.box())
    files = scene.export(file_type="gltf")
    gltf = tmp_path / "cams.gltf"
    for name, payload in files.items():
        (gltf if name.endswith(".gltf") else tmp_path / name).write_bytes(payload)
    document = json.loads(gltf.read_text(encoding="utf-8"))
    document["cameras"] = [
        {
            "type": "perspective",
            "perspective": {"yfov": math.radians(40.0), "znear": 0.2, "zfar": 90.0},
        },
        {
            "type": "orthographic",
            "orthographic": {"xmag": 3.0, "ymag": 2.0, "znear": 0.1, "zfar": 50.0},
        },
        {"type": "perspective", "perspective": {"yfov": 1.0, "znear": 0.05}},
    ]
    document["nodes"] += [
        {"name": "shot", "translation": [0.0, 1.0, 6.0], "camera": 0},
        {"name": "plan", "translation": [0.0, 9.0, 0.0], "camera": 1},
        {"name": "inf", "camera": 2},
    ]
    count = len(document["nodes"])
    document["scenes"][0]["nodes"] += [count - 3, count - 2, count - 1]
    gltf.write_text(json.dumps(document), encoding="utf-8")

    model = load_model(gltf)
    assert [c.kind for c in model.cameras] == [
        "perspective",
        "orthographic",
        "perspective",
    ]
    result = import_objects(model, set())
    cameras = {c.transform: c for c in result.objects.cameras}
    assert set(cameras) == {"shot", "plan", "inf"}
    assert cameras["shot"].fov == pytest.approx(40.0)
    assert (cameras["shot"].near, cameras["shot"].far) == (0.2, 90.0)
    assert cameras["plan"].kind == "orthographic"
    assert cameras["plan"].ortho_size == 4.0
    # An infinite perspective gets a finite far plane.
    assert cameras["inf"].far > cameras["inf"].near
    transforms = {t.name: t for t in result.objects.transforms}
    assert np.allclose(transforms["shot"].position, [0.0, 1.0, 6.0])
    # The single-mesh shortcut is skipped so the camera nodes exist.
    assert any(mesh.transform != result.root for mesh in result.objects.meshes)


def test_export_round_trips_cameras(tmp_path: Path) -> None:
    out = tmp_path / "cams.glb"
    result = export_scene(_world(), out)
    assert result.cameras == 2
    model = load_model(out)
    assert {c.kind for c in model.cameras} == {"perspective", "orthographic"}
    imported = import_objects(model, set())
    by_transform = {c.transform: c for c in imported.objects.cameras}
    assert by_transform["cam"].fov == pytest.approx(35.0)
    assert by_transform["cam"].near == pytest.approx(0.5)
    assert by_transform["cam"].far == pytest.approx(200.0)
    assert by_transform["ortho"].ortho_size == pytest.approx(4.0)
    cam = next(t for t in imported.objects.transforms if t.name == "cam")
    assert np.allclose(cam.position, [0.0, 2.0, 8.0])
