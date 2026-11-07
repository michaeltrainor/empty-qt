"""Save and open scene viewport documents as OpenUSD files.

The component ↔ prim mapping lives in :mod:`scene_viewport.usd_prims`,
shared with the live stage inside :class:`~scene_viewport.world.SceneWorld`.
This module only orchestrates files: one root layer with the scene, and one
sublayer per animation clip in ``<stem>_clips/`` beside it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from pxr import Sdf, Tf, Usd, UsdGeom, UsdLux

from scene_viewport.animation import AnimationClip
from scene_viewport.components import (
    CameraComponent,
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.scene_io import SceneDocument
from scene_viewport.usd_prims import (
    LOOKS,
    ROOT,
    TIME_CODES_PER_SECOND,
    author_clip,
    configure_stage,
    define_camera,
    define_light,
    define_mesh,
    define_transform,
    identifier,
    read_camera,
    read_clip,
    read_index,
    read_light,
    read_mesh,
    read_name,
    read_transform,
    unique_child_path,
)

_log = logging.getLogger(__name__)

#: File suffixes :func:`save_usd_scene` writes and :func:`load_usd_scene` reads.
USD_SUFFIXES = (".usda", ".usdc", ".usd")


def is_usd_file(path: Path) -> bool:
    """Return whether ``path`` has a USD layer suffix.

    Args:
        path: Candidate file path.
    """
    return path.suffix.lower() in USD_SUFFIXES


# -- save ---------------------------------------------------------------------


def save_usd_scene(document: SceneDocument, path: Path) -> Path:
    """Write ``document`` as a USD stage at ``path``.

    Args:
        document: Scene to persist. Mesh ``source`` paths are written relative
            to the layer when they share a drive with it.
        path: Destination ``.usda``, ``.usdc``, or ``.usd`` file. Clips go to
            ``<stem>_clips/`` beside it, one layer each.

    Returns:
        The path written.

    Raises:
        OSError: When a layer cannot be created or saved.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage = Usd.Stage.CreateNew(str(path))
    except Tf.ErrorException as exc:
        raise OSError(f"cannot create {path}: {exc}") from exc
    if stage is None:
        raise OSError(f"cannot create {path}")
    configure_stage(stage)
    taken: set[Sdf.Path] = set()

    def is_taken(candidate: Sdf.Path) -> bool:
        return candidate in taken or stage.GetPrimAtPath(candidate).IsValid()

    paths = _prim_paths(document.transforms, is_taken)
    taken.update(paths.values())
    for index, transform in enumerate(document.transforms):
        define_transform(stage, paths[transform.name], transform, index)
    for index, mesh in enumerate(document.meshes):
        prim_path = unique_child_path(paths[mesh.transform], mesh.name, is_taken)
        taken.add(prim_path)
        define_mesh(stage, prim_path, mesh, path.parent, index)
    for index, light in enumerate(document.lights):
        prim_path = unique_child_path(paths[light.transform], light.name, is_taken)
        taken.add(prim_path)
        define_light(stage, prim_path, light, index)
    for index, camera in enumerate(document.cameras):
        prim_path = unique_child_path(paths[camera.transform], camera.name, is_taken)
        taken.add(prim_path)
        define_camera(stage, prim_path, camera, index)

    layer = stage.GetRootLayer()
    if document.animations:
        clips_dir = path.parent / f"{path.stem}_clips"
        clips_dir.mkdir(exist_ok=True)
        used_files: set[str] = set()
        for clip in document.animations:
            stem = identifier(clip.name)
            file_name, index = f"{stem}{path.suffix}", 1
            while file_name.lower() in used_files:
                file_name, index = f"{stem}_{index}{path.suffix}", index + 1
            used_files.add(file_name.lower())
            clip_path = clips_dir / file_name
            try:
                clip_layer = Sdf.Layer.CreateNew(str(clip_path))
            except Tf.ErrorException as exc:
                raise OSError(f"cannot create {clip_path}: {exc}") from exc
            if clip_layer is None:
                raise OSError(f"cannot create {clip_path}")
            author_clip(clip_layer, clip, paths)
            if not clip_layer.Save():
                raise OSError(f"cannot save {clip_path}")
            layer.subLayerPaths.append(f"./{clips_dir.name}/{clip_path.name}")
        stage.SetStartTimeCode(0.0)
        stage.SetEndTimeCode(
            max(clip.duration() for clip in document.animations) * TIME_CODES_PER_SECOND
        )
    try:
        if not layer.Save():
            raise OSError(f"cannot save {path}")
    except Tf.ErrorException as exc:
        raise OSError(f"cannot save {path}: {exc}") from exc
    return path


def _prim_paths(
    transforms: Iterable[TransformComponent], is_taken: Callable[[Sdf.Path], bool]
) -> dict[str, Sdf.Path]:
    """Map transform names to prim paths that mirror the hierarchy."""
    by_name = {t.name: t for t in transforms}
    paths: dict[str, Sdf.Path] = {}

    def path_of(name: str, seen: frozenset[str]) -> Sdf.Path:
        if name in paths:
            return paths[name]
        parent = by_name[name].parent
        if parent in by_name and parent not in seen:
            base = path_of(parent, seen | {name})
        else:
            base = ROOT
        paths[name] = unique_child_path(
            base, name, lambda p: is_taken(p) or p in paths.values()
        )
        return paths[name]

    for name in by_name:
        path_of(name, frozenset())
    return paths


# -- load ---------------------------------------------------------------------


def load_usd_scene(path: Path) -> SceneDocument:
    """Read a USD stage at ``path`` into a scene document.

    Stages this app wrote come back unchanged. For stages from other tools,
    ``Xform`` hierarchies, ``Cube`` / ``Sphere`` / ``Plane`` prims, lights,
    and cameras are read; ``Mesh`` prims without a model file this app can
    load are skipped with a log message, since the viewport draws meshes
    from model files.

    Args:
        path: ``.usda``, ``.usdc``, or ``.usd`` file.

    Returns:
        The document, with mesh ``source`` paths made absolute.

    Raises:
        OSError: When the file cannot be read.
        ValueError: When it is not a USD layer this app can open.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    try:
        stage = Usd.Stage.Open(str(path))
    except Tf.ErrorException as exc:
        raise ValueError(f"not a USD file: {path.name} ({exc})") from exc
    if stage is None:
        raise ValueError(f"not a USD file: {path.name}")
    root = stage.GetPrimAtPath(ROOT)
    if not root:
        root = stage.GetDefaultPrim() or stage.GetPseudoRoot()

    transforms: list[_Keyed[TransformComponent]] = []
    meshes: list[_Keyed[MeshComponent]] = []
    lights: list[_Keyed[LightComponent]] = []
    cameras: list[_Keyed[CameraComponent]] = []
    names: dict[Sdf.Path, str] = {}
    used: set[str] = set()

    def unique(prim: Usd.Prim) -> str:
        name = read_name(prim)
        candidate, index = name, 1
        while candidate in used:
            candidate = f"{name}{index}"
            index += 1
        used.add(candidate)
        return candidate

    for prim in Usd.PrimRange(root):
        if prim == root or prim.GetPath().HasPrefix(LOOKS):
            continue
        prim_path = prim.GetPath()
        parent = names.get(prim_path.GetParentPath(), "")
        saved = read_index(prim)
        key: _SortKey = (0, saved, prim_path) if saved is not None else None
        if prim.IsA(UsdGeom.Camera):
            if parent:
                camera = read_camera(prim, unique(prim), parent)
                cameras.append((key, prim_path, camera))
        elif prim.HasAPI(UsdLux.LightAPI):
            if parent:
                lights.append((key, prim_path, read_light(prim, unique(prim), parent)))
        elif prim.IsA(UsdGeom.Gprim):
            mesh = read_mesh(prim, parent, path.parent)
            if mesh is not None:
                mesh = mesh.model_copy(update={"name": unique(prim)})
                meshes.append((key, prim_path, mesh))
        elif prim.IsA(UsdGeom.Xformable):
            name = unique(prim)
            names[prim_path] = name
            transform = read_transform(prim, name, parent)
            transforms.append((key, prim_path, transform))
    # Composition lists sublayer prims (the clips' overs) ahead of the root
    # layer's children, so put everything back in the saved order: the index
    # we wrote, or for other tools' files the root layer's authored order.
    order = _authored_order(stage.GetRootLayer(), root.GetPath())
    return SceneDocument(
        transforms=_ordered(transforms, order),
        meshes=_ordered(meshes, order),
        lights=_ordered(lights, order),
        cameras=_ordered(cameras, order),
        animations=_read_clips(stage, names),
    )


def _authored_order(layer: Sdf.Layer, root: Sdf.Path) -> dict[Sdf.Path, int]:
    """Depth-first position of each prim spec in ``layer`` under ``root``."""
    order: dict[Sdf.Path, int] = {}
    spec = layer.GetPrimAtPath(root)
    if spec is None:
        return order
    stack = list(reversed(list(spec.nameChildren)))
    while stack:
        child = stack.pop()
        order[child.path] = len(order)
        stack.extend(reversed(list(child.nameChildren)))
    return order


type _SortKey = tuple[int, int, Sdf.Path] | None
type _Keyed[T] = tuple[_SortKey, Sdf.Path, T]


def _ordered[T](items: list[_Keyed[T]], order: Mapping[Sdf.Path, int]) -> list[T]:
    """Sort by saved index, then by authored order for prims without one."""
    end = len(order)

    def key(item: _Keyed[T]) -> tuple[int, int, Sdf.Path]:
        saved, prim_path, _ = item
        if saved is not None:
            return saved
        return (1, order.get(prim_path, end), prim_path)

    return [item for _, _, item in sorted(items, key=key)]


def _read_clips(stage: Usd.Stage, names: Mapping[Sdf.Path, str]) -> list[AnimationClip]:
    root_layer = stage.GetRootLayer()
    clips: list[AnimationClip] = []
    for sub_path in root_layer.subLayerPaths:
        layer = Sdf.Layer.FindOrOpen(root_layer.ComputeAbsolutePath(sub_path))
        if layer is None:
            _log.warning("clip layer %s is missing, skipped", sub_path)
            continue
        clip = read_clip(layer, names)
        if clip is not None:
            clips.append(clip)
    return clips
