"""Unit tests for SceneWorld document-model façade."""

from __future__ import annotations

from pathlib import Path

import pytest
from examples.scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from examples.scene_viewport.world import SceneWorld


def test_world_indexes_shapes_under_transform() -> None:
    """Meshes and lights index under their transform UID."""
    world = SceneWorld(
        transforms=[TransformComponent(name="geo1")],
        meshes=[
            MeshComponent(name="geo1Shape", transform="geo1", kind="box"),
        ],
        lights=[
            LightComponent(name="geo1Light", transform="geo1", kind="point"),
        ],
    )
    assert world.meshes_on("geo1") == ["geo1Shape"]
    assert world.lights_on("geo1") == ["geo1Light"]
    assert world.resolve_transform("geo1Shape") == "geo1"
    assert world.resolve_transform("geo1Light") == "geo1"


def test_world_rename_rewrites_links() -> None:
    """Renaming a transform updates shape links and child parents."""
    world = SceneWorld(
        transforms=[
            TransformComponent(name="parent"),
            TransformComponent(name="child", parent="parent"),
        ],
        meshes=[MeshComponent(name="parentShape", transform="parent", kind="box")],
    )
    world.rename_transform("parent", "root")
    assert "parent" not in world.transforms
    assert world.transforms["child"].parent == "root"
    assert world.meshes["parentShape"].transform == "root"
    assert world.resolve_transform("parentShape") == "root"


def test_world_remove_transform_drops_shapes() -> None:
    """Removing a transform also removes attached shapes."""
    world = SceneWorld(
        transforms=[TransformComponent(name="box")],
        meshes=[MeshComponent(name="boxShape", transform="box", kind="box")],
    )
    world.remove_transform("box")
    assert world.transforms == {}
    assert world.meshes == {}


def test_world_unique_name_and_document() -> None:
    """unique_name avoids collisions; to_document deep-copies components."""
    world = SceneWorld(transforms=[TransformComponent(name="null")])
    assert world.unique_name("null") == "null1"
    document = world.to_document()
    document.transforms[0].position = (9.0, 0.0, 0.0)
    assert world.transforms["null"].position == (0.0, 0.0, 0.0)


def test_world_rejects_orphan_mesh() -> None:
    """Meshes require an existing transform."""
    world = SceneWorld()
    with pytest.raises(KeyError):
        world.add_mesh(
            MeshComponent(name="orphan", transform="missing", kind="box"),
        )


def test_albedo_map_defaults_empty() -> None:
    """Mesh albedo_map defaults to an empty path."""
    mesh = MeshComponent(name="m", transform="t", kind="sphere")
    assert mesh.albedo_map == Path("")
