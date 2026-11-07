"""Read the glTF data trimesh drops: material extensions, UV sets, samplers.

trimesh keeps a material's core factors, alpha mode, sides, and the core
texture images, but not ``KHR_materials_unlit``, the ``KHR_materials_*``
surface extensions, each texture's ``texCoord`` set, sampler wrap modes,
``KHR_texture_transform``, a second UV set, or vertex colors on textured
meshes. :class:`GltfDocument` reads those straight from the glTF JSON and
buffers, keyed by the geometry names trimesh assigns, so the importer can
attach them to the right mesh.
"""

from __future__ import annotations

import base64
import io
import json
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from trimesh.util import unique_name

from scene_viewport.mesh.types import MapKind

_GLB_MAGIC = b"glTF"
_GLB_JSON_CHUNK = 0x4E4F534A
_GLB_BIN_CHUNK = 0x004E4942
_REPEAT = 10497

#: glTF sampler wrap constants → pygfx ``TextureMap`` wrap names.
_WRAP_MODES = {10497: "repeat", 33071: "clamp", 33648: "mirror"}
#: glTF accessor component types → numpy dtypes.
_COMPONENT_DTYPES = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_TYPE_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

#: Core texture slots → map kind. ``pbrMetallicRoughness`` slots are nested.
_PBR_SLOTS: dict[str, MapKind] = {
    "baseColorTexture": "base_color",
    "metallicRoughnessTexture": "metallic_roughness",
}
_MATERIAL_SLOTS: dict[str, MapKind] = {
    "normalTexture": "normal",
    "emissiveTexture": "emissive",
    "occlusionTexture": "occlusion",
}
#: ``KHR_materials_*`` texture slots → map kind, by extension name.
_EXTENSION_SLOTS: dict[str, dict[str, MapKind]] = {
    "KHR_materials_clearcoat": {
        "clearcoatTexture": "clearcoat",
        "clearcoatRoughnessTexture": "clearcoat_roughness",
        "clearcoatNormalTexture": "clearcoat_normal",
    },
    "KHR_materials_specular": {
        "specularTexture": "specular",
        "specularColorTexture": "specular_color",
    },
    "KHR_materials_sheen": {
        "sheenColorTexture": "sheen_color",
        "sheenRoughnessTexture": "sheen_roughness",
    },
    "KHR_materials_iridescence": {
        "iridescenceTexture": "iridescence",
        "iridescenceThicknessTexture": "iridescence_thickness",
    },
    "KHR_materials_anisotropy": {"anisotropyTexture": "anisotropy"},
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
class MapSampling:
    """How one texture map is sampled.

    Args:
        texture: Index into the glTF ``textures`` array.
        uv_channel: Texture coordinate set (``texCoord``).
        wrap_s: Horizontal wrap: ``repeat``, ``clamp``, or ``mirror``.
        wrap_t: Vertical wrap.
        transform: ``KHR_texture_transform``, or ``None`` for identity.
    """

    texture: int = -1
    uv_channel: int = 0
    wrap_s: str = "repeat"
    wrap_t: str = "repeat"
    transform: TextureTransform | None = None


@dataclass(frozen=True, slots=True)
class SurfaceExtensions:
    """``KHR_materials_*`` factors pygfx's physical material can render.

    All default to the glTF defaults, which leave the material unchanged.

    Args:
        emissive_strength: ``KHR_materials_emissive_strength``.
        ior: ``KHR_materials_ior``.
        clearcoat: ``KHR_materials_clearcoat`` factor.
        clearcoat_roughness: Clearcoat roughness factor.
        specular: ``KHR_materials_specular`` strength factor.
        specular_color: Specular color factor, linear RGB.
        sheen_color: ``KHR_materials_sheen`` color factor, linear RGB.
        sheen_roughness: Sheen roughness factor.
        iridescence: ``KHR_materials_iridescence`` factor.
        iridescence_ior: Thin-film index of refraction.
        iridescence_thickness: Thin-film thickness range in nanometres.
        anisotropy: ``KHR_materials_anisotropy`` strength.
        anisotropy_rotation: Anisotropy rotation in radians.
    """

    emissive_strength: float = 1.0
    ior: float = 1.5
    clearcoat: float = 0.0
    clearcoat_roughness: float = 0.0
    specular: float = 1.0
    specular_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    sheen_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    sheen_roughness: float = 0.0
    iridescence: float = 0.0
    iridescence_ior: float = 1.3
    iridescence_thickness: tuple[float, float] = (100.0, 400.0)
    anisotropy: float = 0.0
    anisotropy_rotation: float = 0.0

    @property
    def is_physical(self) -> bool:
        """Whether any factor needs pygfx's physical material."""
        return (
            self.ior != 1.5
            or self.clearcoat > 0.0
            or self.specular != 1.0
            or self.specular_color != (1.0, 1.0, 1.0)
            or any(self.sheen_color)
            or self.iridescence > 0.0
            or self.anisotropy > 0.0
        )


@dataclass(frozen=True, slots=True)
class PrimitiveExtras:
    """Per-primitive details read from the glTF JSON.

    Args:
        unlit: The material uses ``KHR_materials_unlit``.
        maps: Sampling of every texture the material names, by map kind.
        extensions: Surface extension factors.
        occlusion_strength: ``occlusionTexture.strength``.
        texcoords1: Accessor index of ``TEXCOORD_1``, or ``None``.
        colors: Accessor index of ``COLOR_0``, or ``None``.
    """

    unlit: bool = False
    maps: Mapping[MapKind, MapSampling] = field(default_factory=dict)
    extensions: SurfaceExtensions = field(default_factory=SurfaceExtensions)
    occlusion_strength: float = 1.0
    texcoords1: int | None = None
    colors: int | None = None


class GltfDocument:
    """A glTF or GLB file's JSON plus lazy access to its buffers and images.

    Args:
        path: ``.gltf`` or ``.glb`` file.

    Raises:
        OSError: When the file can't be read.
        ValueError: When the file isn't glTF JSON or a GLB container.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._binary_chunk: bytes | None = None
        if path.suffix.lower() == ".glb":
            document, self._binary_chunk = _read_glb(path)
        else:
            document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            msg = "glTF document must be a JSON object"
            raise ValueError(msg)
        self.document: dict[str, object] = document
        self._buffers: dict[int, bytes] = {}
        self._images: dict[int, NDArray[np.uint8] | None] = {}

    @classmethod
    def open(cls, path: Path) -> GltfDocument | None:
        """Return a document for a glTF file, or ``None`` for other files.

        Unreadable or malformed glTF gives ``None`` too; loading reports
        the real error.
        """
        if path.suffix.lower() not in (".gltf", ".glb"):
            return None
        try:
            return cls(path)
        except (OSError, ValueError):
            return None

    def entries(self, key: str) -> list[dict[str, object]]:
        """Return the top-level array ``key`` as dicts, skipping other items."""
        value = self.document.get(key)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def primitive_extras(self) -> dict[str, PrimitiveExtras]:
        """Return extras per trimesh geometry name.

        Keys are the geometry names trimesh gives each primitive: the mesh
        name, made unique with ``_1``, ``_2`` suffixes in file order, as
        :func:`trimesh.util.unique_name` does.
        """
        samplers = self.entries("samplers")
        materials = [
            self._material_extras(material, samplers)
            for material in self.entries("materials")
        ]
        result: dict[str, PrimitiveExtras] = {}
        counts: dict[str | None, int] = {}
        for mesh in self.entries("meshes"):
            primitives = mesh.get("primitives")
            if not isinstance(primitives, list):
                continue
            for primitive in primitives:
                # Mirror trimesh's naming so the keys line up with its geometry.
                name = unique_name(str(mesh.get("name", "GLTF")), result, counts=counts)
                if not isinstance(primitive, dict):
                    result[name] = PrimitiveExtras()
                    continue
                index = primitive.get("material")
                base = (
                    materials[index]
                    if isinstance(index, int) and 0 <= index < len(materials)
                    else PrimitiveExtras()
                )
                attributes = primitive.get("attributes")
                attributes = attributes if isinstance(attributes, dict) else {}
                result[name] = PrimitiveExtras(
                    unlit=base.unlit,
                    maps=base.maps,
                    extensions=base.extensions,
                    occlusion_strength=base.occlusion_strength,
                    texcoords1=_int_or_none(attributes.get("TEXCOORD_1")),
                    colors=_int_or_none(attributes.get("COLOR_0")),
                )
        return result

    def accessor_array(self, index: int) -> NDArray[np.float32] | None:
        """Decode accessor ``index`` as float32 ``(count, components)``.

        Normalized integer data is scaled to ``[0, 1]`` (or ``[-1, 1]``).
        Sparse accessors, accessors without a buffer view (Draco or Meshopt
        placeholders), and unreadable buffers give ``None``.
        """
        accessors = self.entries("accessors")
        if not 0 <= index < len(accessors):
            return None
        accessor = accessors[index]
        if "sparse" in accessor or "bufferView" not in accessor:
            return None
        dtype = _COMPONENT_DTYPES.get(_int(accessor.get("componentType")))
        size = _TYPE_SIZES.get(str(accessor.get("type", "")))
        count = _int(accessor.get("count"))
        if dtype is None or size is None or count <= 0:
            return None
        views = self.entries("bufferViews")
        view_index = _int(accessor.get("bufferView"))
        if not 0 <= view_index < len(views):
            return None
        view = views[view_index]
        data = self._buffer(_int(view.get("buffer")))
        if data is None:
            return None
        item = np.dtype(dtype).itemsize * size
        stride = _int(view.get("byteStride")) or item
        start = _int(view.get("byteOffset")) + _int(accessor.get("byteOffset"))
        end = start + stride * (count - 1) + item
        if end > len(data):
            return None
        raw = np.frombuffer(data, dtype=np.uint8, count=end - start, offset=start)
        rows = np.lib.stride_tricks.as_strided(
            raw, shape=(count, item), strides=(stride, 1)
        )
        values = np.ascontiguousarray(rows).view(dtype).reshape(count, size)
        result = values.astype(np.float32)
        if accessor.get("normalized") and np.issubdtype(dtype, np.integer):
            info = np.iinfo(dtype)
            result = result / float(info.max)
            if info.min < 0:
                result = np.maximum(result, -1.0)
        return result

    def image_rgba(self, texture: int) -> NDArray[np.uint8] | None:
        """Decode the image of texture ``texture`` as RGBA, top row first.

        Textures without a core ``source`` (KTX2 via ``KHR_texture_basisu``)
        and undecodable images give ``None``.
        """
        textures = self.entries("textures")
        if not 0 <= texture < len(textures):
            return None
        source = textures[texture].get("source")
        if not isinstance(source, int):
            return None
        if source not in self._images:
            self._images[source] = self._decode_image(source)
        return self._images[source]

    def _decode_image(self, index: int) -> NDArray[np.uint8] | None:
        images = self.entries("images")
        if not 0 <= index < len(images):
            return None
        image = images[index]
        payload: bytes | None = None
        uri = image.get("uri")
        if isinstance(uri, str) and uri:
            payload = self._uri_bytes(uri)
        elif "bufferView" in image:
            views = self.entries("bufferViews")
            view_index = _int(image.get("bufferView"))
            if 0 <= view_index < len(views):
                view = views[view_index]
                data = self._buffer(_int(view.get("buffer")))
                if data is not None:
                    start = _int(view.get("byteOffset"))
                    payload = data[start : start + _int(view.get("byteLength"))]
        if not payload:
            return None
        try:
            with Image.open(io.BytesIO(payload)) as decoded:
                return np.array(decoded.convert("RGBA"), dtype=np.uint8)
        except Exception:
            # Pillow raises many types for unsupported or broken images.
            return None

    def _buffer(self, index: int) -> bytes | None:
        if index in self._buffers:
            return self._buffers[index]
        buffers = self.entries("buffers")
        data: bytes | None = None
        if 0 <= index < len(buffers):
            uri = buffers[index].get("uri")
            if isinstance(uri, str) and uri:
                data = self._uri_bytes(uri)
            elif index == 0:
                data = self._binary_chunk
        if data is not None:
            self._buffers[index] = data
        return data

    def _uri_bytes(self, uri: str) -> bytes | None:
        if uri.startswith("data:"):
            _header, _comma, encoded = uri.partition(",")
            try:
                return base64.b64decode(encoded)
            except ValueError:
                return None
        if len(urlsplit(uri).scheme) > 1:
            return None  # Remote: never fetched.
        candidate = (self.path.parent / unquote(uri)).resolve()
        try:
            candidate.relative_to(self.path.parent.resolve())
            return candidate.read_bytes()
        except (OSError, ValueError):
            return None

    def _material_extras(
        self,
        material: Mapping[str, object],
        samplers: list[dict[str, object]],
    ) -> PrimitiveExtras:
        extensions = material.get("extensions")
        extensions = extensions if isinstance(extensions, dict) else {}
        maps: dict[MapKind, MapSampling] = {}

        def read_slot(info: object, kind: MapKind) -> None:
            if not isinstance(info, dict):
                return
            maps[kind] = self._sampling(info, samplers)

        pbr = material.get("pbrMetallicRoughness")
        if isinstance(pbr, dict):
            for slot, kind in _PBR_SLOTS.items():
                read_slot(pbr.get(slot), kind)
        for slot, kind in _MATERIAL_SLOTS.items():
            read_slot(material.get(slot), kind)
        for extension, slots in _EXTENSION_SLOTS.items():
            values = extensions.get(extension)
            if isinstance(values, dict):
                for slot, kind in slots.items():
                    read_slot(values.get(slot), kind)

        strength = 1.0
        occlusion = material.get("occlusionTexture")
        if isinstance(occlusion, dict):
            strength = _clamp(occlusion.get("strength"), 1.0)
        return PrimitiveExtras(
            unlit="KHR_materials_unlit" in extensions,
            maps=maps,
            extensions=_surface_extensions(extensions),
            occlusion_strength=strength,
        )

    def _sampling(
        self, info: Mapping[str, object], samplers: list[dict[str, object]]
    ) -> MapSampling:
        texture = _int(info.get("index"), -1)
        channel = _int(info.get("texCoord"))
        transform: TextureTransform | None = None
        slot_extensions = info.get("extensions")
        if isinstance(slot_extensions, dict):
            value = slot_extensions.get("KHR_texture_transform")
            if isinstance(value, dict):
                parsed = _transform(value)
                transform = None if parsed.is_identity else parsed
                override = value.get("texCoord")
                if isinstance(override, int):
                    channel = override
        wrap_s = wrap_t = "repeat"
        textures = self.entries("textures")
        if 0 <= texture < len(textures):
            sampler_index = textures[texture].get("sampler")
            if isinstance(sampler_index, int) and 0 <= sampler_index < len(samplers):
                sampler = samplers[sampler_index]
                wrap_s = _WRAP_MODES.get(_int(sampler.get("wrapS"), _REPEAT), "repeat")
                wrap_t = _WRAP_MODES.get(_int(sampler.get("wrapT"), _REPEAT), "repeat")
        return MapSampling(
            texture=texture,
            uv_channel=channel,
            wrap_s=wrap_s,
            wrap_t=wrap_t,
            transform=transform,
        )


def read_primitive_extras(path: Path) -> dict[str, PrimitiveExtras]:
    """Return material extras per trimesh geometry name for a glTF file.

    Other formats, and unreadable files, give an empty mapping.

    Args:
        path: ``.gltf`` or ``.glb`` file.
    """
    document = GltfDocument.open(path)
    return {} if document is None else document.primitive_extras()


def _surface_extensions(extensions: Mapping[str, object]) -> SurfaceExtensions:
    def section(name: str) -> Mapping[str, object]:
        value = extensions.get(name)
        return value if isinstance(value, dict) else {}

    emissive = section("KHR_materials_emissive_strength")
    ior = section("KHR_materials_ior")
    clearcoat = section("KHR_materials_clearcoat")
    specular = section("KHR_materials_specular")
    sheen = section("KHR_materials_sheen")
    iridescence = section("KHR_materials_iridescence")
    anisotropy = section("KHR_materials_anisotropy")
    thickness_min = _number(iridescence.get("iridescenceThicknessMinimum"), 100.0)
    thickness_max = _number(iridescence.get("iridescenceThicknessMaximum"), 400.0)
    return SurfaceExtensions(
        emissive_strength=max(_number(emissive.get("emissiveStrength"), 1.0), 0.0),
        ior=max(_number(ior.get("ior"), 1.5), 1.0),
        clearcoat=_clamp(clearcoat.get("clearcoatFactor"), 0.0),
        clearcoat_roughness=_clamp(clearcoat.get("clearcoatRoughnessFactor"), 0.0),
        specular=_clamp(specular.get("specularFactor"), 1.0),
        specular_color=_rgb(specular.get("specularColorFactor"), (1.0, 1.0, 1.0)),
        sheen_color=_rgb(sheen.get("sheenColorFactor"), (0.0, 0.0, 0.0)),
        sheen_roughness=_clamp(sheen.get("sheenRoughnessFactor"), 0.0),
        iridescence=_clamp(iridescence.get("iridescenceFactor"), 0.0),
        iridescence_ior=max(_number(iridescence.get("iridescenceIor"), 1.3), 1.0),
        iridescence_thickness=(thickness_min, thickness_max),
        anisotropy=_clamp(anisotropy.get("anisotropyStrength"), 0.0),
        anisotropy_rotation=_number(anisotropy.get("anisotropyRotation"), 0.0),
    )


def _transform(value: Mapping[str, object]) -> TextureTransform:
    return TextureTransform(
        offset=_pair(value.get("offset"), (0.0, 0.0)),
        rotation=_number(value.get("rotation"), 0.0),
        scale=_pair(value.get("scale"), (1.0, 1.0)),
    )


def _number(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _clamp(value: object, default: float) -> float:
    return min(max(_number(value, default), 0.0), 1.0)


def _int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _pair(value: object, default: tuple[float, float]) -> tuple[float, float]:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) for item in value)
    ):
        return (float(value[0]), float(value[1]))
    return default


def _rgb(
    value: object, default: tuple[float, float, float]
) -> tuple[float, float, float]:
    if (
        isinstance(value, list)
        and len(value) >= 3
        and all(isinstance(item, (int, float)) for item in value[:3])
    ):
        return (float(value[0]), float(value[1]), float(value[2]))
    return default


def _read_glb(path: Path) -> tuple[object, bytes | None]:
    """Return the JSON chunk and the BIN chunk of a binary glTF file."""
    return split_glb(path.read_bytes())


def split_glb(data: bytes) -> tuple[dict[str, object], bytes | None]:
    """Split a GLB container into its JSON document and first BIN chunk.

    Args:
        data: The whole file.

    Returns:
        The parsed JSON and the binary chunk, or ``None`` when there is none.

    Raises:
        ValueError: When ``data`` is not a GLB container or has no JSON.
    """
    if len(data) < 12 or data[:4] != _GLB_MAGIC:
        msg = "not a GLB file"
        raise ValueError(msg)
    document: object = None
    binary: bytes | None = None
    offset = 12
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        payload = data[offset : offset + length]
        offset += length
        if kind == _GLB_JSON_CHUNK:
            document = json.loads(payload.decode("utf-8"))
        elif kind == _GLB_BIN_CHUNK and binary is None:
            binary = payload
    if not isinstance(document, dict):
        msg = "GLB has no JSON chunk"
        raise ValueError(msg)
    return document, binary


def join_glb(document: dict[str, object], binary: bytes | None) -> bytes:
    """Pack a JSON document and an optional BIN chunk into a GLB container.

    Args:
        document: glTF JSON.
        binary: Buffer 0 contents, or ``None``.

    Returns:
        The file bytes, chunks padded to four bytes as the spec requires.
    """
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    chunks = struct.pack("<II", len(payload), _GLB_JSON_CHUNK) + payload
    if binary is not None:
        padded = binary + b"\0" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(padded), _GLB_BIN_CHUNK) + padded
    return struct.pack("<4sII", _GLB_MAGIC, 2, 12 + len(chunks)) + chunks


@dataclass(frozen=True, slots=True)
class ImportedLight:
    """A ``KHR_lights_punctual`` light on a node.

    Args:
        node: trimesh node name the light sits on.
        kind: ``directional``, ``point``, or ``spot``.
        color: Linear RGB, 0 to 1.
        intensity: Candela for point and spot lights, lux for directional.
        range: Cut-off distance, or ``0.0`` for none.
        outer_cone: Spot outer cone half-angle in radians.
    """

    node: str
    kind: str
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    intensity: float = 1.0
    range: float = 0.0
    outer_cone: float = math.pi / 4


def read_lights(document: GltfDocument) -> list[ImportedLight]:
    """Return the file's punctual lights, keyed by trimesh node name.

    Node names are made unique the way trimesh does, with the node index
    as the fallback name, so they match the scene graph the importer sees.

    Args:
        document: An open glTF document.
    """
    extensions = document.document.get("extensions")
    if not isinstance(extensions, dict):
        return []
    punctual = extensions.get("KHR_lights_punctual")
    if not isinstance(punctual, dict):
        return []
    definitions = punctual.get("lights")
    if not isinstance(definitions, list):
        return []
    lights: list[ImportedLight] = []
    names: dict[str, int] = {}
    counts: dict[str | None, int] = {}
    for index, node in enumerate(document.entries("nodes")):
        name = unique_name(str(node.get("name", index)), names, counts=counts)
        names[name] = index
        node_extensions = node.get("extensions")
        if not isinstance(node_extensions, dict):
            continue
        reference = node_extensions.get("KHR_lights_punctual")
        if not isinstance(reference, dict):
            continue
        light_index = reference.get("light")
        if not isinstance(light_index, int) or not 0 <= light_index < len(definitions):
            continue
        definition = definitions[light_index]
        if not isinstance(definition, dict):
            continue
        kind = definition.get("type")
        if kind not in ("directional", "point", "spot"):
            continue
        spot = definition.get("spot")
        spot = spot if isinstance(spot, dict) else {}
        lights.append(
            ImportedLight(
                node=name,
                kind=str(kind),
                color=_rgb(definition.get("color"), (1.0, 1.0, 1.0)),
                intensity=max(_number(definition.get("intensity"), 1.0), 0.0),
                range=max(_number(definition.get("range"), 0.0), 0.0),
                outer_cone=_number(spot.get("outerConeAngle"), math.pi / 4),
            )
        )
    return lights
