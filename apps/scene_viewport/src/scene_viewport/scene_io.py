"""Load and save scene viewport component graphs as JSON."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)


class SceneDocument(BaseModel):
    """Serializable scene graph for the viewport example.

    Args:
        transforms: Transform components (parents before children preferred).
        meshes: Mesh shapes referencing transform names.
        lights: Light shapes referencing transform names.
    """

    version: int = Field(default=1, ge=1)
    transforms: list[TransformComponent] = Field(default_factory=list)
    meshes: list[MeshComponent] = Field(default_factory=list)
    lights: list[LightComponent] = Field(default_factory=list)


def load_scene(path: Path) -> SceneDocument:
    """Load a scene document from ``path``.

    Args:
        path: JSON file path.

    Returns:
        Parsed :class:`SceneDocument`.

    Raises:
        OSError: When the file cannot be read.
        ValueError: When the JSON is invalid for this schema.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SceneDocument.model_validate(payload)


def save_scene(document: SceneDocument, path: Path) -> Path:
    """Write ``document`` to ``path`` as pretty JSON.

    Args:
        document: Scene to persist.
        path: Destination file.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        document.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return path
