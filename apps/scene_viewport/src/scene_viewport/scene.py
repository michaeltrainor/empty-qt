"""Shared scene description for the tree and the viewport."""

from __future__ import annotations

from collections.abc import Iterable

from scene_viewport.components import (
    CameraComponent,
    LightComponent,
    MeshComponent,
    TransformComponent,
)

# Folder UIDs in the outliner only — not TransformComponents.
FOLDER_NAMES = frozenset({"world", "geo", "lgt", "cam"})


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
    cameras: Iterable[CameraComponent] = (),
) -> list[list[object]]:
    """Build a forest nesting transforms by ``parent`` with shape leaves.

    Root transforms that carry a light sit under ``lgt``, ones that carry a
    camera under ``cam``, and everything else under ``geo``. ``world``,
    ``geo``, ``lgt``, and ``cam`` are outliner folders only.
    """
    transform_list = list(transforms)
    mesh_list = list(meshes)
    light_list = list(lights)
    camera_list = list(cameras)

    shapes_by_xform: dict[str, list[str]] = {}
    for mesh in mesh_list:
        shapes_by_xform.setdefault(mesh.transform, []).append(mesh.name)
    for light in light_list:
        shapes_by_xform.setdefault(light.transform, []).append(light.name)
    for camera in camera_list:
        shapes_by_xform.setdefault(camera.transform, []).append(camera.name)

    light_transforms = {light.transform for light in light_list}
    camera_transforms = {camera.transform for camera in camera_list}

    by_parent: dict[str, list[TransformComponent]] = {}
    for transform in transform_list:
        by_parent.setdefault(transform.parent, []).append(transform)

    def branch(transform: TransformComponent) -> list[object]:
        kids: list[object] = [
            branch(child) for child in by_parent.get(transform.name, ())
        ]
        for shape in shapes_by_xform.get(transform.name, ()):
            kids.append([shape])
        return [transform.name, *kids]

    def folder_of(root: TransformComponent) -> str:
        if root.name in light_transforms:
            return "lgt"
        if root.name in camera_transforms:
            return "cam"
        return "geo"

    roots = by_parent.get("", ())
    folders: dict[str, list[object]] = {"geo": [], "lgt": [], "cam": []}
    for root in roots:
        folders[folder_of(root)].append(branch(root))
    return [
        [
            "world",
            ["geo", *folders["geo"]],
            ["lgt", *folders["lgt"]],
            ["cam", *folders["cam"]],
        ]
    ]


def forest() -> list[list[object]]:
    """Build the default demo forest."""
    return build_forest(
        transform_components(),
        mesh_components(),
        light_components(),
    )
