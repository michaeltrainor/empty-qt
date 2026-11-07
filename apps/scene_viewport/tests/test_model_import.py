"""Tests for model file import: loading, scene components, and save paths."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
trimesh = pytest.importorskip("trimesh")
gfx = pytest.importorskip("pygfx")

from trimesh.visual.material import PBRMaterial  # noqa: E402

from scene_viewport.components import MeshComponent, TransformComponent  # noqa: E402
from scene_viewport.mesh.geometry import file_geometry  # noqa: E402
from scene_viewport.mesh.importer import (  # noqa: E402
    ModelImportError,
    importable_paths,
    load_model,
)
from scene_viewport.model_import import import_objects, safe_name  # noqa: E402
from scene_viewport.scene_io import SceneDocument, load_scene, save_scene  # noqa: E402
from scene_viewport.viewport import ViewportController  # noqa: E402


def _car(path: Path) -> Path:
    """Write a glTF: a red metal body with two wheels as its children."""
    scene = trimesh.Scene()
    body = trimesh.creation.box((1.0, 2.0, 1.0))
    body.visual = trimesh.visual.TextureVisuals(
        material=PBRMaterial(
            baseColorFactor=[204, 51, 51, 255],
            roughnessFactor=0.25,
            metallicFactor=0.75,
        )
    )
    wheel = trimesh.creation.cylinder(0.3, 0.2)
    move = trimesh.transformations.translation_matrix
    scene.add_geometry(
        body, node_name="car", geom_name="body", transform=move((0, 1, 0))
    )
    for name, x in (("wheel.FL", 0.5), ("wheel.FR", -0.5)):
        scene.add_geometry(
            wheel,
            node_name=name,
            geom_name="wheel",
            parent_node_name="car",
            transform=move((x, -1, 0.5)),
        )
    path.write_bytes(scene.export(file_type="glb"))
    return path


def test_load_gltf_keeps_hierarchy_names_and_materials(tmp_path: Path) -> None:
    model = load_model(_car(tmp_path / "car.glb"))
    nodes = {node.name: node for node in model.nodes}
    assert set(nodes) == {"car", "wheel.FL", "wheel.FR"}
    # Parents come before children.
    assert model.nodes[0].name == "car"
    assert nodes["car"].parent == ""
    assert nodes["wheel.FL"].parent == "car"
    assert np.allclose(nodes["wheel.FL"].matrix[:3, 3], (0.5, -1.0, 0.5))
    body = model.meshes[nodes["car"].mesh]
    assert body.positions.dtype == np.float32
    assert body.indices.dtype == np.uint32
    assert body.normals.shape == body.positions.shape
    assert body.texcoords.shape == (len(body.positions), 2)
    material = model.materials[nodes["car"].mesh]
    assert material is not None
    assert material.color == "#cc3333"
    assert material.roughness == pytest.approx(0.25)
    assert material.metalness == pytest.approx(0.75)


@pytest.mark.parametrize("suffix", [".obj", ".stl", ".ply"])
def test_single_mesh_formats_load_as_one_node(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"ball{suffix}"
    trimesh.creation.icosphere(subdivisions=1).export(path)
    model = load_model(path)
    assert len(model.nodes) == 1
    assert model.nodes[0].mesh in model.meshes


def test_vertex_colors_become_the_mesh_color(tmp_path: Path) -> None:
    mesh = trimesh.creation.box()
    mesh.visual.vertex_colors = [20, 200, 40, 255]
    path = tmp_path / "green.ply"
    mesh.export(path)
    model = load_model(path)
    material = model.materials[model.nodes[0].mesh]
    assert material is not None
    assert material.color == "#14c828"


def test_load_errors_are_model_import_errors(tmp_path: Path) -> None:
    with pytest.raises(ModelImportError, match="unsupported"):
        load_model(tmp_path / "notes.txt")
    with pytest.raises(ModelImportError, match="cannot read"):
        load_model(tmp_path / "missing.glb")
    broken = tmp_path / "broken.glb"
    broken.write_bytes(b"not a gltf")
    with pytest.raises(ModelImportError, match="cannot read"):
        load_model(broken)
    points = tmp_path / "points.obj"
    points.write_text("v 0 0 0\nv 1 0 0\n", encoding="utf-8")
    with pytest.raises(ModelImportError):
        load_model(points)


def test_load_model_reuses_an_unchanged_file(tmp_path: Path) -> None:
    path = _car(tmp_path / "car.glb")
    first = load_model(path)
    assert load_model(path) is first
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert load_model(path) is not first


def test_importable_paths_filters_type_existence_and_duplicates(tmp_path: Path) -> None:
    model = _car(tmp_path / "car.glb")
    text = tmp_path / "notes.txt"
    text.write_text("x", encoding="utf-8")
    found = importable_paths([text, model, tmp_path / "gone.obj", model])
    assert found == [model]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("wheel.FL", "wheel_FL"),
        ("ball.obj", "ball_obj"),
        ("3d scan", "_3d_scan"),
        ("..", "node"),
    ],
)
def test_safe_name(text: str, expected: str) -> None:
    assert safe_name(text) == expected


def test_import_objects_builds_a_root_with_the_file_hierarchy(tmp_path: Path) -> None:
    model = load_model(_car(tmp_path / "car.glb"))
    used = {"car"}
    result = import_objects(model, used, space="local")
    transforms = {t.name: t for t in result.objects.transforms}
    # "car" was taken, so the root and the file's own "car" node get suffixes.
    assert result.root == "car1"
    assert result.objects.transforms[0].name == "car1"
    assert transforms["car2"].parent == "car1"
    assert transforms["wheel_FL"].parent == "car2"
    assert transforms["wheel_FL"].position == pytest.approx((0.5, -1.0, 0.5))
    assert all(t.space == "local" for t in transforms.values())
    meshes = {m.transform: m for m in result.objects.meshes}
    assert set(meshes) == {"car2", "wheel_FL", "wheel_FR"}
    body = meshes["car2"]
    assert body.kind == "file"
    assert body.source == model.path
    assert body.source_mesh in model.meshes
    assert body.color == "#cc3333"
    # Every new name was claimed.
    assert {*transforms, *(m.name for m in result.objects.meshes)} <= used


def test_import_objects_puts_a_lone_mesh_on_the_root(tmp_path: Path) -> None:
    path = tmp_path / "ball.obj"
    trimesh.creation.icosphere(subdivisions=1).export(path)
    result = import_objects(load_model(path), set())
    assert [t.name for t in result.objects.transforms] == ["ball"]
    (mesh,) = result.objects.meshes
    assert mesh.transform == "ball"
    assert mesh.name == "ballShape"
    # No material in the file: the mesh keeps the default look.
    assert mesh.color == MeshComponent.model_fields["color"].default


def test_file_geometry_and_unknown_mesh(tmp_path: Path) -> None:
    path = _car(tmp_path / "car.glb")
    model = load_model(path)
    key = model.nodes[0].mesh
    geometry = file_geometry(path, key)
    assert geometry.positions is not None
    assert geometry.positions.nitems == len(model.meshes[key].positions)
    with pytest.raises(ModelImportError, match="no mesh"):
        file_geometry(path, "nope")


def test_missing_source_draws_a_placeholder_once(tmp_path: Path) -> None:
    """Viewport logic, run with a stand-in ``self`` (no GPU window)."""
    host = SimpleNamespace(_missing_sources=[], _source_stamps={})
    missing = MeshComponent(
        name="gone",
        transform="t",
        kind="file",
        source=tmp_path / "gone.glb",
        source_mesh="body",
    )
    for _ in range(2):
        geometry = ViewportController._geometry_for(host, missing)  # type: ignore[arg-type]
        assert geometry.positions is not None
        assert geometry.positions.nitems == 24  # the unit box
    assert ViewportController.take_missing_sources(host) == [missing.source]  # type: ignore[arg-type]
    assert ViewportController.take_missing_sources(host) == []  # type: ignore[arg-type]


def test_scene_saves_sources_relative_and_loads_them_absolute(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    model = _car(tmp_path / "models" / "car.glb")
    document = SceneDocument(
        transforms=[TransformComponent(name="car")],
        meshes=[
            MeshComponent(
                name="carShape",
                transform="car",
                kind="file",
                source=model,
                source_mesh="body",
            ),
            MeshComponent(name="boxShape", transform="car", kind="box"),
        ],
    )
    scene = tmp_path / "scenes" / "shot.json"
    save_scene(document, scene)
    saved = json.loads(scene.read_text(encoding="utf-8"))
    assert saved["meshes"][0]["source"] == "../models/car.glb"
    # Primitives keep the empty default.
    assert saved["meshes"][1]["source"] == "."
    loaded = load_scene(scene)
    assert loaded.meshes[0].source == model.resolve()
    assert load_model(loaded.meshes[0].source).meshes
    # The in-memory document is unchanged by saving.
    assert document.meshes[0].source == model


def test_dropped_models_keeps_local_model_files(qtbot, tmp_path: Path) -> None:
    from PySide6.QtCore import QMimeData, QUrl

    from scene_viewport.window import dropped_models

    model = _car(tmp_path / "car.glb")
    text = tmp_path / "notes.txt"
    text.write_text("x", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(text)),
            QUrl.fromLocalFile(str(model)),
            QUrl("https://example.com/remote.glb"),
        ]
    )
    assert dropped_models(mime) == [model]
    plain = QMimeData()
    plain.setText(str(model))
    assert dropped_models(plain) == []
    assert dropped_models(None) == []


def test_vertex_normals_smooth_shared_and_default_unused() -> None:
    from scene_viewport.mesh.importer import vertex_normals

    sphere = trimesh.creation.icosphere(subdivisions=2)
    positions = np.vstack([sphere.vertices, [[9.0, 9.0, 9.0]]]).astype(np.float32)
    normals = vertex_normals(positions, sphere.faces.astype(np.uint32))
    radial = sphere.vertices / np.linalg.norm(sphere.vertices, axis=1)[:, None]
    # Area weighting on a coarse sphere stays within a few degrees of radial.
    assert np.min(np.sum(normals[:-1] * radial, axis=1)) > 0.995
    # The extra vertex is on no face.
    assert np.allclose(normals[-1], (0.0, 1.0, 0.0))


def test_stl_without_normals_loads_faceted(tmp_path: Path) -> None:
    path = tmp_path / "cube.stl"
    trimesh.creation.box().export(path)
    buffers = next(iter(load_model(path).meshes.values()))
    # Split vertices: every corner normal is one of the six face directions.
    assert np.allclose(np.abs(buffers.normals).max(axis=1), 1.0)


def test_import_conversion_rules() -> None:
    from scene_viewport.model_import import import_conversion

    up = np.array([0.0, 0.0, 1.0, 1.0])
    # STL and PLY are Z-up by default: file +Z becomes scene +Y.
    for name in ("part.stl", "scan.PLY"):
        assert np.allclose(import_conversion(Path(name)) @ up, (0.0, 1.0, 0.0, 1.0))
    # OBJ keeps Y-up unless told otherwise; glTF is never converted.
    assert np.allclose(import_conversion(Path("a.obj")), np.eye(4))
    assert np.allclose(import_conversion(Path("a.glb"), "z", "mm"), np.eye(4))
    z_mm = import_conversion(Path("a.obj"), "z", "mm")
    assert np.allclose(z_mm @ np.array([0.0, 0.0, 1000.0, 1.0]), (0.0, 1.0, 0.0, 1.0))
    assert np.allclose(
        import_conversion(Path("a.stl"), "y", "in")[:3, :3], np.eye(3) * 0.0254
    )


def test_import_objects_applies_the_conversion_to_the_root(tmp_path: Path) -> None:
    from scene_viewport.model_import import import_conversion

    stl = tmp_path / "part.stl"
    trimesh.creation.box().export(stl)
    single = import_objects(
        load_model(stl), set(), conversion=import_conversion(stl, "auto", "mm")
    )
    (root,) = single.objects.transforms
    assert root.rotation == pytest.approx((-math.pi / 2, 0.0, 0.0))
    assert root.scale == pytest.approx((0.001, 0.001, 0.001))
    car = load_model(_car(tmp_path / "car.glb"))
    nested = import_objects(
        car, set(), conversion=import_conversion(car.path, "z", "cm")
    )
    # glTF ignores the setting; the root stays at identity.
    assert nested.objects.transforms[0].scale == pytest.approx((1.0, 1.0, 1.0))


def test_reload_changed_sources_refreshes_only_edited_meshes(tmp_path: Path) -> None:
    """Viewport reload logic, run with a stand-in ``self`` (no GPU window)."""
    from scene_viewport.mesh.references import source_signature

    path = tmp_path / "ball.obj"
    trimesh.creation.icosphere(subdivisions=1).export(path)
    key = load_model(path).nodes[0].mesh
    mesh = MeshComponent(
        name="ballShape", transform="ball", kind="file", source=path, source_mesh=key
    )
    box = MeshComponent(name="boxShape", transform="box", kind="box")
    swapped: list[str] = []
    redrawn: list[str] = []
    host = SimpleNamespace(
        _albedo_stamps={},
        _albedo_cache={
            f"{path}#{key}#base_color": object(),
            "other#x#normal": object(),
        },
        _mesh_components={"ballShape": mesh, "boxShape": box},
        _source_stamps={"ballShape": source_signature(path)},
        _set_mesh_geometry=lambda name, _geometry: swapped.append(name),
        _apply_draw_mode_to_mesh=redrawn.append,
        _apply_highlights=lambda: None,
        _canvas=SimpleNamespace(request_draw=lambda: None),
    )
    reload = ViewportController.reload_changed_sources
    assert reload(host) == []  # type: ignore[arg-type]
    trimesh.creation.icosphere(subdivisions=2).export(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert reload(host) == ["ballShape"]  # type: ignore[arg-type]
    assert swapped == ["ballShape"]
    assert redrawn == ["ballShape"]
    # Its cached textures are dropped; other meshes' are kept.
    assert list(host._albedo_cache) == ["other#x#normal"]
    assert reload(host) == []  # type: ignore[arg-type]
    # A file that breaks keeps the old shape and is retried later.
    path.write_text("not an obj with faces\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert reload(host) == []  # type: ignore[arg-type]
    assert swapped == ["ballShape"]
