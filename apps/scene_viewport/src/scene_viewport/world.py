"""Document-model owner for scene viewport components.

Not a full ECS: one façade over pydantic component tables plus the
indexes the outliner, properties book, and undo stack need. Viewport
mirroring stays in :class:`ViewportController`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.scene import FOLDER_NAMES
from scene_viewport.scene_io import SceneDocument


class SceneWorld:
    """Owns transforms, meshes, and lights keyed by stable string UIDs.

    Keeps reverse indexes (shape → transform, transform → shapes) in sync
    so the window does not invent parallel dicts.
    """

    def __init__(
        self,
        transforms: Iterable[TransformComponent] = (),
        meshes: Iterable[MeshComponent] = (),
        lights: Iterable[LightComponent] = (),
    ) -> None:
        """Create a world from optional seed components.

        Args:
            transforms: Initial transform nodes.
            meshes: Initial mesh shapes.
            lights: Initial light shapes.
        """
        self._transforms: dict[str, TransformComponent] = {}
        self._meshes: dict[str, MeshComponent] = {}
        self._lights: dict[str, LightComponent] = {}
        self._mesh_to_transform: dict[str, str] = {}
        self._light_to_transform: dict[str, str] = {}
        self._meshes_by_transform: dict[str, list[str]] = {}
        self._lights_by_transform: dict[str, list[str]] = {}
        for item in transforms:
            self.add_transform(item)
        for item in meshes:
            self.add_mesh(item)
        for item in lights:
            self.add_light(item)

    @property
    def transforms(self) -> dict[str, TransformComponent]:
        """Transform UID → component (mutable mapping; prefer world methods)."""
        return self._transforms

    @property
    def meshes(self) -> dict[str, MeshComponent]:
        """Mesh UID → component."""
        return self._meshes

    @property
    def lights(self) -> dict[str, LightComponent]:
        """Light UID → component."""
        return self._lights

    def meshes_on(self, transform: str) -> list[str]:
        """Mesh UIDs parented under ``transform``."""
        return list(self._meshes_by_transform.get(transform, ()))

    def lights_on(self, transform: str) -> list[str]:
        """Light UIDs parented under ``transform``."""
        return list(self._lights_by_transform.get(transform, ()))

    def used_names(self) -> set[str]:
        """All component UIDs plus outliner folder names."""
        return (
            set(self._transforms)
            | set(self._meshes)
            | set(self._lights)
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
        """Map a transform, mesh, or light UID to its transform UID.

        Args:
            name: Any scene object UID from the outliner or pick path.
        """
        if name in self._transforms:
            return name
        if name in self._mesh_to_transform:
            return self._mesh_to_transform[name]
        if name in self._light_to_transform:
            return self._light_to_transform[name]
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

    def set_transform(self, component: TransformComponent) -> None:
        """Replace a stored transform (same UID).

        Args:
            component: Updated transform snapshot.
        """
        if component.name not in self._transforms:
            msg = f"transform not found: {component.name!r}"
            raise KeyError(msg)
        self._transforms[component.name] = component

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

    def add_transform(self, component: TransformComponent) -> None:
        """Insert a transform and empty shape lists.

        Args:
            component: Transform to store.
        """
        if component.name in self._transforms:
            msg = f"transform already exists: {component.name!r}"
            raise KeyError(msg)
        self._transforms[component.name] = component
        self._meshes_by_transform.setdefault(component.name, [])
        self._lights_by_transform.setdefault(component.name, [])

    def add_mesh(self, component: MeshComponent) -> None:
        """Insert a mesh and index it under its transform.

        Args:
            component: Mesh to store.
        """
        if component.name in self._meshes:
            msg = f"mesh already exists: {component.name!r}"
            raise KeyError(msg)
        if component.transform not in self._transforms:
            msg = f"mesh {component.name!r} missing transform {component.transform!r}"
            raise KeyError(msg)
        self._meshes[component.name] = component
        self._mesh_to_transform[component.name] = component.transform
        self._meshes_by_transform.setdefault(component.transform, []).append(
            component.name
        )

    def add_light(self, component: LightComponent) -> None:
        """Insert a light and index it under its transform.

        Args:
            component: Light to store.
        """
        if component.name in self._lights:
            msg = f"light already exists: {component.name!r}"
            raise KeyError(msg)
        if component.transform not in self._transforms:
            msg = f"light {component.name!r} missing transform {component.transform!r}"
            raise KeyError(msg)
        self._lights[component.name] = component
        self._light_to_transform[component.name] = component.transform
        self._lights_by_transform.setdefault(component.transform, []).append(
            component.name
        )

    def remove_mesh(self, name: str) -> MeshComponent | None:
        """Remove a mesh and drop its indexes.

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
        return mesh

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
        return light

    def remove_transform(self, name: str) -> TransformComponent | None:
        """Remove a transform and all meshes/lights under it.

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
        self._meshes_by_transform.pop(name, None)
        self._lights_by_transform.pop(name, None)
        return self._transforms.pop(name)

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
        for child_name, child in list(self._transforms.items()):
            if child.parent == old_name:
                self._transforms[child_name] = child.model_copy(
                    update={"parent": new_name}
                )
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

    def clear(self) -> None:
        """Remove every component."""
        self._transforms.clear()
        self._meshes.clear()
        self._lights.clear()
        self._mesh_to_transform.clear()
        self._light_to_transform.clear()
        self._meshes_by_transform.clear()
        self._lights_by_transform.clear()

    def replace_all(
        self,
        transforms: Iterable[TransformComponent],
        meshes: Iterable[MeshComponent],
        lights: Iterable[LightComponent],
    ) -> None:
        """Replace the entire world contents.

        Args:
            transforms: New transforms.
            meshes: New meshes.
            lights: New lights.
        """
        self.clear()
        for item in transforms:
            self.add_transform(item)
        for item in meshes:
            self.add_mesh(item)
        for item in lights:
            self.add_light(item)

    def to_document(self) -> SceneDocument:
        """Snapshot the world as a serializable document."""
        return SceneDocument(
            transforms=[
                item.model_copy(deep=True) for item in self._transforms.values()
            ],
            meshes=[item.model_copy(deep=True) for item in self._meshes.values()],
            lights=[item.model_copy(deep=True) for item in self._lights.values()],
        )

    def iter_transforms(self) -> Iterator[TransformComponent]:
        """Iterate transforms in insertion order."""
        yield from self._transforms.values()

    def iter_meshes(self) -> Iterator[MeshComponent]:
        """Iterate meshes in insertion order."""
        yield from self._meshes.values()

    def iter_lights(self) -> Iterator[LightComponent]:
        """Iterate lights in insertion order."""
        yield from self._lights.values()
