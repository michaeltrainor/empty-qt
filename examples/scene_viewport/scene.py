"""Shared scene description for the tree and the viewport."""

from __future__ import annotations

from collections.abc import Iterable

from examples.scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)

# Folder UIDs in the outliner only — not TransformComponents.
FOLDER_NAMES = frozenset({"world", "geo", "lgt"})


def transform_components() -> list[TransformComponent]:
    """Return the default transform set: a single directional key light."""
    return [
        TransformComponent(
            name="key",
            position=(4.0, 8.0, 3.0),
        ),
    ]


def mesh_components() -> list[MeshComponent]:
    """Return the default mesh set (empty — start from the Create menu)."""
    return []


def light_components() -> list[LightComponent]:
    """Return the default light set: one directional key."""
    return [
        LightComponent(
            name="keyShape",
            transform="key",
            kind="directional",
            color="#fff5e6",
            intensity=1.4,
            cast_shadow=True,
        ),
    ]


def build_forest(
    transforms: Iterable[TransformComponent],
    meshes: Iterable[MeshComponent],
    lights: Iterable[LightComponent],
) -> list[list[object]]:
    """Build a forest nesting transforms by ``parent`` with shape leaves.

    Light-bearing transforms sit under ``lgt``; everything else under ``geo``.
    ``world`` / ``geo`` / ``lgt`` are outliner folders only.
    """
    transform_list = list(transforms)
    mesh_list = list(meshes)
    light_list = list(lights)

    meshes_by_xform: dict[str, list[str]] = {}
    for mesh in mesh_list:
        meshes_by_xform.setdefault(mesh.transform, []).append(mesh.name)

    lights_by_xform: dict[str, list[str]] = {}
    for light in light_list:
        lights_by_xform.setdefault(light.transform, []).append(light.name)

    light_transforms = {light.transform for light in light_list}

    by_parent: dict[str, list[TransformComponent]] = {}
    for transform in transform_list:
        by_parent.setdefault(transform.parent, []).append(transform)

    def branch(transform: TransformComponent) -> list[object]:
        kids: list[object] = [
            branch(child) for child in by_parent.get(transform.name, ())
        ]
        for shape in meshes_by_xform.get(transform.name, ()):
            kids.append([shape])
        for shape in lights_by_xform.get(transform.name, ()):
            kids.append([shape])
        return [transform.name, *kids]

    roots = by_parent.get("", ())
    geo_nodes = [branch(root) for root in roots if root.name not in light_transforms]
    lgt_nodes = [branch(root) for root in roots if root.name in light_transforms]
    return [
        [
            "world",
            ["geo", *geo_nodes],
            ["lgt", *lgt_nodes],
        ]
    ]


def forest() -> list[list[object]]:
    """Build the default demo forest."""
    return build_forest(
        transform_components(),
        mesh_components(),
        light_components(),
    )
