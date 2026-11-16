"""Load model files (glTF, OBJ, STL, PLY, USD) into mesh buffers and a node tree.

trimesh reads the first four; USD stages go through
:mod:`scene_viewport.mesh.usd_reader`.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import trimesh
from numpy.typing import NDArray
from PIL import Image
from trimesh.resolvers import FilePathResolver
from trimesh.visual import ColorVisuals
from trimesh.visual.material import PBRMaterial, SimpleMaterial

from scene_viewport.mesh.gltf_extras import (
    GltfDocument,
    ImportedAnimation,
    ImportedCamera,
    ImportedLight,
    MapSampling,
    PrimitiveExtras,
    SurfaceExtensions,
    read_animations,
    read_cameras,
    read_lights,
)
from scene_viewport.mesh.references import (
    FileStamp,
    reference_problems,
    source_signature,
)
from scene_viewport.mesh.types import AlphaMode, MapKind, MeshBuffers
from scene_viewport.mesh.usd_reader import is_usd_model, read_usd_model

_log = logging.getLogger(__name__)

#: File suffixes :func:`load_model` reads, lower case.
IMPORT_SUFFIXES = (
    ".glb",
    ".gltf",
    ".obj",
    ".stl",
    ".ply",
    ".usd",
    ".usda",
    ".usdc",
    ".usdz",
)

#: Parsed files kept in memory, so a scene that uses one file many times (or
#: a reload of the same scene) reads it once.
_CACHE_SIZE = 8


class ModelImportError(Exception):
    """Raised when a model file can't be read or holds no triangle meshes."""


@dataclass(frozen=True, slots=True)
class MaterialSurface:
    """Everything a model file says about how a mesh's surface looks.

    The first group is copied onto the :class:`MeshComponent` at import, so
    the user can edit it and it is saved with the scene. The rest is read
    from the file each time the material is built, like the texture maps.

    Args:
        color: Base color as ``#rrggbb``.
        roughness: PBR roughness, 0 to 1.
        metalness: PBR metalness, 0 to 1.
        opacity: Base color alpha, 0 to 1.
        alpha_mode: How alpha is used (glTF ``alphaMode``).
        alpha_cutoff: Discard threshold for ``mask`` (glTF ``alphaCutoff``).
        double_sided: Draw back faces (glTF ``doubleSided``).
        unlit: ``KHR_materials_unlit``: show the color without lighting.
        emissive: Linear RGB emissive factor. glTF's default is black,
            which turns emission off.
        extensions: ``KHR_materials_*`` factors (ior, clearcoat, specular,
            sheen, iridescence, anisotropy, emissive strength).
        vertex_colors: The mesh carries per-vertex colors (``COLOR_0``).
    """

    color: str
    roughness: float
    metalness: float
    opacity: float = 1.0
    alpha_mode: AlphaMode = "opaque"
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    unlit: bool = False
    emissive: tuple[float, float, float] = (0.0, 0.0, 0.0)
    extensions: SurfaceExtensions = dataclasses.field(default_factory=SurfaceExtensions)
    vertex_colors: bool = False

    #: Field names copied onto a ``MeshComponent`` at import.
    COMPONENT_FIELDS = (
        "color",
        "roughness",
        "metalness",
        "opacity",
        "alpha_mode",
        "alpha_cutoff",
        "double_sided",
        "unlit",
    )

    def component_fields(self) -> dict[str, object]:
        """Return the values to copy onto a ``MeshComponent``."""
        return {name: getattr(self, name) for name in self.COMPONENT_FIELDS}


@dataclass(frozen=True, slots=True)
class ImportedMaps:
    """Texture images of one mesh, RGBA ``(height, width, 4)``, top row first.

    Each image field is named after its :data:`MapKind`. Color maps are
    sRGB; the rest hold linear data in the channels glTF assigns them.

    Args:
        base_color: Color image.
        metallic_roughness: Roughness in green, metalness in blue.
        normal: Tangent-space normal map.
        emissive: Emitted color image.
        occlusion: Ambient occlusion in the red channel.
        clearcoat: Clearcoat strength in red.
        clearcoat_roughness: Clearcoat roughness in green.
        clearcoat_normal: Clearcoat normal map.
        specular: Specular strength in alpha.
        specular_color: Specular color.
        sheen_color: Sheen color.
        sheen_roughness: Sheen roughness in alpha.
        iridescence: Iridescence strength in red.
        iridescence_thickness: Thin-film thickness in green.
        anisotropy: Direction in red and green, strength in blue.
        emissive_factor: Linear RGB multiplier for ``emissive``. glTF's
            default is black, which turns emission off.
        occlusion_strength: How much ``occlusion`` darkens, 0 to 1.
        sampling: UV set, wrap modes, and ``KHR_texture_transform`` per map.
    """

    base_color: NDArray[np.uint8] | None = None
    metallic_roughness: NDArray[np.uint8] | None = None
    normal: NDArray[np.uint8] | None = None
    emissive: NDArray[np.uint8] | None = None
    occlusion: NDArray[np.uint8] | None = None
    clearcoat: NDArray[np.uint8] | None = None
    clearcoat_roughness: NDArray[np.uint8] | None = None
    clearcoat_normal: NDArray[np.uint8] | None = None
    specular: NDArray[np.uint8] | None = None
    specular_color: NDArray[np.uint8] | None = None
    sheen_color: NDArray[np.uint8] | None = None
    sheen_roughness: NDArray[np.uint8] | None = None
    iridescence: NDArray[np.uint8] | None = None
    iridescence_thickness: NDArray[np.uint8] | None = None
    anisotropy: NDArray[np.uint8] | None = None
    emissive_factor: tuple[float, float, float] = (0.0, 0.0, 0.0)
    occlusion_strength: float = 1.0
    sampling: Mapping[MapKind, MapSampling] = dataclasses.field(default_factory=dict)

    def image(self, kind: MapKind) -> NDArray[np.uint8] | None:
        """Return the image for ``kind``, or ``None``."""
        return getattr(self, kind)

    def has_images(self) -> bool:
        """Whether any map carries an image."""
        return any(self.image(kind) is not None for kind in _MAP_KINDS)


#: Every map kind, in the order they appear in :class:`ImportedMaps`.
_MAP_KINDS: tuple[MapKind, ...] = (
    "base_color",
    "metallic_roughness",
    "normal",
    "emissive",
    "occlusion",
    "clearcoat",
    "clearcoat_roughness",
    "clearcoat_normal",
    "specular",
    "specular_color",
    "sheen_color",
    "sheen_roughness",
    "iridescence",
    "iridescence_thickness",
    "anisotropy",
)
#: Maps trimesh decodes itself; the rest are read through the glTF JSON.
_TRIMESH_MAPS: tuple[MapKind, ...] = (
    "base_color",
    "metallic_roughness",
    "normal",
    "emissive",
    "occlusion",
)


@dataclass(frozen=True, slots=True)
class ImportedNode:
    """One node of a model's hierarchy.

    Args:
        name: Node name from the file.
        parent: Parent node name, or ``""`` for a top-level node.
        matrix: 4x4 transform relative to the parent.
        mesh: Key into :attr:`ImportedModel.meshes`, or ``""`` for an empty.
        visible: Whether the node is shown (USD ``visibility``).
    """

    name: str
    parent: str
    matrix: NDArray[np.float64]
    mesh: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class ImportedModel:
    """A model file read into memory.

    Args:
        path: Resolved path of the file.
        nodes: Hierarchy, parents before children.
        meshes: Triangle buffers by mesh key.
        materials: Surface for each mesh key, or ``None`` when the file has
            none for it.
        maps: Texture images for each mesh key that has UVs and at least
            one image.
        warnings: Problems that didn't stop the load, such as a texture
            file that is missing or unreadable.
        lights: ``KHR_lights_punctual`` lights, each naming its node.
        cameras: glTF cameras, each naming its node.
        animations: glTF animations, each channel naming its node.
        up_axis: ``y`` or ``z`` when the file declares its up axis (USD
            ``upAxis``), else ``None``.
        meters_per_unit: Scene units in metres when the file declares them
            (USD ``metersPerUnit``), else ``None``.
    """

    path: Path
    nodes: tuple[ImportedNode, ...]
    meshes: Mapping[str, MeshBuffers]
    materials: Mapping[str, MaterialSurface | None]
    maps: Mapping[str, ImportedMaps]
    warnings: tuple[str, ...] = ()
    lights: tuple[ImportedLight, ...] = ()
    cameras: tuple[ImportedCamera, ...] = ()
    animations: tuple[ImportedAnimation, ...] = ()
    up_axis: str | None = None
    meters_per_unit: float | None = None


def is_importable(path: Path) -> bool:
    """Return whether ``path`` has a suffix :func:`load_model` reads.

    Args:
        path: Candidate file path.

    Returns:
        ``True`` for ``.glb``, ``.gltf``, ``.obj``, ``.stl``, ``.ply``, and
        the USD suffixes ``.usd``, ``.usda``, ``.usdc``, and ``.usdz``.
    """
    return path.suffix.lower() in IMPORT_SUFFIXES


def importable_paths(paths: Iterable[Path]) -> list[Path]:
    """Keep the existing files among ``paths`` that :func:`load_model` reads.

    Args:
        paths: Candidate paths, for example from a drop.

    Returns:
        Matching paths in their original order, without duplicates.
    """
    kept: list[Path] = []
    for path in paths:
        if is_importable(path) and path.is_file() and path not in kept:
            kept.append(path)
    return kept


def load_model(path: Path) -> ImportedModel:
    """Read a model file, reusing the parsed result while it is unchanged.

    "Unchanged" covers the files it refers to as well (glTF buffers and
    images, OBJ materials and textures), so editing a texture reloads.

    Args:
        path: Model file to read.

    Returns:
        The file's hierarchy, meshes, and materials.

    Raises:
        ModelImportError: When the file is missing, unreadable, of an
            unsupported type, or has no triangle meshes.
    """
    if not is_importable(path):
        msg = f"unsupported model file type: {path.name}"
        raise ModelImportError(msg)
    resolved = path.resolve()
    try:
        resolved.stat()
    except OSError as exc:
        msg = f"cannot read {path}: {exc.strerror or exc}"
        raise ModelImportError(msg) from exc
    return _load_cached(resolved, source_signature(resolved))


@functools.lru_cache(maxsize=_CACHE_SIZE)
def _load_cached(path: Path, _signature: tuple[FileStamp, ...]) -> ImportedModel:
    """Parse ``path``; the signature only keys the cache."""
    trimesh_log = logging.getLogger("trimesh")
    level = trimesh_log.level
    # trimesh logs tracebacks for optional packages it can do without.
    trimesh_log.setLevel(logging.ERROR)
    try:
        return _parse(path)
    finally:
        trimesh_log.setLevel(level)


def _parse(path: Path) -> ImportedModel:
    """Read ``path``: a USD stage through its reader, the rest with trimesh."""
    if is_usd_model(path):
        return _parse_usd(path)
    try:
        # process=False keeps the authored vertices: merging them would
        # smooth hard edges in files that ship split normals.
        scene = trimesh.load(
            path,
            force="scene",
            process=False,
            resolver=_UnquotingResolver(str(path.parent)),
        )
    except Exception as exc:
        # trimesh raises many types (ValueError, KeyError, JSON and zip
        # errors) for malformed files.
        msg = f"cannot read {path.name}: {exc}"
        raise ModelImportError(msg) from exc
    if not isinstance(scene, trimesh.Scene):
        msg = f"cannot read {path.name}: not a scene"
        raise ModelImportError(msg)

    document = GltfDocument.open(path)
    extras = document.primitive_extras() if document is not None else {}
    warnings: list[str] = []
    meshes: dict[str, MeshBuffers] = {}
    materials: dict[str, MaterialSurface | None] = {}
    maps: dict[str, ImportedMaps] = {}
    for key, geometry in scene.geometry.items():
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            _log.info("skipping non-triangle geometry %r in %s", key, path.name)
            continue
        extra = extras.get(key, PrimitiveExtras())
        buffers = _buffers(geometry, document, extra)
        materials[key] = _material(geometry, extra, buffers.colors is not None)
        mesh_maps = _maps(
            geometry,
            extra,
            document,
            file_name=path.name,
            key=key,
            has_uv1=buffers.texcoords1 is not None,
            warnings=warnings,
        )
        if mesh_maps is not None:
            maps[key] = mesh_maps
            if mesh_maps.normal is not None:
                buffers = dataclasses.replace(
                    buffers,
                    tangents=vertex_tangents(
                        buffers.positions,
                        buffers.normals,
                        buffers.texcoords,
                        buffers.indices,
                    ),
                )
        meshes[key] = buffers
    if not meshes:
        msg = f"{path.name} has no triangle meshes"
        raise ModelImportError(msg)
    if document is not None and document.entries("skins"):
        warnings.append(
            f"{path.name}: skins are not supported; skinned meshes keep their "
            "rest shape while their joints animate"
        )

    return ImportedModel(
        path=path,
        nodes=tuple(_nodes(scene, meshes)),
        meshes=meshes,
        materials=materials,
        maps=maps,
        warnings=(*reference_problems(path), *warnings),
        lights=tuple(read_lights(document)) if document is not None else (),
        cameras=tuple(read_cameras(document)) if document is not None else (),
        animations=tuple(read_animations(document)) if document is not None else (),
    )


def _parse_usd(path: Path) -> ImportedModel:
    """Read a USD stage, reporting missing textures like the other formats."""
    try:
        model = read_usd_model(path)
    except ValueError as exc:
        msg = f"cannot read {path.name}: {exc}"
        raise ModelImportError(msg) from exc
    if not model.meshes:
        msg = f"{path.name} has no triangle meshes"
        raise ModelImportError(msg)
    return dataclasses.replace(
        model, warnings=(*reference_problems(path), *model.warnings)
    )


class _UnquotingResolver(FilePathResolver):
    """Resolve glTF URIs with percent-escapes, such as ``paint%20new.png``."""

    def get(self, name: str) -> bytes:
        return super().get(unquote(name))


def _nodes(
    scene: trimesh.Scene, meshes: Mapping[str, MeshBuffers]
) -> list[ImportedNode]:
    """Walk the scene graph breadth-first from its base frame.

    trimesh replaces a glTF camera node with its own camera node; that one
    is skipped, since the importer rebuilds camera nodes from the JSON.
    """
    graph = scene.graph
    base = str(graph.base_frame)
    skip = {str(scene.camera.name)} if scene.has_camera else set()
    edges = {
        (str(parent), str(child)): data
        for (parent, child), data in graph.transforms.edge_data.items()
        if str(child) not in skip
    }
    children: dict[str, list[str]] = {}
    for parent, child in edges:
        children.setdefault(parent, []).append(child)

    nodes: list[ImportedNode] = []
    queue = [(base, child) for child in children.get(base, ())]
    while queue:
        parent, name = queue.pop(0)
        data = edges.get((parent, name), {})
        matrix = np.asarray(data.get("matrix", np.eye(4)), dtype=np.float64)
        geometry = data.get("geometry")
        mesh = str(geometry) if geometry is not None else ""
        nodes.append(
            ImportedNode(
                name=name,
                parent="" if parent == base else parent,
                matrix=matrix,
                mesh=mesh if mesh in meshes else "",
            )
        )
        queue.extend((name, child) for child in children.get(name, ()))
    return nodes


def _buffers(
    geometry: trimesh.Trimesh,
    document: GltfDocument | None = None,
    extra: PrimitiveExtras | None = None,
) -> MeshBuffers:
    """Convert a trimesh mesh into contiguous float32 / uint32 buffers.

    A second UV set and vertex colors come from the glTF accessors trimesh
    skips, when ``document`` is given; PLY/OBJ vertex colors come from
    trimesh's color visuals.
    """
    positions = np.ascontiguousarray(geometry.vertices, dtype=np.float32)
    faces = np.ascontiguousarray(geometry.faces, dtype=np.uint32)
    # trimesh caches normals it read from the file. Without SciPy its own
    # fallback for computing them takes minutes on a large mesh, so compute
    # missing ones here.
    # trimesh 5 keeps them in its private cache; tolerate it going away.
    if "vertex_normals" in getattr(geometry, "_cache", {}):
        normals = np.ascontiguousarray(geometry.vertex_normals, dtype=np.float32)
    else:
        normals = vertex_normals(positions, faces)
    uv = getattr(geometry.visual, "uv", None)
    if uv is not None and len(uv) == len(positions):
        texcoords = np.ascontiguousarray(np.asarray(uv)[:, :2], dtype=np.float32)
    else:
        texcoords = np.zeros((len(positions), 2), dtype=np.float32)
    texcoords1: NDArray[np.float32] | None = None
    colors: NDArray[np.float32] | None = None
    if document is not None and extra is not None:
        if extra.texcoords1 is not None:
            texcoords1 = _attribute(document, extra.texcoords1, len(positions), 2)
        if extra.colors is not None:
            colors = _attribute(document, extra.colors, len(positions), (3, 4))
    visual = geometry.visual
    if colors is None and isinstance(visual, ColorVisuals) and visual.kind == "vertex":
        colors = np.asarray(visual.vertex_colors, dtype=np.float32) / 255.0
    if colors is not None and colors.shape[1] == 3:
        colors = np.hstack([colors, np.ones((len(colors), 1), dtype=np.float32)])
    return MeshBuffers(
        positions=positions,
        normals=normals,
        indices=faces,
        texcoords=texcoords,
        texcoords1=texcoords1,
        colors=None if colors is None else np.ascontiguousarray(colors),
    )


def _attribute(
    document: GltfDocument,
    accessor: int,
    count: int,
    components: int | tuple[int, ...],
) -> NDArray[np.float32] | None:
    """Decode a vertex attribute that must match the mesh's vertex count."""
    values = document.accessor_array(accessor)
    if values is None or len(values) != count:
        return None
    allowed = (components,) if isinstance(components, int) else components
    if values.shape[1] not in allowed:
        return None
    return values


def vertex_normals(
    positions: NDArray[np.float32],
    faces: NDArray[np.uint32],
) -> NDArray[np.float32]:
    """Return area-weighted vertex normals for a triangle mesh.

    Vertices shared by faces get a smooth normal. Files with split vertices
    (each STL triangle has its own three) come out faceted.

    Args:
        positions: Vertex positions, shape ``(N, 3)``.
        faces: Triangle indices, shape ``(T, 3)``.

    Returns:
        Unit normals, shape ``(N, 3)``. A vertex on no face, or only on
        degenerate ones, gets ``+Y``.
    """
    points = np.asarray(positions, dtype=np.float64)
    corners = points[faces]
    # Unnormalized, so each face counts in proportion to its area.
    face_normals = np.cross(
        corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    )
    summed = np.zeros_like(points)
    for corner in range(3):
        for axis in range(3):
            summed[:, axis] += np.bincount(
                faces[:, corner],
                weights=face_normals[:, axis],
                minlength=len(points),
            )
    length = np.linalg.norm(summed, axis=1)
    flat = length <= 1e-20
    summed[flat] = (0.0, 1.0, 0.0)
    length[flat] = 1.0
    return np.ascontiguousarray(summed / length[:, None], dtype=np.float32)


def _maps(
    geometry: trimesh.Trimesh,
    extra: PrimitiveExtras,
    document: GltfDocument | None,
    *,
    file_name: str,
    key: str,
    has_uv1: bool,
    warnings: list[str],
) -> ImportedMaps | None:
    """Read a mesh's texture images, when it has UVs and any image.

    Core maps come from trimesh; ``KHR_materials_*`` textures are decoded
    through ``document``. A map that samples a UV set the mesh doesn't
    carry is dropped with a warning.
    """
    visual = geometry.visual
    uv = getattr(visual, "uv", None)
    if uv is None or len(uv) != len(geometry.vertices):
        return None
    material = getattr(visual, "material", None)
    if isinstance(material, SimpleMaterial):
        maps = ImportedMaps(base_color=_rgba(material.image))
        return maps if maps.has_images() else None
    if not isinstance(material, PBRMaterial):
        return None
    images: dict[MapKind, NDArray[np.uint8] | None] = {
        "base_color": _rgba(material.baseColorTexture),
        "metallic_roughness": _rgba(material.metallicRoughnessTexture),
        "normal": _rgba(material.normalTexture),
        "emissive": _rgba(material.emissiveTexture),
        "occlusion": _rgba(material.occlusionTexture),
    }
    if document is not None:
        for kind, sampling in extra.maps.items():
            if kind not in _TRIMESH_MAPS and sampling.texture >= 0:
                images[kind] = document.image_rgba(sampling.texture)
    sampling_by_kind: dict[MapKind, MapSampling] = {}
    for kind, sampling in extra.maps.items():
        if images.get(kind) is None:
            continue
        if sampling.uv_channel > 1 or (sampling.uv_channel == 1 and not has_uv1):
            images[kind] = None
            warnings.append(
                f"{file_name}: {key} {kind.replace('_', ' ')} map uses UV set "
                f"{sampling.uv_channel}, which this mesh doesn't carry; shown "
                "without it"
            )
            continue
        sampling_by_kind[kind] = sampling
    factor = material.emissiveFactor
    maps = ImportedMaps(
        **images,
        emissive_factor=_rgb(factor) if factor is not None else (0.0, 0.0, 0.0),
        occlusion_strength=extra.occlusion_strength,
        sampling=sampling_by_kind,
    )
    return maps if maps.has_images() else None


def _rgba(image: object) -> NDArray[np.uint8] | None:
    """Convert a Pillow image to an RGBA array, top row first."""
    if not isinstance(image, Image.Image):
        return None
    return np.array(image.convert("RGBA"), dtype=np.uint8)


def _rgb(values: object) -> tuple[float, float, float]:
    """Read three 0-1 floats from a factor array."""
    red, green, blue = (float(v) for v in np.asarray(values, dtype=np.float64)[:3])
    return (red, green, blue)


def vertex_tangents(
    positions: NDArray[np.float32],
    normals: NDArray[np.float32],
    texcoords: NDArray[np.float32],
    faces: NDArray[np.uint32],
) -> NDArray[np.float32]:
    """Return per-vertex tangents for normal mapping.

    Each triangle's +U and +V directions are summed onto its vertices, the
    +U sum is made perpendicular to the normal, and ``w`` records whether
    +V agrees with ``cross(normal, tangent)``. UVs use a bottom-left origin,
    so +V is the image's up, where OpenGL-style (glTF) normal maps point
    green.

    Args:
        positions: Vertex positions, shape ``(N, 3)``.
        normals: Unit vertex normals, shape ``(N, 3)``.
        texcoords: UVs, shape ``(N, 2)``.
        faces: Triangle indices, shape ``(T, 3)``.

    Returns:
        ``(N, 4)`` float32 tangents. A vertex without usable UVs gets any
        unit vector perpendicular to its normal and ``w = 1``.
    """
    points = np.asarray(positions, dtype=np.float64)
    uv = np.asarray(texcoords, dtype=np.float64)
    normal = np.asarray(normals, dtype=np.float64)
    edge1 = points[faces[:, 1]] - points[faces[:, 0]]
    edge2 = points[faces[:, 2]] - points[faces[:, 0]]
    duv1 = uv[faces[:, 1]] - uv[faces[:, 0]]
    duv2 = uv[faces[:, 2]] - uv[faces[:, 0]]
    det = duv1[:, 0] * duv2[:, 1] - duv2[:, 0] * duv1[:, 1]
    usable = np.abs(det) > 1e-20
    inverse = np.where(usable, 1.0 / np.where(usable, det, 1.0), 0.0)[:, None]
    face_u = (edge1 * duv2[:, 1:2] - edge2 * duv1[:, 1:2]) * inverse
    face_v = (edge2 * duv1[:, 0:1] - edge1 * duv2[:, 0:1]) * inverse
    count = len(points)
    sum_u = np.zeros((count, 3))
    sum_v = np.zeros((count, 3))
    for corner in range(3):
        for axis in range(3):
            sum_u[:, axis] += np.bincount(
                faces[:, corner], weights=face_u[:, axis], minlength=count
            )
            sum_v[:, axis] += np.bincount(
                faces[:, corner], weights=face_v[:, axis], minlength=count
            )
    tangent = sum_u - normal * np.sum(normal * sum_u, axis=1, keepdims=True)
    length = np.linalg.norm(tangent, axis=1)
    missing = length <= 1e-12
    if np.any(missing):
        # Any direction perpendicular to the normal.
        helper = np.where(
            np.abs(normal[missing, 0:1]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]]
        )
        tangent[missing] = np.cross(normal[missing], helper)
        length[missing] = np.linalg.norm(tangent[missing], axis=1)
    length[length <= 1e-12] = 1.0
    tangent /= length[:, None]
    handed = np.sum(np.cross(normal, tangent) * sum_v, axis=1)
    w = np.where(handed < 0.0, -1.0, 1.0)
    return np.ascontiguousarray(np.column_stack([tangent, w]), dtype=np.float32)


def _material(
    geometry: trimesh.Trimesh, extra: PrimitiveExtras, has_colors: bool
) -> MaterialSurface | None:
    """Read the surface the file describes: the one reader for every format."""
    visual = geometry.visual
    material = getattr(visual, "material", None)
    if isinstance(material, PBRMaterial):
        factor = material.baseColorFactor
        return MaterialSurface(
            # glTF's default factor is white, which leaves a texture as is.
            color=_hex(factor) if factor is not None else "#ffffff",
            roughness=_unit(material.roughnessFactor, 1.0),
            metalness=_unit(material.metallicFactor, 1.0),
            opacity=_alpha(factor),
            alpha_mode=_alpha_mode(material.alphaMode),
            alpha_cutoff=_unit(material.alphaCutoff, 0.5),
            double_sided=bool(material.doubleSided),
            unlit=extra.unlit,
            emissive=(
                _rgb(material.emissiveFactor)
                if material.emissiveFactor is not None
                else (0.0, 0.0, 0.0)
            ),
            extensions=extra.extensions,
            vertex_colors=has_colors,
        )
    if isinstance(material, SimpleMaterial):
        return MaterialSurface(
            color=_hex(material.diffuse),
            roughness=0.6,
            metalness=0.0,
            vertex_colors=has_colors,
        )
    # Vertex or face colors (common in PLY): the dominant one is the base.
    if isinstance(visual, ColorVisuals) and visual.defined:
        return MaterialSurface(
            color=_hex(visual.main_color),
            roughness=0.6,
            metalness=0.0,
            vertex_colors=has_colors,
        )
    return None


def _hex(rgba: object) -> str:
    """Format an RGB(A) color, 0-255 integers or 0-1 floats, as ``#rrggbb``."""
    values = np.asarray(rgba, dtype=np.float64).reshape(-1)[:3]
    if values.size < 3:
        return "#cccccc"
    if np.all(values <= 1.0) and not np.issubdtype(np.asarray(rgba).dtype, np.integer):
        values = values * 255.0
    red, green, blue = (int(np.clip(round(v), 0, 255)) for v in values)
    return f"#{red:02x}{green:02x}{blue:02x}"


def _unit(value: float | None, default: float) -> float:
    """Clamp an optional factor to ``[0, 1]``."""
    if value is None:
        return default
    return float(np.clip(value, 0.0, 1.0))


def _alpha(rgba: object) -> float:
    """Return the alpha of an RGBA factor, 0-255 integers or 0-1 floats."""
    if rgba is None:
        return 1.0
    values = np.asarray(rgba, dtype=np.float64).reshape(-1)
    if values.size < 4:
        return 1.0
    alpha = values[3]
    if np.issubdtype(np.asarray(rgba).dtype, np.integer) or alpha > 1.0:
        alpha = alpha / 255.0
    return float(np.clip(alpha, 0.0, 1.0))


def _alpha_mode(value: object) -> AlphaMode:
    """Map glTF ``alphaMode`` to the component value."""
    if isinstance(value, str):
        if value.upper() == "MASK":
            return "mask"
        if value.upper() == "BLEND":
            return "blend"
    return "opaque"
