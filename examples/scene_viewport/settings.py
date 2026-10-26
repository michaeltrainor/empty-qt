"""Persistent application settings for the scene viewport example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Space = Literal["local", "world"]
BackgroundMode = Literal["gradient", "skybox"]
ShadowCull = Literal["front", "back", "none"]

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
        default=0.22,
        ge=0.0,
        le=5.0,
        title="Ambient Intensity",
    )
    exposure: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        title="Exposure",
        description="Scales ambient fill and environment intensity.",
    )
    gamma: float = Field(
        default=2.2,
        ge=1.0,
        le=2.4,
        title="Gamma",
        description="Display gamma applied by the renderer.",
    )
    background_mode: BackgroundMode = Field(
        default="skybox",
        title="Background Mode",
    )
    background_top: str = Field(
        default="#6b7c95",
        title="Sky / Top",
        json_schema_extra={"mtqt_editor": "color"},
    )
    background_bottom: str = Field(
        default="#2a2e36",
        title="Ground / Bottom",
        json_schema_extra={"mtqt_editor": "color"},
    )
    env_map_intensity: float = Field(
        default=0.55,
        ge=0.0,
        le=3.0,
        title="Environment Intensity",
        description="IBL strength on Standard materials.",
    )
    shadow_bias: float = Field(
        default=0.0002,
        ge=0.0,
        le=0.05,
        title="Shadow Bias",
        description="Keep tiny. Large values detach shadows from contact.",
    )
    shadow_extent: float = Field(
        default=16.0,
        ge=4.0,
        le=200.0,
        title="Directional Shadow Extent",
        description="Ortho size of the key light shadow map (smaller = sharper).",
    )
    shadow_cull: ShadowCull = Field(
        default="front",
        title="Shadow Cull",
        description="Front cull reduces acne; none helps open meshes.",
    )
    outline_thickness: float = Field(
        default=1.75,
        ge=0.5,
        le=4.0,
        title="Selection Outline",
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
        settings = AppSettings.model_validate(payload)
        migrated = _migrate_shadow_defaults(settings, payload)
        if migrated != settings:
            # Persist the rewrite so Preferences shows the fixed defaults.
            save_settings(migrated, target)
        return migrated
    except (OSError, json.JSONDecodeError, ValueError):
        return AppSettings()


def _migrate_shadow_defaults(
    settings: AppSettings,
    payload: dict[str, object],
) -> AppSettings:
    """Rewrite known-bad pre-fix shadow defaults that cause peter-panning."""
    updates: dict[str, object] = {}
    # Prior defaults (0.002 / 0.0015) detach contact shadows on this set scale.
    if "shadow_bias" in payload and float(settings.shadow_bias) >= 0.001:
        updates["shadow_bias"] = 0.0002
    if payload.get("shadow_extent") == 24.0:
        updates["shadow_extent"] = 16.0
    if not updates:
        return settings
    return settings.model_copy(update=updates)


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
