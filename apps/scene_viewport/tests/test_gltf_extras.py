"""glTF material extras trimesh drops: unlit, UV sets, texture transforms."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from scene_viewport.mesh.gltf_extras import TextureTransform, read_primitive_extras

DOCUMENT = {
    "asset": {"version": "2.0"},
    "materials": [
        {
            "name": "Painted",
            "pbrMetallicRoughness": {
                "baseColorTexture": {
                    "index": 0,
                    "extensions": {
                        "KHR_texture_transform": {
                            "offset": [0.5, 0.25],
                            "rotation": 1.5707963,
                            "scale": [2, 2],
                        }
                    },
                },
                "metallicRoughnessTexture": {
                    "index": 1,
                    "extensions": {"KHR_texture_transform": {"scale": [1, 1]}},
                },
            },
            "occlusionTexture": {"index": 2, "texCoord": 1, "strength": 0.6},
            "normalTexture": {
                "index": 3,
                "extensions": {"KHR_texture_transform": {"texCoord": 1}},
            },
        },
        {"name": "Flat", "extensions": {"KHR_materials_unlit": {}}},
    ],
    "meshes": [
        {
            "name": "Cube",
            "primitives": [
                {"attributes": {"POSITION": 0}, "material": 0},
                {"attributes": {"POSITION": 0}, "material": 1},
            ],
        },
        {"name": "Cube", "primitives": [{"attributes": {"POSITION": 0}}]},
        {"primitives": [{"attributes": {"POSITION": 0}, "material": 7}]},
    ],
}


def test_extras_follow_trimesh_geometry_names(tmp_path: Path) -> None:
    gltf = tmp_path / "scene.gltf"
    gltf.write_text(json.dumps(DOCUMENT), encoding="utf-8")
    extras = read_primitive_extras(gltf)
    # Mesh "Cube" has two primitives, then another mesh also named "Cube".
    assert list(extras) == ["Cube", "Cube_1", "Cube_2", "GLTF"]

    painted = extras["Cube"]
    assert not painted.unlit
    assert painted.transforms == {
        "base_color": TextureTransform(
            offset=(0.5, 0.25), rotation=1.5707963, scale=(2.0, 2.0)
        )
    }
    # An identity transform is dropped; a texCoord override is honoured.
    assert painted.uv_channels == {"occlusion": 1, "normal": 1}
    assert painted.occlusion_strength == 0.6

    assert extras["Cube_1"].unlit
    assert extras["Cube_1"].transforms == {}
    # No material, and a material index out of range, give defaults.
    assert not extras["Cube_2"].unlit
    assert extras["GLTF"].occlusion_strength == 1.0


def test_extras_read_glb_and_ignore_other_files(tmp_path: Path) -> None:
    payload = json.dumps(DOCUMENT).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(payload))
    glb = tmp_path / "scene.glb"
    glb.write_bytes(header + struct.pack("<II", len(payload), 0x4E4F534A) + payload)
    assert read_primitive_extras(glb)["Cube_1"].unlit

    assert read_primitive_extras(tmp_path / "missing.gltf") == {}
    obj = tmp_path / "quad.obj"
    obj.write_text("v 0 0 0\n", encoding="utf-8")
    assert read_primitive_extras(obj) == {}
    bad = tmp_path / "bad.gltf"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert read_primitive_extras(bad) == {}


def test_texture_transform_identity() -> None:
    assert TextureTransform().is_identity
    assert not TextureTransform(offset=(0.1, 0.0)).is_identity
    assert not TextureTransform(rotation=0.5).is_identity
