"""Persistent application settings for the scene viewport example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Space = Literal["local", "world"]

_SETTINGS_DIR = Path.home() / ".config" / "mtqt"
SETTINGS_PATH = _SETTINGS_DIR / "scene_viewport.json"


class AppSettings(BaseModel):
    """User preferences for the scene viewport example.

    These are application settings (not scene data). Edited from
    Settings → Preferences and saved to ``SETTINGS_PATH``.
    """

    default_edit_space: Space = Field(
        default="world",
        title="Default Edit Space",
        description="World or local for new objects and the toolbar.",
    )
    show_grid: bool = Field(default=True, title="Show Grid")
    show_axes: bool = Field(default=True, title="Show Axes")
    grid_size: float = Field(
        default=40.0,
        ge=2.0,
        le=200.0,
        title="Grid Size",
        description="XZ grid extent in world units.",
    )
    grid_divisions: int = Field(
        default=40,
        ge=2,
        le=200,
        title="Grid Divisions",
        description="Cells along each axis (size/divisions = cell size).",
    )
    axes_size: float = Field(
        default=2.0,
        ge=0.1,
        le=50.0,
        title="Axes Size",
    )
    gizmo_size: float = Field(
        default=100.0,
        ge=40.0,
        le=300.0,
        title="Gizmo Size",
        description="On-screen manipulator size in pixels.",
    )
    ambient_intensity: float = Field(
        default=0.35,
        ge=0.0,
        le=5.0,
        title="Ambient Intensity",
    )
    background_top: str = Field(
        default="#2a3038",
        title="Background Top",
        json_schema_extra={"mtqt_editor": "color"},
    )
    background_bottom: str = Field(
        default="#1e2228",
        title="Background Bottom",
        json_schema_extra={"mtqt_editor": "color"},
    )
    shadow_bias: float = Field(
        default=0.002,
        ge=0.0,
        le=0.05,
        title="Shadow Bias",
    )
    shadow_extent: float = Field(
        default=24.0,
        ge=4.0,
        le=200.0,
        title="Directional Shadow Extent",
        description="Ortho half-size of the key light shadow map.",
    )


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings from disk, or return defaults when missing/invalid.

    Args:
        path: JSON file path. Defaults to :data:`SETTINGS_PATH`.
    """
    target = path or SETTINGS_PATH
    if not target.is_file():
        return AppSettings()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return AppSettings.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    """Write settings to disk as JSON.

    Args:
        settings: Settings instance to persist.
        path: JSON file path. Defaults to :data:`SETTINGS_PATH`.

    Returns:
        The path written.
    """
    target = path or SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        settings.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return target
