"""Load and save scene viewport component graphs as JSON."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from scene_viewport.animation import AnimationClip
from scene_viewport.components import (
    CameraComponent,
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
        cameras: Camera shapes referencing transform names.
        animations: Keyframe clips whose channels reference transform names.
    """

    version: int = Field(default=1, ge=1)
    transforms: list[TransformComponent] = Field(default_factory=list)
    meshes: list[MeshComponent] = Field(default_factory=list)
    lights: list[LightComponent] = Field(default_factory=list)
    cameras: list[CameraComponent] = Field(default_factory=list)
    animations: list[AnimationClip] = Field(default_factory=list)


def load_scene(path: Path) -> SceneDocument:
    """Load a scene document from ``path``.

    Args:
        path: JSON file path.

    Relative mesh ``source`` paths are resolved against the scene file's
    folder, so the returned document holds absolute paths.

    Returns:
        Parsed :class:`SceneDocument`.

    Raises:
        OSError: When the file cannot be read.
        ValueError: When the JSON is invalid for this schema.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    document = SceneDocument.model_validate(payload)
    base = path.resolve().parent
    return _with_sources(document, lambda source: (base / source).resolve())


def save_scene(document: SceneDocument, path: Path) -> Path:
    """Write ``document`` to ``path`` as pretty JSON.

    Args:
        document: Scene to persist.
        path: Destination file.

    Mesh ``source`` paths are written relative to the scene file's folder
    when they share a drive with it, so a scene and its models can move
    together.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    base = path.resolve().parent
    document = _with_sources(document, lambda source: _relative_to(source, base))
    path.write_text(
        document.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _with_sources(
    document: SceneDocument,
    convert: Callable[[Path], Path],
) -> SceneDocument:
    """Return ``document`` with ``convert`` applied to each mesh source."""
    meshes = [
        mesh.model_copy(update={"source": convert(mesh.source)})
        if str(mesh.source).strip() and str(mesh.source) != "."
        else mesh
        for mesh in document.meshes
    ]
    return document.model_copy(update={"meshes": meshes})


def _relative_to(source: Path, base: Path) -> Path:
    """Express ``source`` relative to ``base``, or keep it if on another drive."""
    try:
        return source.resolve().relative_to(base, walk_up=True)
    except ValueError:
        return source
