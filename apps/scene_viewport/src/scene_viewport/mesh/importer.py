"""Load model files (glTF, OBJ, STL, PLY) into mesh buffers and a node tree."""

from __future__ import annotations

import functools
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from numpy.typing import NDArray
from trimesh.visual import ColorVisuals
from trimesh.visual.material import PBRMaterial, SimpleMaterial

from scene_viewport.mesh.types import MeshBuffers

_log = logging.getLogger(__name__)

#: File suffixes :func:`load_model` reads, lower case.
IMPORT_SUFFIXES = (".glb", ".gltf", ".obj", ".stl", ".ply")

#: Parsed files kept in memory, so a scene that uses one file many times (or
#: a reload of the same scene) reads it once.
_CACHE_SIZE = 8


class ModelImportError(Exception):
    """Raised when a model file can't be read or holds no triangle meshes."""


@dataclass(frozen=True, slots=True)
class ImportedMaterial:
    """Flat material values read from a model file.

    Args:
        color: Base color as ``#rrggbb``.
        roughness: PBR roughness, 0 to 1.
        metalness: PBR metalness, 0 to 1.
    """

    color: str
    roughness: float
    metalness: float


@dataclass(frozen=True, slots=True)
class ImportedNode:
    """One node of a model's hierarchy.

    Args:
        name: Node name from the file.
        parent: Parent node name, or ``""`` for a top-level node.
        matrix: 4x4 transform relative to the parent.
        mesh: Key into :attr:`ImportedModel.meshes`, or ``""`` for an empty.
    """

    name: str
    parent: str
    matrix: NDArray[np.float64]
    mesh: str


@dataclass(frozen=True, slots=True)
class ImportedModel:
    """A model file read into memory.

    Args:
        path: Resolved path of the file.
        nodes: Hierarchy, parents before children.
        meshes: Triangle buffers by mesh key.
        materials: Material for each mesh key, or ``None`` when the file has
            none for it.
    """

    path: Path
    nodes: tuple[ImportedNode, ...]
    meshes: Mapping[str, MeshBuffers]
    materials: Mapping[str, ImportedMaterial | None]


def is_importable(path: Path) -> bool:
    """Return whether ``path`` has a suffix :func:`load_model` reads.

    Args:
        path: Candidate file path.

    Returns:
        ``True`` for ``.glb``, ``.gltf``, ``.obj``, ``.stl``, and ``.ply``.
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
        stat = resolved.stat()
    except OSError as exc:
        msg = f"cannot read {path}: {exc.strerror or exc}"
        raise ModelImportError(msg) from exc
    return _load_cached(resolved, stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=_CACHE_SIZE)
def _load_cached(path: Path, _mtime_ns: int, _size: int) -> ImportedModel:
    """Parse ``path``; the stat values only key the cache."""
    trimesh_log = logging.getLogger("trimesh")
    level = trimesh_log.level
    # trimesh logs tracebacks for optional packages it can do without.
    trimesh_log.setLevel(logging.ERROR)
    try:
        return _parse(path)
    finally:
        trimesh_log.setLevel(level)


def _parse(path: Path) -> ImportedModel:
    """Read ``path`` with trimesh and convert its meshes and hierarchy."""
    try:
        # process=False keeps the authored vertices: merging them would
        # smooth hard edges in files that ship split normals.
        scene = trimesh.load(path, force="scene", process=False)
    except Exception as exc:
        # trimesh raises many types (ValueError, KeyError, JSON and zip
        # errors) for malformed files.
        msg = f"cannot read {path.name}: {exc}"
        raise ModelImportError(msg) from exc
    if not isinstance(scene, trimesh.Scene):
        msg = f"cannot read {path.name}: not a scene"
        raise ModelImportError(msg)

    meshes: dict[str, MeshBuffers] = {}
    materials: dict[str, ImportedMaterial | None] = {}
    for key, geometry in scene.geometry.items():
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            _log.info("skipping non-triangle geometry %r in %s", key, path.name)
            continue
        meshes[key] = _buffers(geometry)
        materials[key] = _material(geometry)
    if not meshes:
        msg = f"{path.name} has no triangle meshes"
        raise ModelImportError(msg)

    return ImportedModel(
        path=path,
        nodes=tuple(_nodes(scene, meshes)),
        meshes=meshes,
        materials=materials,
    )


def _nodes(
    scene: trimesh.Scene, meshes: Mapping[str, MeshBuffers]
) -> list[ImportedNode]:
    """Walk the scene graph breadth-first from its base frame."""
    graph = scene.graph
    base = str(graph.base_frame)
    edges = {
        (str(parent), str(child)): data
        for (parent, child), data in graph.transforms.edge_data.items()
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


def _buffers(geometry: trimesh.Trimesh) -> MeshBuffers:
    """Convert a trimesh mesh into contiguous float32 / uint32 buffers."""
    positions = np.ascontiguousarray(geometry.vertices, dtype=np.float32)
    faces = np.ascontiguousarray(geometry.faces, dtype=np.uint32)
    # trimesh caches normals it read from the file. Without SciPy its own
    # fallback for computing them takes minutes on a large mesh, so compute
    # missing ones here.
    if "vertex_normals" in geometry._cache:
        normals = np.ascontiguousarray(geometry.vertex_normals, dtype=np.float32)
    else:
        normals = vertex_normals(positions, faces)
    uv = getattr(geometry.visual, "uv", None)
    if uv is not None and len(uv) == len(positions):
        texcoords = np.ascontiguousarray(np.asarray(uv)[:, :2], dtype=np.float32)
    else:
        texcoords = np.zeros((len(positions), 2), dtype=np.float32)
    return MeshBuffers(
        positions=positions,
        normals=normals,
        indices=faces,
        texcoords=texcoords,
    )


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


def _material(geometry: trimesh.Trimesh) -> ImportedMaterial | None:
    """Read base color, roughness, and metalness, when the file sets them."""
    visual = geometry.visual
    material = getattr(visual, "material", None)
    if isinstance(material, PBRMaterial):
        factor = material.baseColorFactor
        return ImportedMaterial(
            color=_hex(factor) if factor is not None else "#cccccc",
            roughness=_unit(material.roughnessFactor, 1.0),
            metalness=_unit(material.metallicFactor, 1.0),
        )
    if isinstance(material, SimpleMaterial):
        return ImportedMaterial(
            color=_hex(material.diffuse),
            roughness=0.6,
            metalness=0.0,
        )
    # Vertex or face colors (common in PLY): use the dominant one.
    if isinstance(visual, ColorVisuals) and visual.defined:
        return ImportedMaterial(
            color=_hex(visual.main_color),
            roughness=0.6,
            metalness=0.0,
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
