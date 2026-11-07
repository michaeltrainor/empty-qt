"""Turn an imported model file into scene components."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from scene_viewport.components import MeshComponent, Space, TransformComponent
from scene_viewport.history import SceneObjects
from scene_viewport.mesh.importer import ImportedModel, ImportedNode
from scene_viewport.settings import ImportUnit, ImportUpAxis
from scene_viewport.xform_math import trs_from_mat

_UNSAFE = re.compile(r"[^0-9A-Za-z_]+")
#: Metres per unit for :data:`ImportUnit` values; ``auto`` keeps numbers.
_METRES = {"auto": 1.0, "m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254}
#: Formats usually authored Z-up (CAD and 3D printing).
_Z_UP_SUFFIXES = frozenset({".stl", ".ply"})
#: glTF fixes Y-up and metres in its spec, so it is never converted.
_GLTF_SUFFIXES = frozenset({".gltf", ".glb"})


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Components created for one model file.

    Args:
        root: Name of the transform that holds the whole file.
        objects: Every new transform and mesh, parents before children.
    """

    root: str
    objects: SceneObjects


def safe_name(text: str, fallback: str = "node") -> str:
    """Return ``text`` as a node name: letters, digits, and underscores.

    Args:
        text: Name from a file, for example ``"Wheel.001"`` or ``"ball.obj"``.
        fallback: Used when nothing usable is left.

    Returns:
        A name like ``"Wheel_001"`` that does not start with a digit.
    """
    name = _UNSAFE.sub("_", text).strip("_")
    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"_{name}"
    return name


def import_conversion(
    path: Path,
    up_axis: ImportUpAxis = "auto",
    unit: ImportUnit = "auto",
) -> NDArray[np.float64]:
    """Return the matrix that brings a file's coordinates into the scene.

    The scene is Y-up in metres. A Z-up file is turned -90 degrees about X,
    so its +Z becomes +Y, and its units are scaled to metres. glTF files
    are already Y-up metres and get the identity.

    Args:
        path: Model file; its suffix picks the ``auto`` up axis.
        up_axis: ``auto`` (Z for STL and PLY, Y for OBJ), ``y``, or ``z``.
        unit: Units the file was authored in, or ``auto`` to keep numbers.

    Returns:
        A 4x4 rotation and uniform scale.
    """
    suffix = path.suffix.lower()
    if suffix in _GLTF_SUFFIXES:
        return np.eye(4)
    scale = _METRES[unit]
    matrix = np.diag([scale, scale, scale, 1.0])
    z_up = up_axis == "z" or (up_axis == "auto" and suffix in _Z_UP_SUFFIXES)
    if z_up:
        to_y_up = np.array(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, -1.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        matrix = to_y_up @ matrix
    return matrix


def import_objects(
    model: ImportedModel,
    used: set[str],
    *,
    space: Space = "world",
    conversion: NDArray[np.float64] | None = None,
) -> ImportResult:
    """Build transforms and meshes for ``model`` under one new root.

    The root transform is named after the file. Each node becomes a
    transform under it, keeping the file's hierarchy, and each node with a
    mesh gets a ``file`` mesh shape. A file with a single mesh node and no
    hierarchy (most OBJ, STL, and PLY files) puts the mesh on the root.

    Args:
        model: Parsed model file.
        used: Names already taken. New names are added to it.
        space: Edit space for the new transforms.
        conversion: Matrix applied to the root, such as from
            :func:`import_conversion`. ``None`` keeps the file's coordinates.

    Returns:
        The root name and the new components.
    """

    def claim(stem: str) -> str:
        name = stem
        index = 1
        while name in used:
            name = f"{stem}{index}"
            index += 1
        used.add(name)
        return name

    root = claim(safe_name(model.path.stem, "model"))
    convert = (
        np.eye(4) if conversion is None else np.asarray(conversion, dtype=np.float64)
    )
    transforms: list[TransformComponent] = []
    meshes: list[MeshComponent] = []

    single = _single_mesh_node(model)
    if single is not None:
        position, rotation, scale = trs_from_mat(convert @ single.matrix)
        transforms.append(
            TransformComponent(
                name=root,
                space=space,
                position=position,
                rotation=rotation,
                scale=scale,
            )
        )
        meshes.append(_mesh(model, single, root, claim))
        return ImportResult(root, SceneObjects(transforms=transforms, meshes=meshes))

    position, rotation, scale = trs_from_mat(convert)
    transforms.append(
        TransformComponent(
            name=root,
            space=space,
            position=position,
            rotation=rotation,
            scale=scale,
        )
    )
    names: dict[str, str] = {}
    for node in model.nodes:
        name = claim(safe_name(node.name))
        names[node.name] = name
        position, rotation, scale = trs_from_mat(node.matrix)
        transforms.append(
            TransformComponent(
                name=name,
                parent=names.get(node.parent, root),
                space=space,
                position=position,
                rotation=rotation,
                scale=scale,
            )
        )
        if node.mesh:
            meshes.append(_mesh(model, node, name, claim))
    return ImportResult(root, SceneObjects(transforms=transforms, meshes=meshes))


def _single_mesh_node(model: ImportedModel) -> ImportedNode | None:
    """Return the only node when the file is one mesh with no hierarchy."""
    if len(model.nodes) != 1:
        return None
    node = model.nodes[0]
    return node if node.mesh else None


def _mesh(
    model: ImportedModel,
    node: ImportedNode,
    transform: str,
    claim: Callable[[str], str],
) -> MeshComponent:
    """Build the ``file`` mesh shape for ``node`` on ``transform``."""
    mesh = MeshComponent(
        name=claim(f"{transform}Shape"),
        transform=transform,
        kind="file",
        source=model.path,
        source_mesh=node.mesh,
    )
    material = model.materials.get(node.mesh)
    if material is None:
        return mesh
    return mesh.model_copy(
        update={
            "color": material.color,
            "roughness": material.roughness,
            "metalness": material.metalness,
            "opacity": material.opacity,
            "alpha_mode": material.alpha_mode,
            "alpha_cutoff": material.alpha_cutoff,
            "double_sided": material.double_sided,
            "unlit": material.unlit,
        }
    )
