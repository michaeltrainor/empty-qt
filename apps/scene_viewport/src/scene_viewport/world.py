"""Document-model owner for scene viewport components, backed by a USD stage.

:class:`SceneWorld` keeps the façade the window, outliner, properties book,
and undo stack already use: pydantic components in insertion-ordered tables
keyed by name. Every mutation now also authors the same component onto a
live in-memory ``Usd.Stage`` through :mod:`scene_viewport.usd_prims`, so the
stage mirrors the tables exactly and can become the viewport's data source.
Animation clips are anonymous sublayers of the root layer, muted so the
composed stage shows the rest pose; playback will unmute one and move the
stage time.

The tables returned by :attr:`SceneWorld.transforms` and friends are live
views: ``world.transforms[name] = component`` routes through
:meth:`set_transform` or :meth:`add_transform`, so direct writes keep the
stage in step too. Viewport mirroring stays in ``ViewportController``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, MutableMapping, Sequence
from typing import Any

from pxr import Sdf, Usd

from scene_viewport.animation import AnimationClip
from scene_viewport.components import (
    CameraComponent,
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.scene import FOLDER_NAMES
from scene_viewport.scene_io import SceneDocument
from scene_viewport.usd_prims import (
    ROOT,
    TIME_CODES_PER_SECOND,
    author_camera,
    author_clip,
    author_light,
    author_mesh_surface,
    author_transform,
    configure_stage,
    define_camera,
    define_light,
    define_mesh,
    define_transform,
    identifier,
    material_path,
    unique_child_path,
)


class _PathTable(dict[str, Sdf.Path]):
    """Component name → prim path, with constant-time ``has_path``.

    Sibling-path checks run once per prim added, so a linear scan of the
    values made opening a few thousand prims quadratic.
    """

    def __init__(self) -> None:
        super().__init__()
        self._paths: set[Sdf.Path] = set()

    def __setitem__(self, name: str, path: Sdf.Path) -> None:
        old = self.get(name)
        if old is not None:
            self._paths.discard(old)
        super().__setitem__(name, path)
        self._paths.add(path)

    def __delitem__(self, name: str) -> None:
        self._paths.discard(self[name])
        super().__delitem__(name)

    def pop(self, name: str, *default: Sdf.Path) -> Sdf.Path:  # type: ignore[override]
        path = super().pop(name, *default)
        self._paths.discard(path)
        return path

    def clear(self) -> None:
        super().clear()
        self._paths.clear()

    def has_path(self, path: Sdf.Path) -> bool:
        """Whether ``path`` is the prim path of any component."""
        return path in self._paths


class _Table[T: Any](MutableMapping[str, T]):
    """A live, ordered view of one component table.

    Reads come from the world's cache; writes go through the world's add
    and set methods, deletes through its remove method, so the stage stays
    in step with direct ``world.transforms[name] = component`` writes.
    """

    def __init__(
        self,
        cache: dict[str, T],
        add: Callable[[T], None],
        replace: Callable[[T], None],
        remove: Callable[[str], object],
    ) -> None:
        self._cache = cache
        self._add = add
        self._replace = replace
        self._remove = remove

    def __getitem__(self, name: str) -> T:
        return self._cache[name]

    def __setitem__(self, name: str, component: T) -> None:
        if component.name != name:
            msg = f"key {name!r} does not match component name {component.name!r}"
            raise KeyError(msg)
        if name in self._cache:
            self._replace(component)
        else:
            self._add(component)

    def __delitem__(self, name: str) -> None:
        if name not in self._cache:
            raise KeyError(name)
        self._remove(name)

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._cache))

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, name: object) -> bool:
        return name in self._cache

    def __repr__(self) -> str:
        return repr(self._cache)


class SceneWorld:
    """Owns transforms, meshes, lights, cameras, and clips keyed by name.

    Keeps reverse indexes (shape → transform, transform → shapes) in sync
    so the window does not invent parallel dicts, and mirrors every
    component onto :attr:`stage`. Animation clips live in their own
    namespace: a clip may share its name with a transform.
    """

    def __init__(
        self,
        transforms: Iterable[TransformComponent] = (),
        meshes: Iterable[MeshComponent] = (),
        lights: Iterable[LightComponent] = (),
        cameras: Iterable[CameraComponent] = (),
        animations: Iterable[AnimationClip] = (),
    ) -> None:
        """Create a world from optional seed components.

        Args:
            transforms: Initial transform nodes, parents before children.
            meshes: Initial mesh shapes.
            lights: Initial light shapes.
            cameras: Initial camera shapes.
            animations: Initial clips; their channels must name transforms
                in ``transforms``.
        """
        self._stage: Usd.Stage = Usd.Stage.CreateInMemory()
        configure_stage(self._stage)
        self._root_layer: Sdf.Layer = self._stage.GetRootLayer()
        self._transforms: dict[str, TransformComponent] = {}
        self._meshes: dict[str, MeshComponent] = {}
        self._lights: dict[str, LightComponent] = {}
        self._cameras: dict[str, CameraComponent] = {}
        self._animations: dict[str, AnimationClip] = {}
        self._mesh_to_transform: dict[str, str] = {}
        self._light_to_transform: dict[str, str] = {}
        self._camera_to_transform: dict[str, str] = {}
        self._meshes_by_transform: dict[str, list[str]] = {}
        self._lights_by_transform: dict[str, list[str]] = {}
        self._cameras_by_transform: dict[str, list[str]] = {}
        # Component name → prim path, for transforms and shapes alike.
        self._paths = _PathTable()
        self._clip_layers: dict[str, Sdf.Layer] = {}
        self._transform_table = _Table(
            self._transforms,
            self.add_transform,
            self.set_transform,
            self.remove_transform,
        )
        self._mesh_table = _Table(
            self._meshes, self.add_mesh, self.set_mesh, self.remove_mesh
        )
        self._light_table = _Table(
            self._lights, self.add_light, self.set_light, self.remove_light
        )
        self._camera_table = _Table(
            self._cameras, self.add_camera, self.set_camera, self.remove_camera
        )
        self._animation_table = _Table(
            self._animations,
            self.add_animation,
            self.set_animation,
            self.remove_animation,
        )
        for item in transforms:
            self.add_transform(item)
        for item in meshes:
            self.add_mesh(item)
        for item in lights:
            self.add_light(item)
        for item in cameras:
            self.add_camera(item)
        for item in animations:
            self.add_animation(item)

    # -- stage ---------------------------------------------------------------

    @property
    def stage(self) -> Usd.Stage:
        """The live stage mirroring every component (read it, author via methods)."""
        return self._stage

    def prim_path(self, name: str) -> Sdf.Path | None:
        """Return the prim path of a transform, mesh, light, or camera.

        Args:
            name: Component name.
        """
        return self._paths.get(name)

    def clip_layer(self, name: str) -> Sdf.Layer | None:
        """Return the anonymous sublayer holding clip ``name``, or ``None``.

        Args:
            name: Clip name.
        """
        return self._clip_layers.get(name)

    def _is_taken(self, path: Sdf.Path) -> bool:
        return self._paths.has_path(path) or self._stage.GetPrimAtPath(path).IsValid()

    def _parent_path(self, parent: str) -> Sdf.Path:
        return self._paths.get(parent, ROOT) if parent else ROOT

    def _index_of(self, table: dict[str, Any], name: str) -> int:
        return list(table).index(name)

    def _move_subtree(self, old: Sdf.Path, new: Sdf.Path) -> set[str]:
        """Move a prim and everything under it; return the transforms moved."""
        Sdf.CopySpec(self._root_layer, old, self._root_layer, new)
        self._stage.RemovePrim(old)
        moved: set[str] = set()
        for name, path in list(self._paths.items()):
            if path.HasPrefix(old):
                self._paths[name] = path.ReplacePrefix(old, new)
                if name in self._transforms:
                    moved.add(name)
        return moved

    def _rewrite_clips_for(self, transforms: set[str]) -> None:
        """Re-author the clip layers whose channels target ``transforms``."""
        for clip in self._animations.values():
            if clip.targets() & transforms:
                author_clip(self._clip_layers[clip.name], clip, self._paths)

    def _update_time_range(self) -> None:
        end = max((clip.duration() for clip in self._animations.values()), default=0.0)
        self._stage.SetStartTimeCode(0.0)
        self._stage.SetEndTimeCode(end * TIME_CODES_PER_SECOND)

    # -- tables --------------------------------------------------------------

    @property
    def transforms(self) -> MutableMapping[str, TransformComponent]:
        """Transform UID → component (live view; writes author the stage)."""
        return self._transform_table

    @property
    def meshes(self) -> MutableMapping[str, MeshComponent]:
        """Mesh UID → component (live view)."""
        return self._mesh_table

    @property
    def lights(self) -> MutableMapping[str, LightComponent]:
        """Light UID → component (live view)."""
        return self._light_table

    @property
    def cameras(self) -> MutableMapping[str, CameraComponent]:
        """Camera UID → component (live view)."""
        return self._camera_table

    @property
    def animations(self) -> MutableMapping[str, AnimationClip]:
        """Clip name → clip, in insertion order (live view)."""
        return self._animation_table

    def meshes_on(self, transform: str) -> list[str]:
        """Mesh UIDs parented under ``transform``."""
        return list(self._meshes_by_transform.get(transform, ()))

    def lights_on(self, transform: str) -> list[str]:
        """Light UIDs parented under ``transform``."""
        return list(self._lights_by_transform.get(transform, ()))

    def cameras_on(self, transform: str) -> list[str]:
        """Camera UIDs parented under ``transform``."""
        return list(self._cameras_by_transform.get(transform, ()))

    def used_names(self) -> set[str]:
        """All component UIDs plus outliner folder names."""
        return (
            set(self._transforms)
            | set(self._meshes)
            | set(self._lights)
            | set(self._cameras)
            | set(FOLDER_NAMES)
        )

    def unique_name(self, stem: str) -> str:
        """Return ``stem`` or ``stemN`` that is not yet used.

        Args:
            stem: Preferred base name.
        """
        used = self.used_names()
        if stem not in used:
            return stem
        index = 1
        while f"{stem}{index}" in used:
            index += 1
        return f"{stem}{index}"

    def resolve_transform(self, name: str) -> str | None:
        """Map a transform, mesh, light, or camera UID to its transform UID.

        Args:
            name: Any scene object UID from the outliner or pick path.
        """
        if name in self._transforms:
            return name
        if name in self._mesh_to_transform:
            return self._mesh_to_transform[name]
        if name in self._light_to_transform:
            return self._light_to_transform[name]
        if name in self._camera_to_transform:
            return self._camera_to_transform[name]
        return None

    def get_transform(self, name: str) -> TransformComponent | None:
        """Return a transform by UID, or ``None``."""
        return self._transforms.get(name)

    def get_mesh(self, name: str) -> MeshComponent | None:
        """Return a mesh by UID, or ``None``."""
        return self._meshes.get(name)

    def get_light(self, name: str) -> LightComponent | None:
        """Return a light by UID, or ``None``."""
        return self._lights.get(name)

    def get_camera(self, name: str) -> CameraComponent | None:
        """Return a camera by UID, or ``None``."""
        return self._cameras.get(name)

    def get_animation(self, name: str) -> AnimationClip | None:
        """Return a clip by name, or ``None``."""
        return self._animations.get(name)

    # -- transforms ----------------------------------------------------------

    def add_transform(self, component: TransformComponent) -> None:
        """Insert a transform and empty shape lists.

        Its prim goes under its parent's prim, or under ``/World`` when the
        parent is not in the world yet.

        Args:
            component: Transform to store.

        Raises:
            KeyError: When the UID exists.
        """
        if component.name in self._transforms:
            msg = f"transform already exists: {component.name!r}"
            raise KeyError(msg)
        path = unique_child_path(
            self._parent_path(component.parent), component.name, self._is_taken
        )
        define_transform(self._stage, path, component, len(self._transforms))
        self._paths[component.name] = path
        self._transforms[component.name] = component
        self._meshes_by_transform.setdefault(component.name, [])
        self._lights_by_transform.setdefault(component.name, [])
        self._cameras_by_transform.setdefault(component.name, [])

    def set_transform(self, component: TransformComponent) -> None:
        """Replace a stored transform (same UID).

        A changed ``parent`` moves the prim, and everything under it, to the
        new parent's prim.

        Args:
            component: Updated transform snapshot.

        Raises:
            KeyError: When the UID is missing.
            ValueError: When the new parent is the transform or a descendant.
        """
        stored = self._transforms.get(component.name)
        if stored is None:
            msg = f"transform not found: {component.name!r}"
            raise KeyError(msg)
        path = self._paths[component.name]
        if stored.parent != component.parent:
            parent_path = self._parent_path(component.parent)
            if parent_path.HasPrefix(path):
                msg = f"{component.name!r} cannot be parented under itself"
                raise ValueError(msg)
            new_path = unique_child_path(parent_path, component.name, self._is_taken)
            moved = self._move_subtree(path, new_path)
            path = new_path
            self._transforms[component.name] = component
            self._rewrite_clips_for(moved)
        else:
            self._transforms[component.name] = component
        author_transform(
            self._stage.GetPrimAtPath(path),
            component,
            self._index_of(self._transforms, component.name),
        )

    def remove_transform(self, name: str) -> TransformComponent | None:
        """Remove a transform and all meshes, lights, and cameras under it.

        Channels animating it are dropped (a clip left empty goes too).
        Child transforms still in the world move up under its parent.

        Args:
            name: Transform UID.

        Returns:
            The removed transform, or ``None`` when missing.
        """
        if name not in self._transforms:
            return None
        for mesh_name in list(self._meshes_by_transform.get(name, ())):
            self.remove_mesh(mesh_name)
        for light_name in list(self._lights_by_transform.get(name, ())):
            self.remove_light(light_name)
        for camera_name in list(self._cameras_by_transform.get(name, ())):
            self.remove_camera(camera_name)
        self._meshes_by_transform.pop(name, None)
        self._lights_by_transform.pop(name, None)
        self._cameras_by_transform.pop(name, None)
        self._drop_channels(name)
        removed = self._transforms.pop(name)
        for child_name, child in list(self._transforms.items()):
            if child.parent == name:
                self.set_transform(child.model_copy(update={"parent": removed.parent}))
                del child_name
        path = self._paths.pop(name)
        self._stage.RemovePrim(path)
        return removed

    def rename_transform(self, old_name: str, new_name: str) -> TransformComponent:
        """Rename a transform and rewrite child parent / shape links.

        Args:
            old_name: Existing transform UID.
            new_name: Unused new UID.

        Returns:
            The updated transform component.

        Raises:
            KeyError: When ``old_name`` is missing or ``new_name`` is taken.
        """
        if old_name not in self._transforms:
            msg = f"transform not found: {old_name!r}"
            raise KeyError(msg)
        if new_name in self.used_names():
            msg = f"name already in use: {new_name!r}"
            raise KeyError(msg)
        stored = self._transforms.pop(old_name)
        updated = stored.model_copy(update={"name": new_name})
        self._transforms[new_name] = updated
        old_path = self._paths[old_name]
        new_path = unique_child_path(old_path.GetParentPath(), new_name, self._is_taken)
        moved = self._move_subtree(old_path, new_path)
        self._paths[new_name] = self._paths.pop(old_name)
        moved.discard(old_name)
        moved.add(new_name)
        self._meshes_by_transform[new_name] = self._meshes_by_transform.pop(
            old_name, []
        )
        self._lights_by_transform[new_name] = self._lights_by_transform.pop(
            old_name, []
        )
        for mesh_name in self._meshes_by_transform.get(new_name, ()):
            mesh = self._meshes[mesh_name]
            self._meshes[mesh_name] = mesh.model_copy(update={"transform": new_name})
            self._mesh_to_transform[mesh_name] = new_name
        for light_name in self._lights_by_transform.get(new_name, ()):
            light = self._lights[light_name]
            self._lights[light_name] = light.model_copy(update={"transform": new_name})
            self._light_to_transform[light_name] = new_name
        self._cameras_by_transform[new_name] = self._cameras_by_transform.pop(
            old_name, []
        )
        for camera_name in self._cameras_by_transform.get(new_name, ()):
            camera = self._cameras[camera_name]
            self._cameras[camera_name] = camera.model_copy(
                update={"transform": new_name}
            )
            self._camera_to_transform[camera_name] = new_name
        for child_name, child in list(self._transforms.items()):
            if child.parent == old_name:
                self._transforms[child_name] = child.model_copy(
                    update={"parent": new_name}
                )
        for clip_name, clip in list(self._animations.items()):
            if any(channel.transform == old_name for channel in clip.channels):
                self._animations[clip_name] = clip.model_copy(
                    update={
                        "channels": [
                            channel.model_copy(update={"transform": new_name})
                            if channel.transform == old_name
                            else channel
                            for channel in clip.channels
                        ]
                    }
                )
        author_transform(
            self._stage.GetPrimAtPath(new_path),
            updated,
            self._index_of(self._transforms, new_name),
        )
        self._rewrite_clips_for(moved)
        return updated

    def reorder_transforms(self, names: Sequence[str]) -> None:
        """Reorder the transform table, which sets outliner and file order.

        Args:
            names: Transform UIDs in the wanted order. Unknown names are
                skipped, and transforms not listed keep their relative order
                after the listed ones.
        """
        ordered: dict[str, TransformComponent] = {}
        for name in names:
            stored = self._transforms.get(name)
            if stored is not None and name not in ordered:
                ordered[name] = stored
        for name, stored in self._transforms.items():
            ordered.setdefault(name, stored)
        self._transforms.clear()
        self._transforms.update(ordered)
        for index, name in enumerate(self._transforms):
            prim = self._stage.GetPrimAtPath(self._paths[name])
            prim.SetCustomDataByKey("mtqt:index", index)

    def descendant_transforms(self, roots: list[str]) -> list[str]:
        """Return ``roots`` plus descendants, parents before children.

        Args:
            roots: Transform UIDs to expand.
        """
        children: dict[str, list[str]] = {}
        for transform in self._transforms.values():
            children.setdefault(transform.parent, []).append(transform.name)

        ordered: list[str] = []
        seen: set[str] = set()

        def walk(name: str) -> None:
            if name in seen or name not in self._transforms:
                return
            seen.add(name)
            ordered.append(name)
            for child in children.get(name, ()):
                walk(child)

        for root in roots:
            walk(root)
        return ordered

    # -- shapes --------------------------------------------------------------

    def _shape_path(
        self, component: MeshComponent | LightComponent | CameraComponent
    ) -> Sdf.Path:
        if component.transform not in self._transforms:
            msg = f"{component.name!r} missing transform {component.transform!r}"
            raise KeyError(msg)
        return unique_child_path(
            self._paths[component.transform], component.name, self._is_taken
        )

    def add_mesh(self, component: MeshComponent) -> None:
        """Insert a mesh and index it under its transform.

        File meshes get their triangles embedded in the stage when the model
        file loads, so other USD readers see geometry.

        Args:
            component: Mesh to store.

        Raises:
            KeyError: When the UID exists or its transform is missing.
        """
        if component.name in self._meshes:
            msg = f"mesh already exists: {component.name!r}"
            raise KeyError(msg)
        path = self._shape_path(component)
        define_mesh(self._stage, path, component, None, len(self._meshes))
        self._paths[component.name] = path
        self._meshes[component.name] = component
        self._mesh_to_transform[component.name] = component.transform
        self._meshes_by_transform.setdefault(component.transform, []).append(
            component.name
        )

    def set_mesh(self, component: MeshComponent) -> None:
        """Replace a stored mesh (same UID and transform link).

        Args:
            component: Updated mesh snapshot.
        """
        if component.name not in self._meshes:
            msg = f"mesh not found: {component.name!r}"
            raise KeyError(msg)
        self._meshes[component.name] = component
        self._mesh_to_transform[component.name] = component.transform
        author_mesh_surface(
            self._stage,
            self._stage.GetPrimAtPath(self._paths[component.name]),
            component,
            self._index_of(self._meshes, component.name),
        )

    def remove_mesh(self, name: str) -> MeshComponent | None:
        """Remove a mesh, its material, and its indexes.

        Args:
            name: Mesh UID.

        Returns:
            The removed component, or ``None`` when missing.
        """
        mesh = self._meshes.pop(name, None)
        if mesh is None:
            return None
        self._mesh_to_transform.pop(name, None)
        under = self._meshes_by_transform.get(mesh.transform)
        if under and name in under:
            under.remove(name)
        path = self._paths.pop(name)
        self._stage.RemovePrim(material_path(path.name))
        self._stage.RemovePrim(path)
        return mesh

    def add_light(self, component: LightComponent) -> None:
        """Insert a light and index it under its transform.

        Args:
            component: Light to store.

        Raises:
            KeyError: When the UID exists or its transform is missing.
        """
        if component.name in self._lights:
            msg = f"light already exists: {component.name!r}"
            raise KeyError(msg)
        path = self._shape_path(component)
        define_light(self._stage, path, component, len(self._lights))
        self._paths[component.name] = path
        self._lights[component.name] = component
        self._light_to_transform[component.name] = component.transform
        self._lights_by_transform.setdefault(component.transform, []).append(
            component.name
        )

    def set_light(self, component: LightComponent) -> None:
        """Replace a stored light (same UID and transform link).

        Args:
            component: Updated light snapshot.
        """
        if component.name not in self._lights:
            msg = f"light not found: {component.name!r}"
            raise KeyError(msg)
        self._lights[component.name] = component
        self._light_to_transform[component.name] = component.transform
        author_light(
            self._stage.GetPrimAtPath(self._paths[component.name]),
            component,
            self._index_of(self._lights, component.name),
        )

    def remove_light(self, name: str) -> LightComponent | None:
        """Remove a light and drop its indexes.

        Args:
            name: Light UID.

        Returns:
            The removed component, or ``None`` when missing.
        """
        light = self._lights.pop(name, None)
        if light is None:
            return None
        self._light_to_transform.pop(name, None)
        under = self._lights_by_transform.get(light.transform)
        if under and name in under:
            under.remove(name)
        self._stage.RemovePrim(self._paths.pop(name))
        return light

    def add_camera(self, component: CameraComponent) -> None:
        """Insert a camera and index it under its transform.

        Args:
            component: Camera to store.

        Raises:
            KeyError: When the UID exists or its transform is missing.
        """
        if component.name in self._cameras:
            msg = f"camera already exists: {component.name!r}"
            raise KeyError(msg)
        path = self._shape_path(component)
        define_camera(self._stage, path, component, len(self._cameras))
        self._paths[component.name] = path
        self._cameras[component.name] = component
        self._camera_to_transform[component.name] = component.transform
        self._cameras_by_transform.setdefault(component.transform, []).append(
            component.name
        )

    def set_camera(self, component: CameraComponent) -> None:
        """Replace a stored camera (same UID and transform link).

        Args:
            component: Updated camera snapshot.
        """
        if component.name not in self._cameras:
            msg = f"camera not found: {component.name!r}"
            raise KeyError(msg)
        self._cameras[component.name] = component
        self._camera_to_transform[component.name] = component.transform
        author_camera(
            self._stage.GetPrimAtPath(self._paths[component.name]),
            component,
            self._index_of(self._cameras, component.name),
        )

    def remove_camera(self, name: str) -> CameraComponent | None:
        """Remove a camera and drop its indexes.

        Args:
            name: Camera UID.

        Returns:
            The removed component, or ``None`` when missing.
        """
        camera = self._cameras.pop(name, None)
        if camera is None:
            return None
        self._camera_to_transform.pop(name, None)
        under = self._cameras_by_transform.get(camera.transform)
        if under and name in under:
            under.remove(name)
        self._stage.RemovePrim(self._paths.pop(name))
        return camera

    # -- animations ----------------------------------------------------------

    def unique_clip_name(self, stem: str) -> str:
        """Return ``stem`` or ``stem_N`` that no clip uses yet.

        Args:
            stem: Preferred clip name.
        """
        return unique_clip_name(stem, self._animations)

    def add_animation(self, clip: AnimationClip) -> None:
        """Insert a clip as a muted sublayer of time samples.

        Args:
            clip: Clip to store.

        Raises:
            KeyError: When a clip of that name exists, or a channel names a
                transform the world does not have.
        """
        if clip.name in self._animations:
            msg = f"animation already exists: {clip.name!r}"
            raise KeyError(msg)
        self._check_targets(clip)
        layer = Sdf.Layer.CreateAnonymous(f"{identifier(clip.name)}.usda")
        author_clip(layer, clip, self._paths)
        self._root_layer.subLayerPaths.append(layer.identifier)
        self._stage.MuteLayer(layer.identifier)
        self._clip_layers[clip.name] = layer
        self._animations[clip.name] = clip
        self._update_time_range()

    def set_animation(self, clip: AnimationClip) -> None:
        """Replace a stored clip (same name).

        Args:
            clip: Updated clip.

        Raises:
            KeyError: When no clip of that name exists, or a channel names
                a transform the world does not have.
        """
        if clip.name not in self._animations:
            msg = f"animation not found: {clip.name!r}"
            raise KeyError(msg)
        self._check_targets(clip)
        self._animations[clip.name] = clip
        author_clip(self._clip_layers[clip.name], clip, self._paths)
        self._update_time_range()

    def remove_animation(self, name: str) -> AnimationClip | None:
        """Remove a clip and its sublayer.

        Args:
            name: Clip name.

        Returns:
            The removed clip, or ``None`` when missing.
        """
        clip = self._animations.pop(name, None)
        if clip is None:
            return None
        layer = self._clip_layers.pop(name)
        self._stage.UnmuteLayer(layer.identifier)
        self._root_layer.subLayerPaths.remove(layer.identifier)
        self._update_time_range()
        return clip

    def animations_targeting(self, transform: str) -> list[str]:
        """Names of the clips with a channel on ``transform``."""
        return [
            clip.name
            for clip in self._animations.values()
            if any(channel.transform == transform for channel in clip.channels)
        ]

    def _check_targets(self, clip: AnimationClip) -> None:
        missing = sorted(clip.targets() - set(self._transforms))
        if missing:
            msg = f"animation {clip.name!r} targets missing transforms: {missing}"
            raise KeyError(msg)

    def _drop_channels(self, transform: str) -> None:
        """Forget the channels on ``transform``; drop clips that empties."""
        for clip_name, clip in list(self._animations.items()):
            kept = [c for c in clip.channels if c.transform != transform]
            if len(kept) == len(clip.channels):
                continue
            if kept:
                self.set_animation(clip.model_copy(update={"channels": kept}))
            else:
                self.remove_animation(clip_name)

    def iter_animations(self) -> Iterator[AnimationClip]:
        """Iterate clips in insertion order."""
        yield from self._animations.values()

    # -- whole-scene ---------------------------------------------------------

    def clear(self) -> None:
        """Remove every component, prim, and clip layer."""
        for layer in self._clip_layers.values():
            self._stage.UnmuteLayer(layer.identifier)
        self._root_layer.subLayerPaths.clear()
        world = self._stage.GetPrimAtPath(ROOT)
        for child in list(world.GetChildren()):
            self._stage.RemovePrim(child.GetPath())
        self._transforms.clear()
        self._meshes.clear()
        self._lights.clear()
        self._cameras.clear()
        self._animations.clear()
        self._mesh_to_transform.clear()
        self._light_to_transform.clear()
        self._camera_to_transform.clear()
        self._meshes_by_transform.clear()
        self._lights_by_transform.clear()
        self._cameras_by_transform.clear()
        self._paths.clear()
        self._clip_layers.clear()
        self._update_time_range()

    def replace_all(
        self,
        transforms: Iterable[TransformComponent],
        meshes: Iterable[MeshComponent],
        lights: Iterable[LightComponent],
        cameras: Iterable[CameraComponent] = (),
        animations: Iterable[AnimationClip] = (),
    ) -> None:
        """Replace the entire world contents.

        Channels that name a transform not in ``transforms`` are dropped,
        and a clip left without channels with them, so a hand-edited scene
        file still opens.

        Args:
            transforms: New transforms.
            meshes: New meshes.
            lights: New lights.
            cameras: New cameras.
            animations: New clips.
        """
        self.clear()
        for item in transforms:
            self.add_transform(item)
        for item in meshes:
            self.add_mesh(item)
        for item in lights:
            self.add_light(item)
        for item in cameras:
            self.add_camera(item)
        for item in animations:
            pruned = prune_clip(item, self._transforms)
            if pruned is not None and pruned.name not in self._animations:
                self.add_animation(pruned)

    def to_document(self) -> SceneDocument:
        """Snapshot the world as a serializable document."""
        return SceneDocument(
            transforms=[
                item.model_copy(deep=True) for item in self._transforms.values()
            ],
            meshes=[item.model_copy(deep=True) for item in self._meshes.values()],
            lights=[item.model_copy(deep=True) for item in self._lights.values()],
            cameras=[item.model_copy(deep=True) for item in self._cameras.values()],
            animations=[
                item.model_copy(deep=True) for item in self._animations.values()
            ],
        )

    def iter_cameras(self) -> Iterator[CameraComponent]:
        """Iterate cameras in insertion order."""
        yield from self._cameras.values()

    def iter_transforms(self) -> Iterator[TransformComponent]:
        """Iterate transforms in insertion order."""
        yield from self._transforms.values()

    def iter_meshes(self) -> Iterator[MeshComponent]:
        """Iterate meshes in insertion order."""
        yield from self._meshes.values()

    def iter_lights(self) -> Iterator[LightComponent]:
        """Iterate lights in insertion order."""
        yield from self._lights.values()


def unique_clip_name(stem: str, taken: Iterable[str]) -> str:
    """Return ``stem`` or ``stem_N`` that is not in ``taken``.

    Args:
        stem: Preferred clip name.
        taken: Clip names already in use.
    """
    used = set(taken)
    if stem not in used:
        return stem
    index = 1
    while f"{stem}_{index}" in used:
        index += 1
    return f"{stem}_{index}"


def prune_clip(clip: AnimationClip, transforms: Iterable[str]) -> AnimationClip | None:
    """Return ``clip`` without channels on transforms not in ``transforms``.

    Args:
        clip: Clip to filter.
        transforms: Transform names that exist.

    Returns:
        The clip, a copy with fewer channels, or ``None`` when no channel
        is left.
    """
    present = set(transforms)
    kept = [channel for channel in clip.channels if channel.transform in present]
    if not kept:
        return None
    if len(kept) == len(clip.channels):
        return clip
    return clip.model_copy(update={"channels": kept})
