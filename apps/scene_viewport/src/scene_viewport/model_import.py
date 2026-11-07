"""Turn an imported model file into scene components."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from scene_viewport.components import MeshComponent, Space, TransformComponent
from scene_viewport.history import SceneObjects
from scene_viewport.mesh.importer import ImportedModel, ImportedNode
from scene_viewport.xform_math import trs_from_mat

_UNSAFE = re.compile(r"[^0-9A-Za-z_]+")


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


def import_objects(
    model: ImportedModel,
    used: set[str],
    *,
    space: Space = "world",
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
    transforms: list[TransformComponent] = []
    meshes: list[MeshComponent] = []

    single = _single_mesh_node(model)
    if single is not None:
        position, rotation, scale = trs_from_mat(single.matrix)
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

    transforms.append(TransformComponent(name=root, space=space))
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
        }
    )
