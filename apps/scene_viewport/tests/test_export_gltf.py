"""glTF export round trips and punctual light import."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
Image = pytest.importorskip("PIL.Image")

from scene_viewport.components import (  # noqa: E402
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.export_gltf import export_scene  # noqa: E402
from scene_viewport.mesh.gltf_extras import GltfDocument, join_glb, split_glb  # noqa: E402
from scene_viewport.mesh.importer import load_model  # noqa: E402
from scene_viewport.model_import import import_objects  # noqa: E402
from scene_viewport.world import SceneWorld  # noqa: E402


def _world() -> SceneWorld:
    return SceneWorld(
        transforms=[
            TransformComponent(name="rig", position=(1.0, 0.0, 0.0)),
            TransformComponent(
                name="cube",
                parent="rig",
                position=(0.0, 2.0, 0.0),
                scale=(2.0, 1.0, 1.0),
            ),
            TransformComponent(name="ball", position=(0.0, 0.0, -3.0)),
            TransformComponent(name="ghost", visible=False),
            TransformComponent(name="key", position=(4.0, 8.0, 3.0)),
            TransformComponent(name="lamp", position=(0.0, 3.0, 0.0)),
        ],
        meshes=[
            MeshComponent(
                name="cubeShape",
                transform="cube",
                kind="box",
                color="#ff0000",
                roughness=0.3,
                metalness=0.9,
                alpha_mode="blend",
                opacity=0.5,
                double_sided=False,
                unlit=True,
            ),
            MeshComponent(name="ballShape", transform="ball", kind="sphere"),
            MeshComponent(name="ghostShape", transform="ghost", kind="plane"),
        ],
        lights=[
            LightComponent(
                name="keyShape", transform="key", kind="directional", intensity=2.0
            ),
            LightComponent(
                name="lampShape",
                transform="lamp",
                kind="point",
                color="#00ff00",
                intensity=5.0,
                distance=12.0,
            ),
        ],
    )


def test_export_glb_round_trips_hierarchy_materials_and_lights(tmp_path: Path) -> None:
    out = tmp_path / "scene.glb"
    result = export_scene(_world(), out)
    assert result.path == out
    assert (result.transforms, result.meshes, result.lights) == (5, 2, 2)
    assert result.warnings == [
        "1 hidden transform(s) left out; use Export with hidden objects to include them"
    ]

    document = GltfDocument(out)
    names = {node.get("name") for node in document.entries("nodes")}
    assert {"rig", "cube", "ball", "key", "lamp", "keyShape", "lampShape"} <= names
    assert "ghost" not in names
    assert "KHR_lights_punctual" in document.document["extensionsUsed"]
    lights = document.document["extensions"]["KHR_lights_punctual"]["lights"]  # type: ignore[index]
    assert [light["type"] for light in lights] == ["directional", "point"]
    assert lights[1]["range"] == 12.0
    assert lights[1]["color"] == [0.0, 1.0, 0.0]
    cube_material = next(
        m for m in document.entries("materials") if m.get("name") == "cubeShape"
    )
    assert cube_material["alphaMode"] == "BLEND"
    assert cube_material["doubleSided"] is False
    assert "KHR_materials_unlit" in cube_material["extensions"]  # type: ignore[operator]
    assert cube_material["pbrMetallicRoughness"]["baseColorFactor"][3] == pytest.approx(
        0.5, abs=0.01
    )  # type: ignore[index]

    # The importer reads it back: hierarchy, surface, and lights.
    model = load_model(out)
    by_name = {node.name: node for node in model.nodes}
    assert by_name["cube"].parent == "rig"
    assert by_name["cube"].mesh
    cube_surface = model.materials[by_name["cube"].mesh]
    assert cube_surface is not None
    assert cube_surface.alpha_mode == "blend"
    assert cube_surface.unlit
    assert not cube_surface.double_sided
    assert cube_surface.color == "#ff0000"
    assert {light.kind for light in model.lights} == {"directional", "point"}
    imported = import_objects(model, set())
    assert {light.kind for light in imported.objects.lights} == {"directional", "point"}
    # The key light's node aims at the origin along the exported direction.
    key = next(t for t in imported.objects.transforms if t.name == "key")
    direction = -np.asarray(key.position) / np.linalg.norm(key.position)
    expected = -np.array([4.0, 8.0, 3.0]) / np.linalg.norm([4.0, 8.0, 3.0])
    assert np.allclose(direction, expected, atol=1e-5)


def test_export_gltf_json_writes_buffers_beside_it(tmp_path: Path) -> None:
    out = tmp_path / "out" / "scene.gltf"
    out.parent.mkdir()
    result = export_scene(_world(), out, roots=["rig"], include_hidden=True)
    assert result.transforms == 2
    assert result.meshes == 1
    assert result.lights == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    for buffer in document["buffers"]:
        assert (out.parent / buffer["uri"]).is_file()
    names = {node.get("name") for node in document["nodes"]}
    assert names == {"rig", "cube"} or {"rig", "cube"} <= names
    model = load_model(out)
    assert {node.name for node in model.nodes} == {"rig", "cube"}


def test_export_selection_keeps_world_pose_of_detached_roots(tmp_path: Path) -> None:
    out = tmp_path / "part.glb"
    export_scene(_world(), out, roots=["cube"])
    model = load_model(out)
    cube = next(node for node in model.nodes if node.name == "cube")
    assert cube.parent == ""
    # rig at x=1 plus cube at y=2 → world position (1, 2, 0).
    assert np.allclose(cube.matrix[:3, 3], [1.0, 2.0, 0.0])


def test_export_refuses_bad_paths_and_empty_sets(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.glb or \.gltf"):
        export_scene(_world(), tmp_path / "scene.obj")
    with pytest.raises(ValueError, match="nothing to export"):
        export_scene(_world(), tmp_path / "scene.glb", roots=["missing"])
    with pytest.raises(ValueError, match="nothing to export"):
        export_scene(SceneWorld(), tmp_path / "scene.glb")


def test_glb_join_and_split_round_trip() -> None:
    document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 5}]}
    data = join_glb(document, b"hello")
    assert len(data) % 4 == 0
    parsed, binary = split_glb(data)
    assert parsed == document
    assert binary is not None
    assert binary[:5] == b"hello"
    assert split_glb(join_glb(document, None))[1] is None
    with pytest.raises(ValueError, match="not a GLB"):
        split_glb(b"nope")


def _lit_gltf(tmp_path: Path) -> Path:
    """A glTF with a cube node, a rotated directional light, and a spot light."""
    cube = trimesh.creation.box()
    scene = trimesh.Scene(cube)
    files = scene.export(file_type="gltf")
    gltf = tmp_path / "lit.gltf"
    for name, payload in files.items():
        (gltf if name.endswith(".gltf") else tmp_path / name).write_bytes(payload)
    document = json.loads(gltf.read_text(encoding="utf-8"))
    # Rotate the sun node 90° about X: its -Z then points along -Y... wait,
    # a +90° rotation about X maps -Z to +Y; use -90° so the light points down.
    half = math.sin(-math.pi / 4)
    document["nodes"] += [
        {
            "name": "sun",
            "rotation": [half, 0.0, 0.0, math.cos(-math.pi / 4)],
            "extensions": {"KHR_lights_punctual": {"light": 0}},
        },
        {
            "name": "spot",
            "translation": [0.0, 5.0, 0.0],
            "extensions": {"KHR_lights_punctual": {"light": 1}},
        },
    ]
    document["scenes"][0]["nodes"] += [
        len(document["nodes"]) - 2,
        len(document["nodes"]) - 1,
    ]
    document["extensions"] = {
        "KHR_lights_punctual": {
            "lights": [
                {"type": "directional", "color": [1.0, 0.5, 0.0], "intensity": 3.0},
                {
                    "type": "spot",
                    "intensity": 500.0,
                    "range": 20.0,
                    "spot": {"outerConeAngle": 0.5},
                },
            ]
        }
    }
    document["extensionsUsed"] = ["KHR_lights_punctual"]
    gltf.write_text(json.dumps(document), encoding="utf-8")
    return gltf


def test_punctual_lights_import_onto_their_nodes(tmp_path: Path) -> None:
    gltf = _lit_gltf(tmp_path)
    model = load_model(gltf)
    assert [light.kind for light in model.lights] == ["directional", "spot"]
    assert model.lights[0].color == (1.0, 0.5, 0.0)
    assert model.lights[1].range == 20.0
    assert model.lights[1].outer_cone == 0.5

    result = import_objects(model, set())
    lights = {light.kind: light for light in result.objects.lights}
    assert set(lights) == {"directional", "spot"}
    assert lights["directional"].color == "#ff8000"
    assert lights["directional"].intensity == 3.0
    # Intensity above the component range is clamped, with a warning.
    assert lights["spot"].intensity == 20.0
    assert lights["spot"].distance == 20.0
    assert any("clamped" in w for w in result.warnings)
    assert any("direction was dropped" in w for w in result.warnings)
    transforms = {t.name: t for t in result.objects.transforms}
    # The sun pointed straight down, so its node sits above the origin.
    sun = transforms[lights["directional"].transform]
    assert np.allclose(sun.position, [0.0, 8.0, 0.0], atol=1e-5)
    spot = transforms[lights["spot"].transform]
    assert np.allclose(spot.position, [0.0, 5.0, 0.0])
    # Shapes attach to their node, and the single-mesh shortcut is skipped.
    assert lights["spot"].transform == spot.name
    assert any(mesh.transform != result.root for mesh in result.objects.meshes)


def test_import_without_lights_is_unchanged(tmp_path: Path) -> None:
    cube = trimesh.creation.box()
    glb = tmp_path / "cube.glb"
    glb.write_bytes(trimesh.Scene(cube).export(file_type="glb"))
    model = load_model(glb)
    assert model.lights == ()
    result = import_objects(model, set())
    assert result.objects.lights == []
    assert result.warnings == ()
    # A lone mesh still lands on the root.
    assert result.objects.meshes[0].transform == result.root


def test_window_export_path_reports_and_remembers(tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from scene_viewport.window import SceneViewportWindow

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(str(a[2]))
    )
    recents: list[Path] = []
    messages: list[str] = []
    ns = SimpleNamespace(
        _world=_world(),
        _remember_recent=recents.append,
        statusBar=lambda: SimpleNamespace(
            showMessage=lambda text, _ms: messages.append(text)
        ),
    )
    out = tmp_path / "scene.glb"
    assert SceneViewportWindow.export_path(ns, out)  # type: ignore[arg-type]
    assert recents == [out]
    assert len(messages) == 1
    assert "5 nodes, 2 meshes, 2 lights" in messages[-1]
    assert len(warnings) == 1
    assert "hidden transform" in warnings[-1]
    assert not SceneViewportWindow.export_path(ns, tmp_path / "scene.obj")  # type: ignore[arg-type]
    assert "Could not export" in warnings[-1]
