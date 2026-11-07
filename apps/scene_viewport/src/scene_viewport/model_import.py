"""Turn an imported model file into scene components."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray

from scene_viewport.animation import (
    AnimationChannel,
    AnimationClip,
    ChannelPath,
    Interpolation,
)
from scene_viewport.components import (
    CameraComponent,
    LightComponent,
    MeshComponent,
    Space,
    TransformComponent,
)
from scene_viewport.history import SceneObjects
from scene_viewport.mesh.importer import ImportedModel, ImportedNode
from scene_viewport.settings import ImportUnit, ImportUpAxis
from scene_viewport.world import unique_clip_name
from scene_viewport.xform_math import mat_from_trs, trs_from_mat

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
        objects: Every new transform, mesh, light, camera, and animation
            clip, parents before children.
        warnings: Approximations made while converting, such as a spot
            light's direction being dropped or animation channels skipped.
    """

    root: str
    objects: SceneObjects
    warnings: tuple[str, ...] = ()


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
    clip_names: set[str] | None = None,
) -> ImportResult:
    """Build transforms and meshes for ``model`` under one new root.

    The root transform is named after the file. Each node becomes a
    transform under it, keeping the file's hierarchy, and each node with a
    mesh gets a ``file`` mesh shape. A file with a single mesh node and no
    hierarchy (most OBJ, STL, and PLY files) puts the mesh on the root,
    unless the file has lights, cameras, or animations.

    Args:
        model: Parsed model file.
        used: Names already taken. New names are added to it.
        space: Edit space for the new transforms.
        conversion: Matrix applied to the root, such as from
            :func:`import_conversion`. ``None`` keeps the file's coordinates.
        clip_names: Animation clip names already taken; new clip names are
            added to it. Clips have their own namespace, separate from
            ``used``. ``None`` only avoids clashes within this file.

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
    if (
        single is not None
        and not model.lights
        and not model.cameras
        and not model.animations
    ):
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
    world_matrices: dict[str, NDArray[np.float64]] = {root: convert}
    for node in model.nodes:
        name = claim(safe_name(node.name))
        names[node.name] = name
        parent = names.get(node.parent, root)
        world_matrices[name] = world_matrices[parent] @ node.matrix
        position, rotation, scale = trs_from_mat(node.matrix)
        transforms.append(
            TransformComponent(
                name=name,
                parent=parent,
                space=space,
                position=position,
                rotation=rotation,
                scale=scale,
            )
        )
        if node.mesh:
            meshes.append(_mesh(model, node, name, claim))
    lights, warnings = _lights(model, names, transforms, world_matrices, claim)
    cameras = _cameras(model, names, transforms, root, space, claim)
    # After the cameras, so a channel on a rebuilt camera node finds it.
    animations, animation_warnings = _animations(
        model, names, set() if clip_names is None else clip_names
    )
    warnings.extend(animation_warnings)
    return ImportResult(
        root,
        SceneObjects(
            transforms=transforms,
            meshes=meshes,
            lights=lights,
            cameras=cameras,
            animations=animations,
        ),
        warnings=tuple(warnings),
    )


#: Decimal places kept for keyframe values in the scene file. glTF stores
#: float32, which has about seven significant digits.
_KEY_DECIMALS = 7


def _animations(
    model: ImportedModel,
    names: dict[str, str],
    clip_names: set[str],
) -> tuple[list[AnimationClip], list[str]]:
    """Turn the file's animations into clips on the new transforms.

    Each channel's node name is translated through ``names``, the file
    node → transform name map built while creating the transforms, so a
    clip follows the renames ``claim`` made. Channels on nodes that were
    not imported are dropped. Skipped channels of any kind give one
    warning per clip. A clip with nothing left to play is dropped.
    """
    clips: list[AnimationClip] = []
    warnings: list[str] = []
    for animation in model.animations:
        channels: list[AnimationChannel] = []
        dropped: list[str] = []
        for channel in animation.channels:
            target = names.get(channel.node)
            if target is None:
                dropped.append(channel.node)
                continue
            channels.append(
                AnimationChannel(
                    transform=target,
                    path=cast(ChannelPath, channel.path),
                    interpolation=cast(Interpolation, channel.interpolation),
                    times=np.asarray(channel.times, dtype=np.float64).tolist(),
                    values=np.round(
                        np.asarray(channel.values, dtype=np.float64), _KEY_DECIMALS
                    ).tolist(),
                )
            )
        skipped = list(animation.skipped)
        if dropped:
            nodes = ", ".join(dict.fromkeys(dropped))
            skipped.append(f"channels on nodes that were not imported ({nodes})")
        if not channels and not skipped:
            skipped.append("every channel; it has nothing to play")
        if skipped:
            warnings.append(
                f"{model.path.name}: animation {animation.name!r} skips "
                + "; ".join(skipped)
            )
        if not channels:
            continue
        name = unique_clip_name(animation.name, clip_names)
        clip_names.add(name)
        clips.append(AnimationClip(name=name, channels=channels))
    return clips, warnings


def _cameras(
    model: ImportedModel,
    names: dict[str, str],
    transforms: list[TransformComponent],
    root: str,
    space: Space,
    claim: Callable[[str], str],
) -> list[CameraComponent]:
    """Turn the file's cameras into camera shapes on their nodes.

    glTF cameras look along their node's -Z, as ours do, so the node's
    transform carries the aim unchanged. trimesh drops camera nodes from
    its graph, so a node that didn't come through is rebuilt here from the
    glTF JSON, under its parent or the file root.
    """
    cameras: list[CameraComponent] = []
    for camera in model.cameras:
        transform_name = names.get(camera.node)
        if transform_name is None:
            transform_name = claim(safe_name(camera.node))
            names[camera.node] = transform_name
            position, rotation, scale = trs_from_mat(camera.matrix)
            transforms.append(
                TransformComponent(
                    name=transform_name,
                    parent=names.get(camera.parent, root),
                    space=space,
                    position=position,
                    rotation=rotation,
                    scale=scale,
                )
            )
        near = min(max(camera.znear, 1e-4), 1000.0)
        far = camera.zfar if camera.zfar > near else max(near * 5000.0, 500.0)
        cameras.append(
            CameraComponent(
                name=claim(f"{transform_name}Shape"),
                transform=transform_name,
                kind="orthographic" if camera.kind == "orthographic" else "perspective",
                fov=min(max(math.degrees(camera.yfov), 1.0), 170.0),
                ortho_size=min(max(camera.ymag * 2.0, 1e-3), 10000.0),
                near=near,
                far=min(far, 100000.0),
            )
        )
    return cameras


#: Distance from the origin at which an imported directional light is placed.
_DIRECTIONAL_DISTANCE = 8.0
#: Upper bound of ``LightComponent.intensity``.
_MAX_INTENSITY = 20.0


def _lights(
    model: ImportedModel,
    names: dict[str, str],
    transforms: list[TransformComponent],
    world_matrices: dict[str, NDArray[np.float64]],
    claim: Callable[[str], str],
) -> tuple[list[LightComponent], list[str]]:
    """Turn the file's punctual lights into light shapes on their nodes.

    The viewport aims directional and spot lights at the scene origin, so a
    directional light's node is moved onto the line its direction defines
    (its position carries no meaning in glTF). A spot light keeps its
    position and loses the file's direction, with a warning. Intensities
    above the component's range are clamped, with a warning.
    """
    lights: list[LightComponent] = []
    warnings: list[str] = []
    by_name = {transform.name: index for index, transform in enumerate(transforms)}
    for light in model.lights:
        transform_name = names.get(light.node)
        if transform_name is None or transform_name not in by_name:
            continue
        intensity = light.intensity
        if intensity > _MAX_INTENSITY:
            warnings.append(
                f"{model.path.name}: light on {light.node} has intensity "
                f"{intensity:g}; clamped to {_MAX_INTENSITY:g}"
            )
            intensity = _MAX_INTENSITY
        if light.kind == "directional":
            index = by_name[transform_name]
            transforms[index] = _aim_from_direction(
                transforms[index], world_matrices, transform_name
            )
        elif light.kind == "spot":
            warnings.append(
                f"{model.path.name}: spot light on {light.node} aims at the "
                "scene origin; the file's direction was dropped"
            )
        red, green, blue = (round(max(0.0, min(1.0, c)) * 255) for c in light.color)
        lights.append(
            LightComponent(
                name=claim(f"{transform_name}Shape"),
                transform=transform_name,
                kind=light.kind,  # type: ignore[arg-type]
                color=f"#{red:02x}{green:02x}{blue:02x}",
                intensity=intensity,
                distance=min(light.range, 100.0),
            )
        )
    return lights, warnings


def _aim_from_direction(
    transform: TransformComponent,
    world_matrices: dict[str, NDArray[np.float64]],
    name: str,
) -> TransformComponent:
    """Place a directional light's node so aiming at the origin keeps its direction.

    glTF lights shine along their node's -Z. The node is moved to
    ``-direction * distance`` in world space, expressed in its parent's
    frame, so the viewport's aim at the origin reproduces the direction.
    """
    world = world_matrices[name]
    direction = -world[:3, 2]
    length = float(np.linalg.norm(direction))
    if length < 1e-9:
        return transform
    direction = direction / length
    target_world = -direction * _DIRECTIONAL_DISTANCE
    parent_world = world @ np.linalg.inv(
        mat_from_trs(transform.position, transform.rotation, transform.scale)
    )
    local = np.linalg.inv(parent_world) @ np.array([*target_world, 1.0])
    return transform.model_copy(
        update={"position": (float(local[0]), float(local[1]), float(local[2]))}
    )


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
    return mesh.model_copy(update=material.component_fields())
