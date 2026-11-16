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
from scene_viewport.materials import (  # noqa: E402
    build_material,
    surface_kwargs,
    texture_map,
)
from scene_viewport.mesh.importer import load_model  # noqa: E402
from scene_viewport.mesh.references import (  # noqa: E402
    forget_signatures,
    reference_problems,
    referenced_files,
    remote_references,
    source_signature,
)
from scene_viewport.textures import (  # noqa: E402
    file_maps,
    file_material,
    texture_from_rgba,
)
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
    host._file_material = functools.partial(ViewportController._file_material, host)
    return host


def _build(host: SimpleNamespace, component: MeshComponent, *, textured: bool = True):
    """Build the material the viewport would, through the materials module."""
    surface, maps = file_material(component.source, component.source_mesh)
    albedo = ViewportController._albedo_texture(host, component, maps)  # type: ignore[arg-type]
    return build_material(
        component,
        surface=surface,
        maps=maps,
        albedo=albedo if textured else None,
        texture_for=lambda kind: host._file_texture(component, kind, maps),
        textured=textured,
        env_map=None,
        env_map_intensity=1.0,
    )


def _maps(component: MeshComponent) -> object:
    """The images the viewport reads once per material build."""
    return file_maps(component.source, component.source_mesh)


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
    maps = _maps(imported)
    texture = ViewportController._albedo_texture(host, imported, maps)  # type: ignore[arg-type]
    assert texture is not None
    # Cached: the same texture object on the next draw-mode change.
    assert ViewportController._albedo_texture(host, imported, maps) is texture  # type: ignore[arg-type]
    swatch = tmp_path / "swatch.png"
    Image.new("RGBA", (2, 2), (9, 9, 9, 255)).save(swatch)
    override = imported.model_copy(update={"albedo_map": swatch})
    other = ViewportController._albedo_texture(host, override, maps)  # type: ignore[arg-type]
    assert other is not None
    assert other is not texture
    assert np.asarray(other.data)[0, 0].tolist() == [9, 9, 9, 255]
    # The override's file state is recorded for reload checks.
    assert str(swatch) in host._albedo_stamps
    # Primitives without an albedo map stay untextured.
    box = MeshComponent(name="boxShape", transform="box", kind="box")
    assert ViewportController._albedo_texture(host, box, None) is None  # type: ignore[arg-type]


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
    target = _build(host, component)
    assert isinstance(target, pygfx.MeshStandardMaterial)
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
    target = _build(_host(), component)
    assert isinstance(target, pygfx.MeshStandardMaterial)
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
    forget_signatures()
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


def _touch_later(file: Path) -> None:
    """Bump the mtime so a rewrite in the same second still counts as an edit."""
    stat = file.stat()
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_edited_mtl_is_rescanned_without_touching_the_obj(tmp_path: Path) -> None:
    """A texture added to the .mtl is tracked and reloads the model."""
    path = _obj_with_texture(tmp_path)
    library = referenced_files(path)[0]
    assert library.suffix == ".mtl"
    before = source_signature(path)
    first = load_model(path)
    _quadrants().save(tmp_path / "rough.png")
    library.write_text(
        library.read_text(encoding="utf-8") + "\nmap_Ns rough.png\n", encoding="utf-8"
    )
    _touch_later(library)
    names = [file.name for file in referenced_files(path)]
    assert "rough.png" in names
    forget_signatures()
    assert source_signature(path) != before
    assert load_model(path) is not first
    # Editing the new texture now counts as an edit too.
    after = source_signature(path)
    _touch_later(tmp_path / "rough.png")
    forget_signatures()
    assert source_signature(path) != after


def test_obj_material_names_with_spaces_and_several_libraries(tmp_path: Path) -> None:
    (tmp_path / "my paint.mtl").write_text(
        "newmtl a\n"
        "map_Kd -bm 1 -o 0 0.5 base color.png\n"
        "map_Ns -blendu on -s 1 1 1 rough.png\n"
        "bump -imfchan r bump map.png\n"
        "norm normal.png\n",
        encoding="utf-8",
    )
    (tmp_path / "second.mtl").write_text(
        "newmtl b\nmap_Kd other.png\n", encoding="utf-8"
    )
    (tmp_path / "third.mtl").write_text("newmtl c\n", encoding="utf-8")
    obj = tmp_path / "quad.obj"
    obj.write_text(
        "mtllib my paint.mtl\n"
        "mtllib second.mtl third.mtl\n"
        "mtllib missing.mtl\n"
        "v 0 0 0\n",
        encoding="utf-8",
    )
    names = [file.name for file in referenced_files(obj)]
    assert names == [
        "my paint.mtl",
        "second.mtl",
        "third.mtl",
        "missing.mtl",
        "base color.png",
        "rough.png",
        "bump map.png",
        "normal.png",
        "other.png",
    ]


def test_remote_gltf_uris_are_reported_as_remote_not_missing(tmp_path: Path) -> None:
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": "data.bin", "byteLength": 0}],
        "images": [
            {"uri": "https://example.com/paint.png"},
            {"uri": "HTTP://example.com/paint.png"},
            {"uri": "local.png"},
        ],
    }
    gltf = tmp_path / "scene.gltf"
    gltf.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"")
    _quadrants().save(tmp_path / "local.png")
    assert [f.name for f in referenced_files(gltf)] == ["data.bin", "local.png"]
    assert remote_references(gltf) == (
        "https://example.com/paint.png",
        "HTTP://example.com/paint.png",
    )
    assert reference_problems(gltf) == [
        "scene.gltf: remote file not loaded https://example.com/paint.png",
        "scene.gltf: remote file not loaded HTTP://example.com/paint.png",
    ]
    assert remote_references(tmp_path / "missing.gltf") == ()


def test_obj_libraries_without_mtl_suffix_are_scanned(tmp_path: Path) -> None:
    (tmp_path / "materials.txt").write_text(
        "newmtl a\nmap_Kd paint.png\n", encoding="utf-8"
    )
    obj = tmp_path / "quad.obj"
    obj.write_text("mtllib materials.txt\nv 0 0 0\n", encoding="utf-8")
    assert [f.name for f in referenced_files(obj)] == ["materials.txt", "paint.png"]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("map_Kd -halo tex.png", "tex.png"),
        ("map_Kd -halo 1 on tex.png", "tex.png"),
        ("map_Kd -halo word tex.png", "word tex.png"),
        ("map_Kd -shadow.png", "-shadow.png"),
        ("map_Kd -o 1 2 -dark.png", "-dark.png"),
        ("map_Kd -bm", ""),
        ("map_Kd -type sphere env.png", "env.png"),
    ],
)
def test_mtl_map_file_names_after_unknown_options(
    tmp_path: Path, statement: str, expected: str
) -> None:
    (tmp_path / "m.mtl").write_text(f"newmtl a\n{statement}\n", encoding="utf-8")
    obj = tmp_path / "quad.obj"
    obj.write_text("mtllib m.mtl\nv 0 0 0\n", encoding="utf-8")
    names = [f.name for f in referenced_files(obj)][1:]
    assert names == ([expected] if expected else [])


def _gltf_files(folder: Path, material: object) -> Path:
    """Export a textured quad as .gltf + .bin and return the .gltf path."""
    files = trimesh.Scene(_quad(material)).export(file_type="gltf")
    gltf = folder / "quad.gltf"
    for name, payload in files.items():
        target = gltf if name.endswith(".gltf") else folder / name
        target.write_bytes(payload)
    return gltf


def _edit_gltf(gltf: Path, edit) -> None:
    document = json.loads(gltf.read_text(encoding="utf-8"))
    edit(document)
    gltf.write_text(json.dumps(document), encoding="utf-8")


def test_alpha_sides_and_occlusion_are_read_from_the_material(tmp_path: Path) -> None:
    material = PBRMaterial(
        baseColorFactor=[255, 255, 255, 128],
        baseColorTexture=_quadrants(),
        occlusionTexture=_quadrants(),
        alphaMode="MASK",
        alphaCutoff=0.3,
        doubleSided=True,
    )
    path = _glb(tmp_path / "masked.glb", material)
    model = load_model(path)
    key = model.nodes[0].mesh
    imported = model.materials[key]
    assert imported is not None
    assert imported.alpha_mode == "mask"
    assert imported.alpha_cutoff == 0.3
    assert imported.double_sided is True
    assert not imported.unlit
    assert abs(imported.opacity - 128 / 255) < 1e-6
    maps = model.maps[key]
    assert maps.occlusion is not None
    assert maps.occlusion_strength == 1.0
    assert all(item.transform is None for item in maps.sampling.values())

    # Plain materials stay opaque and single sided, as glTF defaults them.
    plain = load_model(_glb(tmp_path / "plain.glb", PBRMaterial()))
    imported = plain.materials[plain.nodes[0].mesh]
    assert imported is not None
    assert (imported.alpha_mode, imported.double_sided, imported.opacity) == (
        "opaque",
        False,
        1.0,
    )


def test_unlit_transform_and_second_uv_set_come_from_the_json(tmp_path: Path) -> None:
    material = PBRMaterial(baseColorTexture=_quadrants(), occlusionTexture=_quadrants())
    gltf = _gltf_files(tmp_path, material)

    def edit(document: dict) -> None:
        mat = document["materials"][0]
        mat["extensions"] = {"KHR_materials_unlit": {}}
        mat["pbrMetallicRoughness"]["baseColorTexture"]["extensions"] = {
            "KHR_texture_transform": {"offset": [0.5, 0.0], "scale": [2.0, 1.0]}
        }
        mat["occlusionTexture"]["texCoord"] = 1

    _edit_gltf(gltf, edit)
    model = load_model(gltf)
    key = model.nodes[0].mesh
    imported = model.materials[key]
    assert imported is not None
    assert imported.unlit
    maps = model.maps[key]
    assert maps.base_color is not None
    transform = maps.sampling["base_color"].transform
    assert transform is not None
    assert transform.offset == (0.5, 0.0)
    assert transform.scale == (2.0, 1.0)
    # The occlusion map samples UV set 1, which trimesh never reads.
    assert maps.occlusion is None
    assert any("occlusion map uses UV set 1" in w for w in model.warnings)


def test_percent_encoded_image_uris_load(tmp_path: Path) -> None:
    gltf = _gltf_files(tmp_path, PBRMaterial(baseColorTexture=_quadrants()))
    _quadrants().save(tmp_path / "paint new.png")

    def edit(document: dict) -> None:
        # Point the image at a file whose name needs percent-escaping.
        document["images"][0] = {"uri": "paint%20new.png"}

    _edit_gltf(gltf, edit)
    model = load_model(gltf)
    assert model.maps[model.nodes[0].mesh].base_color is not None
    assert model.warnings == ()


def test_import_objects_copies_surface_fields(tmp_path: Path) -> None:
    from scene_viewport.model_import import import_objects

    material = PBRMaterial(
        baseColorFactor=[1.0, 1.0, 1.0, 0.5], alphaMode="BLEND", doubleSided=True
    )
    model = load_model(_glb(tmp_path / "glass.glb", material))
    mesh = import_objects(model, set()).objects.meshes[0]
    assert mesh.alpha_mode == "blend"
    assert abs(mesh.opacity - 128 / 255) < 1e-6  # trimesh stores 8-bit factors
    assert mesh.double_sided is True
    assert not mesh.unlit


def test_surface_kwargs_and_texture_map_transform() -> None:
    kwargs = surface_kwargs
    base = MeshComponent(name="s", transform="t", kind="box")
    assert kwargs(base) == {
        "side": "both",
        "opacity": 1.0,
        "alpha_mode": "solid",
        "alpha_test": 0.0,
    }
    masked = base.model_copy(update={"alpha_mode": "mask", "alpha_cutoff": 0.3})
    assert kwargs(masked)["alpha_test"] == 0.3
    assert kwargs(masked)["alpha_mode"] == "solid"
    glass = base.model_copy(
        update={"alpha_mode": "blend", "opacity": 0.4, "double_sided": False}
    )
    assert kwargs(glass) == {
        "side": "front",
        "opacity": 0.4,
        "alpha_mode": "blend",
        "alpha_test": 0.0,
    }
    # The kwargs build real materials, lit and unlit.
    standard = pygfx.MeshStandardMaterial(**kwargs(masked))
    assert standard.alpha_test == 0.3
    assert pygfx.MeshBasicMaterial(**kwargs(glass)).side == "front"

    from scene_viewport.mesh.gltf_extras import MapSampling, TextureTransform
    from scene_viewport.mesh.importer import ImportedMaps

    texture = texture_from_rgba(np.zeros((2, 2, 4), dtype=np.uint8))
    maps = ImportedMaps(
        base_color=np.zeros((2, 2, 4), dtype=np.uint8),
        sampling={
            "base_color": MapSampling(
                uv_channel=1,
                wrap_s="clamp",
                wrap_t="mirror",
                transform=TextureTransform(offset=(0.5, 0.0), scale=(2.0, 2.0)),
            )
        },
    )
    moved = texture_map(texture, "base_color", maps)
    assert tuple(moved.offset) == (0.5, 0.0)
    assert tuple(moved.scale) == (2.0, 2.0)
    assert moved.uv_channel == 1
    assert (moved.wrap_s, moved.wrap_t) == ("clamp", "mirror")
    plain = texture_map(texture, "normal", maps)
    assert tuple(plain.offset) == (0.0, 0.0)
    assert plain.wrap_s == "repeat"


def test_occlusion_map_goes_on_the_material(tmp_path: Path) -> None:
    material = PBRMaterial(baseColorTexture=_quadrants(), occlusionTexture=_quadrants())
    path = _glb(tmp_path / "ao.glb", material)
    key = load_model(path).nodes[0].mesh
    component = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=path, source_mesh=key
    )
    target = _build(_host(), component)
    assert isinstance(target, pygfx.MeshStandardMaterial)
    assert target.ao_map is not None
    assert target.ao_map.texture.colorspace == "physical"
    assert target.ao_map_intensity == 1.0


def test_physical_extensions_build_a_physical_material(tmp_path: Path) -> None:
    material = PBRMaterial(
        baseColorTexture=_quadrants(),
        emissiveTexture=_quadrants(),
        emissiveFactor=[1, 1, 1],
    )
    gltf = _gltf_files(tmp_path, material)

    def edit(document: dict) -> None:
        mat = document["materials"][0]
        mat["extensions"] = {
            "KHR_materials_clearcoat": {
                "clearcoatFactor": 0.7,
                "clearcoatRoughnessFactor": 0.1,
                "clearcoatTexture": {"index": 0},
            },
            "KHR_materials_ior": {"ior": 1.8},
            "KHR_materials_sheen": {"sheenColorFactor": [0.2, 0.4, 0.6]},
            "KHR_materials_iridescence": {"iridescenceFactor": 0.5},
            "KHR_materials_anisotropy": {
                "anisotropyStrength": 0.3,
                "anisotropyRotation": 1.0,
            },
            "KHR_materials_specular": {
                "specularFactor": 0.5,
                "specularColorFactor": [1, 0, 0],
            },
            "KHR_materials_emissive_strength": {"emissiveStrength": 3.0},
        }

    _edit_gltf(gltf, edit)
    model = load_model(gltf)
    key = model.nodes[0].mesh
    surface = model.materials[key]
    assert surface is not None
    assert surface.extensions.is_physical
    maps = model.maps[key]
    assert maps.clearcoat is not None  # decoded through the glTF document
    component = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=gltf, source_mesh=key
    )
    target = _build(_host(), component)
    assert isinstance(target, pygfx.MeshPhysicalMaterial)
    assert target.clearcoat == pytest.approx(0.7)
    assert target.clearcoat_roughness == pytest.approx(0.1)
    assert target.clearcoat_map is not None
    assert target.ior == pytest.approx(1.8)
    assert target.sheen == 1.0
    assert target.iridescence == pytest.approx(0.5)
    assert target.anisotropy == pytest.approx(0.3)
    assert target.anisotropy_rotation == pytest.approx(1.0)
    assert target.specular_intensity == pytest.approx(0.5)
    assert target.emissive_intensity == pytest.approx(3.0)
    # A plain glTF material stays on the standard material.
    plain = load_model(_glb(tmp_path / "plain.glb", PBRMaterial()))
    plain_surface = plain.materials[plain.nodes[0].mesh]
    assert plain_surface is not None
    assert not plain_surface.extensions.is_physical


def test_second_uv_set_and_vertex_colors_are_read(tmp_path: Path) -> None:
    material = PBRMaterial(baseColorTexture=_quadrants(), occlusionTexture=_quadrants())
    gltf = _gltf_files(tmp_path, material)
    uv1 = np.array([[1, 1], [0, 1], [0, 0], [1, 0]], dtype=np.float32)
    colors = np.array(
        [[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255], [9, 9, 9, 255]],
        dtype=np.uint8,
    )
    (tmp_path / "extra.bin").write_bytes(uv1.tobytes() + colors.tobytes())

    def edit(document: dict) -> None:
        document["buffers"].append({"uri": "extra.bin", "byteLength": 32 + 16})
        buffer = len(document["buffers"]) - 1
        document["bufferViews"] += [
            {"buffer": buffer, "byteOffset": 0, "byteLength": 32},
            {"buffer": buffer, "byteOffset": 32, "byteLength": 16},
        ]
        views = len(document["bufferViews"])
        document["accessors"] += [
            {
                "bufferView": views - 2,
                "componentType": 5126,
                "count": 4,
                "type": "VEC2",
            },
            {
                "bufferView": views - 1,
                "componentType": 5121,
                "count": 4,
                "type": "VEC4",
                "normalized": True,
            },
        ]
        accessors = len(document["accessors"])
        attributes = document["meshes"][0]["primitives"][0]["attributes"]
        attributes["TEXCOORD_1"] = accessors - 2
        attributes["COLOR_0"] = accessors - 1
        document["materials"][0]["occlusionTexture"]["texCoord"] = 1

    _edit_gltf(gltf, edit)
    model = load_model(gltf)
    key = model.nodes[0].mesh
    buffers = model.meshes[key]
    assert buffers.texcoords1 is not None
    assert buffers.texcoords1.tolist() == uv1.tolist()
    assert buffers.colors is not None
    assert buffers.colors[0].tolist() == [1.0, 0.0, 0.0, 1.0]
    surface = model.materials[key]
    assert surface is not None
    assert surface.vertex_colors
    maps = model.maps[key]
    # The occlusion map now samples the second UV set instead of being dropped.
    assert maps.occlusion is not None
    assert maps.sampling["occlusion"].uv_channel == 1
    assert model.warnings == ()
    component = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=gltf, source_mesh=key
    )
    textured = _build(_host(), component)
    assert isinstance(textured, pygfx.MeshStandardMaterial)
    assert textured.ao_map is not None
    assert textured.ao_map.uv_channel == 1
    # Without a base color texture in use, the vertex colors show.
    shaded = _build(_host(), component, textured=False)
    assert shaded.color_mode == "vertex"
    assert textured.color_mode != "vertex"

    from scene_viewport.mesh.geometry import to_geometry

    geometry = to_geometry(buffers)
    assert geometry.texcoords1 is not None
    assert geometry.colors is not None


def test_emissive_factor_applies_without_a_texture(tmp_path: Path) -> None:
    """EmissiveStrengthTest-style files: a factor and strength, no emissive map."""
    gltf = _gltf_files(tmp_path, PBRMaterial(emissiveFactor=[1.0, 0.5, 0.0]))

    def edit(document: dict) -> None:
        document["materials"][0]["extensions"] = {
            "KHR_materials_emissive_strength": {"emissiveStrength": 6.0}
        }

    _edit_gltf(gltf, edit)
    model = load_model(gltf)
    key = model.nodes[0].mesh
    surface = model.materials[key]
    assert surface is not None
    assert surface.emissive == (1.0, 0.5, 0.0)
    component = MeshComponent(
        name="quadShape", transform="quad", kind="file", source=gltf, source_mesh=key
    )
    shaded = _build(_host(), component, textured=False)
    assert isinstance(shaded, pygfx.MeshStandardMaterial)
    assert shaded.emissive_intensity == pytest.approx(6.0)
    assert tuple(shaded.emissive.rgb) != (0.0, 0.0, 0.0)
    assert shaded.emissive_map is None
