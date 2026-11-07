"""Tests for base color textures read from imported model files."""

from __future__ import annotations

import functools
import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
trimesh = pytest.importorskip("trimesh")
pygfx = pytest.importorskip("pygfx")
Image = pytest.importorskip("PIL.Image")

from trimesh.visual.material import PBRMaterial, SimpleMaterial  # noqa: E402

from scene_viewport.components import MeshComponent  # noqa: E402
from scene_viewport.mesh.importer import load_model  # noqa: E402
from scene_viewport.mesh.references import (  # noqa: E402
    reference_problems,
    referenced_files,
    source_signature,
)
from scene_viewport.textures import file_maps, texture_from_rgba  # noqa: E402
from scene_viewport.viewport import ViewportController  # noqa: E402

RED = [255, 0, 0, 255]
BLUE = [0, 0, 255, 255]


def _quadrants(size: int = 8) -> object:
    """Top-left red, top-right green, bottom-left blue, bottom-right white."""
    half = size // 2
    pixels = np.zeros((size, size, 4), dtype=np.uint8)
    pixels[..., 3] = 255
    pixels[:half, :half, :3] = (255, 0, 0)
    pixels[:half, half:, :3] = (0, 255, 0)
    pixels[half:, :half, :3] = (0, 0, 255)
    pixels[half:, half:, :3] = (255, 255, 255)
    return Image.fromarray(pixels, "RGBA")


def _quad(material: object, *, uv: bool = True) -> object:
    """A 2x2 quad facing +Z with bottom-left (trimesh) UVs."""
    vertices = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=float)
    mesh = trimesh.Trimesh(vertices, [[0, 1, 2], [0, 2, 3]], process=False)
    coords = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float) if uv else None
    mesh.visual = trimesh.visual.TextureVisuals(uv=coords, material=material)
    return mesh


def _glb(path: Path, material: object, *, uv: bool = True) -> Path:
    path.write_bytes(trimesh.Scene(_quad(material, uv=uv)).export(file_type="glb"))
    return path


def _host() -> SimpleNamespace:
    """Stand-in ``self`` for the viewport's texture lookups (no GPU window)."""
    host = SimpleNamespace(_albedo_cache={}, _albedo_stamps={})
    host._file_texture = functools.partial(ViewportController._file_texture, host)
    return host


def test_gltf_base_color_texture_is_read_top_row_first(tmp_path: Path) -> None:
    model = load_model(
        _glb(tmp_path / "quad.glb", PBRMaterial(baseColorTexture=_quadrants()))
    )
    key = model.nodes[0].mesh
    pixels = model.maps[key].base_color
    assert pixels is not None
    assert pixels.shape == (8, 8, 4)
    assert pixels.dtype == np.uint8
    assert pixels[0, 0].tolist() == RED
    assert pixels[-1, 0].tolist() == BLUE
    # No factor in the file: glTF's white default, so the texture shows as is.
    material = model.materials[key]
    assert material is not None
    assert material.color == "#ffffff"


def test_gltf_data_maps_and_emissive_factor_are_read(tmp_path: Path) -> None:
    material = PBRMaterial(
        baseColorTexture=_quadrants(),
        metallicRoughnessTexture=_quadrants(),
        normalTexture=_quadrants(),
        emissiveTexture=_quadrants(),
        emissiveFactor=[1.0, 0.5, 0.0],
    )
    model = load_model(_glb(tmp_path / "maps.glb", material))
    maps = model.maps[model.nodes[0].mesh]
    for image in (maps.metallic_roughness, maps.normal, maps.emissive):
        assert image is not None
        assert image[0, 0].tolist() == RED
    assert maps.emissive_factor == pytest.approx((1.0, 0.5, 0.0))


def test_obj_material_image_is_read(tmp_path: Path) -> None:
    path = tmp_path / "quad.obj"
    _quad(SimpleMaterial(image=_quadrants(), diffuse=[255, 255, 255, 255])).export(path)
    model = load_model(path)
    pixels = model.maps[model.nodes[0].mesh].base_color
    assert pixels is not None
    assert pixels[0, 0].tolist() == RED
    assert model.warnings == ()


def test_mesh_without_uvs_or_image_has_no_maps(tmp_path: Path) -> None:
    no_uv = load_model(
        _glb(
            tmp_path / "no_uv.glb", PBRMaterial(baseColorTexture=_quadrants()), uv=False
        )
    )
    assert dict(no_uv.maps) == {}
    plain = load_model(
        _glb(tmp_path / "plain.glb", PBRMaterial(baseColorFactor=[10, 20, 30, 255]))
    )
    assert dict(plain.maps) == {}


def test_texture_from_rgba_flips_and_marks_data_maps_linear() -> None:
    pixels = np.asarray(_quadrants(), dtype=np.uint8)
    texture = texture_from_rgba(pixels)
    data = np.asarray(texture.data)
    # Row 0 of the texture is the image's bottom row (UV v = 0).
    assert data[0, 0].tolist() == BLUE
    assert data[-1, 0].tolist() == RED
    assert texture.colorspace == "srgb"
    assert texture_from_rgba(pixels, color=False).colorspace == "physical"


def test_file_maps_missing_file_or_mesh_is_none(tmp_path: Path) -> None:
    path = _glb(tmp_path / "quad.glb", PBRMaterial(baseColorTexture=_quadrants()))
    key = load_model(path).nodes[0].mesh
    assert file_maps(path, key) is not None
    assert file_maps(path, "nope") is None
    assert file_maps(tmp_path / "gone.glb", key) is None


def test_albedo_map_overrides_the_file_texture(tmp_path: Path) -> None:
    path = _glb(tmp_path / "quad.glb", PBRMaterial(baseColorTexture=_quadrants()))
    key = load_model(path).nodes[0].mesh
    host = _host()
    imported = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=path, source_mesh=key
    )
    texture = ViewportController._albedo_texture(host, imported)  # type: ignore[arg-type]
    assert texture is not None
    # Cached: the same texture object on the next draw-mode change.
    assert ViewportController._albedo_texture(host, imported) is texture  # type: ignore[arg-type]
    swatch = tmp_path / "swatch.png"
    Image.new("RGBA", (2, 2), (9, 9, 9, 255)).save(swatch)
    override = imported.model_copy(update={"albedo_map": swatch})
    other = ViewportController._albedo_texture(host, override)  # type: ignore[arg-type]
    assert other is not None
    assert other is not texture
    assert np.asarray(other.data)[0, 0].tolist() == [9, 9, 9, 255]
    # The override's file state is recorded for reload checks.
    assert str(swatch) in host._albedo_stamps
    # Primitives without an albedo map stay untextured.
    box = MeshComponent(name="boxShape", transform="box", kind="box")
    assert ViewportController._albedo_texture(host, box) is None  # type: ignore[arg-type]


def test_imported_maps_go_on_the_textured_material(tmp_path: Path) -> None:
    material = PBRMaterial(
        baseColorTexture=_quadrants(),
        metallicRoughnessTexture=_quadrants(),
        normalTexture=_quadrants(),
        emissiveTexture=_quadrants(),
        emissiveFactor=[1.0, 1.0, 1.0],
    )
    path = _glb(tmp_path / "maps.glb", material)
    key = load_model(path).nodes[0].mesh
    host = _host()
    component = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=path, source_mesh=key
    )
    target = pygfx.MeshStandardMaterial()
    ViewportController._apply_file_maps(host, target, component)  # type: ignore[arg-type]
    assert target.roughness_map is not None
    assert target.metalness_map is not None
    assert target.roughness_map.texture is target.metalness_map.texture
    assert target.roughness_map.texture.colorspace == "physical"
    assert target.normal_map is not None
    assert target.emissive_map is not None
    assert target.emissive_map.texture.colorspace == "srgb"


def test_black_emissive_factor_leaves_emission_off(tmp_path: Path) -> None:
    material = PBRMaterial(baseColorTexture=_quadrants(), emissiveTexture=_quadrants())
    path = _glb(tmp_path / "dark.glb", material)
    key = load_model(path).nodes[0].mesh
    component = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=path, source_mesh=key
    )
    target = pygfx.MeshStandardMaterial()
    ViewportController._apply_file_maps(_host(), target, component)  # type: ignore[arg-type]
    assert target.emissive_map is None


def _obj_with_texture(folder: Path) -> Path:
    path = folder / "quad.obj"
    _quad(SimpleMaterial(image=_quadrants(), diffuse=[255, 255, 255, 255])).export(path)
    return path


def test_obj_references_and_warnings(tmp_path: Path) -> None:
    path = _obj_with_texture(tmp_path)
    names = [file.name for file in referenced_files(path)]
    assert names[0].endswith(".mtl")
    image = tmp_path / names[1]
    assert image.suffix == ".png"
    image.write_bytes(b"not a png")
    assert reference_problems(path) == [f"quad.obj: cannot read image {image.name}"]
    image.unlink()
    assert reference_problems(path) == [f"quad.obj: missing {image.name}"]
    # The model still loads, in its material color, and says why.
    model = load_model(path)
    assert dict(model.maps) == {}
    assert model.warnings == (f"quad.obj: missing {image.name}",)


def test_gltf_external_image_is_referenced(tmp_path: Path) -> None:
    _quadrants().save(tmp_path / "paint.png")
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": "data.bin", "byteLength": 0}],
        "images": [{"uri": "paint%20new.png"}, {"uri": "data:image/png;base64,AA=="}],
    }
    gltf = tmp_path / "scene.gltf"
    gltf.write_text(json.dumps(document), encoding="utf-8")
    assert [f.name for f in referenced_files(gltf)] == ["data.bin", "paint new.png"]
    glb = tmp_path / "scene.glb"
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(payload))
    glb.write_bytes(header + struct.pack("<II", len(payload), 0x4E4F534A) + payload)
    assert [f.name for f in referenced_files(glb)] == ["data.bin", "paint new.png"]
    assert referenced_files(tmp_path / "missing.gltf") == ()


def test_editing_a_texture_changes_the_signature_and_reloads(tmp_path: Path) -> None:
    path = _obj_with_texture(tmp_path)
    before = source_signature(path)
    first = load_model(path)
    assert load_model(path) is first
    image = referenced_files(path)[1]
    Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save(image)
    stat = image.stat()
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert source_signature(path) != before
    second = load_model(path)
    assert second is not first
    pixels = second.maps[second.nodes[0].mesh].base_color
    assert pixels is not None
    assert pixels[0, 0].tolist() == [0, 255, 0, 255]


def test_vertex_tangents_follow_u_and_record_handedness() -> None:
    from scene_viewport.mesh.importer import vertex_tangents

    positions = np.array(
        [[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=np.float32
    )
    normals = np.tile(np.array([0, 0, 1], dtype=np.float32), (4, 1))
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    tangents = vertex_tangents(positions, normals, uv, faces)
    assert tangents.shape == (4, 4)
    assert np.allclose(tangents, [[1, 0, 0, 1]] * 4)
    # V running down the quad flips the bitangent: w = -1.
    flipped = vertex_tangents(positions, normals, uv * [1, -1], faces)
    assert np.allclose(flipped[:, 3], -1.0)
    # No usable UVs: still a unit tangent perpendicular to the normal.
    flat = vertex_tangents(positions, normals, np.zeros_like(uv), faces)
    assert np.allclose(np.linalg.norm(flat[:, :3], axis=1), 1.0)
    assert np.allclose(np.sum(flat[:, :3] * normals, axis=1), 0.0)


def test_normal_mapped_meshes_get_tangents_on_their_geometry(tmp_path: Path) -> None:
    from scene_viewport.mesh.geometry import file_geometry

    mapped = _glb(
        tmp_path / "mapped.glb",
        PBRMaterial(baseColorTexture=_quadrants(), normalTexture=_quadrants()),
    )
    plain = _glb(tmp_path / "plain.glb", PBRMaterial(baseColorTexture=_quadrants()))
    for path, expected in ((mapped, True), (plain, False)):
        model = load_model(path)
        key = model.nodes[0].mesh
        assert (model.meshes[key].tangents is not None) is expected
        geometry = file_geometry(path, key)
        assert (getattr(geometry, "tangents", None) is not None) is expected
