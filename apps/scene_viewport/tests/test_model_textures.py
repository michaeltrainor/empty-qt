"""Tests for base color textures read from imported model files."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("pygfx")
Image = pytest.importorskip("PIL.Image")

from trimesh.visual.material import PBRMaterial, SimpleMaterial  # noqa: E402

from scene_viewport.components import MeshComponent  # noqa: E402
from scene_viewport.mesh.importer import load_model  # noqa: E402
from scene_viewport.textures import file_texture, texture_from_rgba  # noqa: E402
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


def test_gltf_base_color_texture_is_read_top_row_first(tmp_path: Path) -> None:
    model = load_model(
        _glb(tmp_path / "quad.glb", PBRMaterial(baseColorTexture=_quadrants()))
    )
    key = model.nodes[0].mesh
    pixels = model.textures[key]
    assert pixels.shape == (8, 8, 4)
    assert pixels.dtype == np.uint8
    assert pixels[0, 0].tolist() == RED
    assert pixels[-1, 0].tolist() == BLUE
    # No factor in the file: glTF's white default, so the texture shows as is.
    material = model.materials[key]
    assert material is not None
    assert material.color == "#ffffff"


def test_obj_material_image_is_read(tmp_path: Path) -> None:
    path = tmp_path / "quad.obj"
    _quad(SimpleMaterial(image=_quadrants(), diffuse=[255, 255, 255, 255])).export(path)
    model = load_model(path)
    pixels = model.textures[model.nodes[0].mesh]
    assert pixels[0, 0].tolist() == RED


def test_mesh_without_uvs_or_image_has_no_texture(tmp_path: Path) -> None:
    no_uv = load_model(
        _glb(
            tmp_path / "no_uv.glb", PBRMaterial(baseColorTexture=_quadrants()), uv=False
        )
    )
    assert dict(no_uv.textures) == {}
    plain = load_model(
        _glb(tmp_path / "plain.glb", PBRMaterial(baseColorFactor=[10, 20, 30, 255]))
    )
    assert dict(plain.textures) == {}


def test_texture_from_rgba_flips_to_bottom_left_origin() -> None:
    pixels = np.asarray(_quadrants(), dtype=np.uint8)
    texture = texture_from_rgba(pixels)
    data = np.asarray(texture.data)
    # Row 0 of the texture is the image's bottom row (UV v = 0).
    assert data[0, 0].tolist() == BLUE
    assert data[-1, 0].tolist() == RED


def test_file_texture_missing_file_or_mesh_is_none(tmp_path: Path) -> None:
    path = _glb(tmp_path / "quad.glb", PBRMaterial(baseColorTexture=_quadrants()))
    key = load_model(path).nodes[0].mesh
    assert file_texture(path, key) is not None
    assert file_texture(path, "nope") is None
    assert file_texture(tmp_path / "gone.glb", key) is None


def test_albedo_map_overrides_the_file_texture(tmp_path: Path) -> None:
    """Viewport logic, run with a stand-in ``self`` (no GPU window)."""
    path = _glb(tmp_path / "quad.glb", PBRMaterial(baseColorTexture=_quadrants()))
    key = load_model(path).nodes[0].mesh
    host = SimpleNamespace(_albedo_cache={})
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
    # Primitives without an albedo map stay untextured.
    box = MeshComponent(name="boxShape", transform="box", kind="box")
    assert ViewportController._albedo_texture(host, box) is None  # type: ignore[arg-type]
