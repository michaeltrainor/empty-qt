"""Read the glTF material details trimesh drops: unlit, UV sets, transforms.

trimesh keeps a material's factors, alpha mode, sides, and images, but not
``KHR_materials_unlit``, each texture's ``texCoord`` set, or
``KHR_texture_transform``. This module reads those straight from the glTF
JSON and keys them by the geometry names trimesh assigns, so the importer
can attach them to the right mesh.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from trimesh.util import unique_name

from scene_viewport.mesh.types import MapKind

_GLB_MAGIC = b"glTF"
_GLB_JSON_CHUNK = 0x4E4F534A

#: glTF texture slot → map kind. ``pbrMetallicRoughness`` slots are nested.
_PBR_SLOTS: dict[str, MapKind] = {
    "baseColorTexture": "base_color",
    "metallicRoughnessTexture": "metallic_roughness",
}
_MATERIAL_SLOTS: dict[str, MapKind] = {
    "normalTexture": "normal",
    "emissiveTexture": "emissive",
    "occlusionTexture": "occlusion",
}


@dataclass(frozen=True, slots=True)
class TextureTransform:
    """A ``KHR_texture_transform``: UVs are offset, rotated, then scaled.

    Args:
        offset: UV translation.
        rotation: Rotation in radians, counter-clockwise in UV space.
        scale: UV scale.
    """

    offset: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    scale: tuple[float, float] = (1.0, 1.0)

    @property
    def is_identity(self) -> bool:
        """Whether the transform leaves UVs unchanged."""
        return (
            self.offset == (0.0, 0.0)
            and self.rotation == 0.0
            and self.scale == (1.0, 1.0)
        )


@dataclass(frozen=True, slots=True)
class PrimitiveExtras:
    """Per-primitive material details read from the glTF JSON.

    Args:
        unlit: The material uses ``KHR_materials_unlit``.
        uv_channels: Texture coordinate set each map samples (``texCoord``).
            Missing maps use set 0.
        transforms: ``KHR_texture_transform`` per map, when present.
        occlusion_strength: ``occlusionTexture.strength``.
    """

    unlit: bool = False
    uv_channels: Mapping[MapKind, int] = field(default_factory=dict)
    transforms: Mapping[MapKind, TextureTransform] = field(default_factory=dict)
    occlusion_strength: float = 1.0


def read_primitive_extras(path: Path) -> dict[str, PrimitiveExtras]:
    """Return material extras per trimesh geometry name for a glTF file.

    Other formats, and unreadable files, give an empty mapping; loading
    reports the real error.

    Args:
        path: ``.gltf`` or ``.glb`` file.

    Returns:
        Extras keyed by the geometry name trimesh gives each primitive:
        the mesh name, made unique with ``_1``, ``_2`` suffixes in file
        order, as :func:`trimesh.util.unique_name` does.
    """
    suffix = path.suffix.lower()
    if suffix not in (".gltf", ".glb"):
        return {}
    try:
        document = (
            _glb_json(path)
            if suffix == ".glb"
            else json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    materials = _entries(document, "materials")
    by_material = [_material_extras(material) for material in materials]
    result: dict[str, PrimitiveExtras] = {}
    counts: dict[str | None, int] = {}
    for mesh in _entries(document, "meshes"):
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list):
            continue
        for primitive in primitives:
            # Mirror trimesh's naming so the keys line up with its geometry.
            name = unique_name(str(mesh.get("name", "GLTF")), result, counts=counts)
            index = primitive.get("material") if isinstance(primitive, dict) else None
            if isinstance(index, int) and 0 <= index < len(by_material):
                result[name] = by_material[index]
            else:
                result[name] = PrimitiveExtras()
    return result


def _entries(document: Mapping[str, object], key: str) -> list[dict[str, object]]:
    value = document.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _material_extras(material: Mapping[str, object]) -> PrimitiveExtras:
    extensions = material.get("extensions")
    unlit = isinstance(extensions, dict) and "KHR_materials_unlit" in extensions
    channels: dict[MapKind, int] = {}
    transforms: dict[MapKind, TextureTransform] = {}
    strength = 1.0

    def read_slot(info: object, kind: MapKind) -> None:
        if not isinstance(info, dict):
            return
        channel = info.get("texCoord", 0)
        if isinstance(channel, int) and channel != 0:
            channels[kind] = channel
        slot_extensions = info.get("extensions")
        if isinstance(slot_extensions, dict):
            transform = slot_extensions.get("KHR_texture_transform")
            if isinstance(transform, dict):
                parsed = _transform(transform)
                if not parsed.is_identity:
                    transforms[kind] = parsed
                override = transform.get("texCoord")
                if isinstance(override, int):
                    if override != 0:
                        channels[kind] = override
                    else:
                        channels.pop(kind, None)

    pbr = material.get("pbrMetallicRoughness")
    if isinstance(pbr, dict):
        for slot, kind in _PBR_SLOTS.items():
            read_slot(pbr.get(slot), kind)
    for slot, kind in _MATERIAL_SLOTS.items():
        read_slot(material.get(slot), kind)
    occlusion = material.get("occlusionTexture")
    if isinstance(occlusion, dict):
        value = occlusion.get("strength", 1.0)
        if isinstance(value, (int, float)):
            strength = float(min(max(value, 0.0), 1.0))
    return PrimitiveExtras(
        unlit=unlit,
        uv_channels=channels,
        transforms=transforms,
        occlusion_strength=strength,
    )


def _transform(value: Mapping[str, object]) -> TextureTransform:
    offset = _pair(value.get("offset"), (0.0, 0.0))
    scale = _pair(value.get("scale"), (1.0, 1.0))
    rotation = value.get("rotation", 0.0)
    return TextureTransform(
        offset=offset,
        rotation=float(rotation) if isinstance(rotation, (int, float)) else 0.0,
        scale=scale,
    )


def _pair(value: object, default: tuple[float, float]) -> tuple[float, float]:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) for item in value)
    ):
        return (float(value[0]), float(value[1]))
    return default


def _glb_json(path: Path) -> object:
    """Return the JSON chunk of a binary glTF."""
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) < 20 or header[:4] != _GLB_MAGIC:
            msg = "not a GLB file"
            raise ValueError(msg)
        length, kind = struct.unpack("<II", header[12:20])
        if kind != _GLB_JSON_CHUNK:
            msg = "GLB has no JSON chunk"
            raise ValueError(msg)
        return json.loads(stream.read(length).decode("utf-8"))
