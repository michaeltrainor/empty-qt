"""glTF data trimesh drops: unlit, UV sets, samplers, transforms, extensions."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import numpy as np

from scene_viewport.mesh.gltf_extras import (
    GltfDocument,
    SurfaceExtensions,
    TextureTransform,
    read_primitive_extras,
)

DOCUMENT = {
    "asset": {"version": "2.0"},
    "samplers": [{"wrapS": 33071, "wrapT": 33648}],
    "textures": [
        {"source": 0, "sampler": 0},
        {"source": 0},
        {"source": 0},
        {"source": 0},
    ],
    "images": [{"uri": "paint.png"}],
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
            "extensions": {
                "KHR_materials_clearcoat": {
                    "clearcoatFactor": 0.8,
                    "clearcoatRoughnessFactor": 0.2,
                    "clearcoatTexture": {"index": 1},
                },
                "KHR_materials_ior": {"ior": 1.8},
                "KHR_materials_emissive_strength": {"emissiveStrength": 4.0},
                "KHR_materials_sheen": {"sheenColorFactor": [1, 0, 0]},
                "KHR_materials_iridescence": {
                    "iridescenceFactor": 1.0,
                    "iridescenceThicknessMaximum": 800,
                },
            },
        },
        {"name": "Flat", "extensions": {"KHR_materials_unlit": {}}},
    ],
    "meshes": [
        {
            "name": "Cube",
            "primitives": [
                {
                    "attributes": {"POSITION": 0, "TEXCOORD_1": 3, "COLOR_0": 4},
                    "material": 0,
                },
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
    assert painted.texcoords1 == 3
    assert painted.colors == 4
    base = painted.maps["base_color"]
    assert base.transform == TextureTransform(
        offset=(0.5, 0.25), rotation=1.5707963, scale=(2.0, 2.0)
    )
    assert (base.wrap_s, base.wrap_t) == ("clamp", "mirror")
    # An identity transform is dropped; a texCoord override is honoured.
    assert painted.maps["metallic_roughness"].transform is None
    assert painted.maps["metallic_roughness"].wrap_s == "repeat"
    assert painted.maps["occlusion"].uv_channel == 1
    assert painted.maps["normal"].uv_channel == 1
    assert painted.maps["clearcoat"].texture == 1
    assert painted.occlusion_strength == 0.6
    ext = painted.extensions
    assert ext.is_physical
    assert (ext.clearcoat, ext.clearcoat_roughness, ext.ior) == (0.8, 0.2, 1.8)
    assert ext.emissive_strength == 4.0
    assert ext.sheen_color == (1.0, 0.0, 0.0)
    assert ext.iridescence == 1.0
    assert ext.iridescence_thickness == (100.0, 800.0)

    flat = extras["Cube_1"]
    assert flat.unlit
    assert flat.maps == {}
    assert not flat.extensions.is_physical
    assert flat.texcoords1 is None
    # No material, and a material index out of range, give defaults.
    assert not extras["Cube_2"].unlit
    assert extras["GLTF"].occlusion_strength == 1.0
    assert SurfaceExtensions() == extras["GLTF"].extensions


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
    assert GltfDocument.open(obj) is None
    bad = tmp_path / "bad.gltf"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert read_primitive_extras(bad) == {}


def test_texture_transform_identity() -> None:
    assert TextureTransform().is_identity
    assert not TextureTransform(offset=(0.1, 0.0)).is_identity
    assert not TextureTransform(rotation=0.5).is_identity


def _accessor_document(tmp_path: Path) -> GltfDocument:
    """A .gltf with one external buffer: float UVs, normalized u16 UVs, u8 RGB."""
    uv = np.array([[0.0, 1.0], [0.5, 0.25]], dtype=np.float32)
    uv16 = np.array([[0, 65535], [32768, 0]], dtype=np.uint16)
    # Strided RGB: 3 bytes used of a 4-byte stride.
    rgb = np.array([[255, 0, 0, 9], [0, 255, 0, 9]], dtype=np.uint8)
    blob = uv.tobytes() + uv16.tobytes() + rgb.tobytes()
    (tmp_path / "data.bin").write_bytes(blob)
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": "data.bin", "byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 16},
            {"buffer": 0, "byteOffset": 16, "byteLength": 8},
            {"buffer": 0, "byteOffset": 24, "byteLength": 8, "byteStride": 4},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 2, "type": "VEC2"},
            {
                "bufferView": 1,
                "componentType": 5123,
                "count": 2,
                "type": "VEC2",
                "normalized": True,
            },
            {
                "bufferView": 2,
                "componentType": 5121,
                "count": 2,
                "type": "VEC3",
                "normalized": True,
            },
            {"componentType": 5126, "count": 2, "type": "VEC2", "sparse": {}},
            {"bufferView": 0, "componentType": 5126, "count": 9, "type": "VEC2"},
        ],
        "textures": [
            {"source": 0},
            {"extensions": {"KHR_texture_basisu": {"source": 1}}},
        ],
        "images": [
            {
                "uri": "data:image/png;base64,"
                + base64.b64encode(_png_bytes()).decode("ascii")
            },
            {"uri": "paint.ktx2"},
        ],
    }
    path = tmp_path / "scene.gltf"
    path.write_text(json.dumps(document), encoding="utf-8")
    opened = GltfDocument.open(path)
    assert opened is not None
    return opened


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_accessors_decode_floats_normalized_ints_and_strides(tmp_path: Path) -> None:
    document = _accessor_document(tmp_path)
    floats = document.accessor_array(0)
    assert floats is not None
    assert floats.tolist() == [[0.0, 1.0], [0.5, 0.25]]
    normalized = document.accessor_array(1)
    assert normalized is not None
    assert np.allclose(normalized, [[0.0, 1.0], [32768 / 65535, 0.0]])
    rgb = document.accessor_array(2)
    assert rgb is not None
    assert rgb.tolist() == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    # Sparse, out of range, and overrunning accessors are refused.
    assert document.accessor_array(3) is None
    assert document.accessor_array(4) is None
    assert document.accessor_array(42) is None


def test_images_decode_data_uris_and_skip_basisu(tmp_path: Path) -> None:
    document = _accessor_document(tmp_path)
    pixels = document.image_rgba(0)
    assert pixels is not None
    assert pixels.shape == (2, 2, 4)
    assert pixels[0, 0].tolist() == [10, 20, 30, 255]
    assert document.image_rgba(0) is pixels  # cached
    assert document.image_rgba(1) is None  # KTX2 without a core source
    assert document.image_rgba(5) is None


def test_glb_binary_chunk_feeds_buffer_zero(tmp_path: Path) -> None:
    uv = np.array([[0.25, 0.75]], dtype=np.float32)
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 8}],
        "bufferViews": [{"buffer": 0, "byteLength": 8}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 1, "type": "VEC2"}
        ],
    }
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    binary = uv.tobytes()
    total = 12 + 8 + len(payload) + 8 + len(binary)
    glb = tmp_path / "scene.glb"
    glb.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(payload), 0x4E4F534A)
        + payload
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    opened = GltfDocument.open(glb)
    assert opened is not None
    values = opened.accessor_array(0)
    assert values is not None
    assert values.tolist() == [[0.25, 0.75]]
