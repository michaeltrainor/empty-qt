"""Tests for scene viewport JSON save/load."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pydantic")

from examples.scene_viewport.components import (
    MeshComponent,
    TransformComponent,
)
from examples.scene_viewport.scene_io import SceneDocument, load_scene, save_scene


def test_scene_roundtrip(tmp_path: Path) -> None:
    """Scene documents survive a JSON roundtrip."""
    document = SceneDocument(
        transforms=[
            TransformComponent(name="root", visible=True),
            TransformComponent(name="child", parent="root", position=(1.0, 0.0, 0.0)),
        ],
        meshes=[
            MeshComponent(name="childShape", transform="child", kind="box"),
        ],
    )
    path = tmp_path / "scene.json"
    save_scene(document, path)
    loaded = load_scene(path)
    assert loaded.transforms[0].name == "root"
    assert loaded.transforms[1].parent == "root"
    assert loaded.meshes[0].kind == "box"
    assert loaded.transforms[1].visible is True
