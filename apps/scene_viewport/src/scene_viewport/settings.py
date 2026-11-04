"""Persistent application settings for the scene viewport example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Space = Literal["local", "world"]
BackgroundMode = Literal["solid", "gradient", "skybox"]
ShadowCull = Literal["front", "back", "none"]

_SETTINGS_DIR = Path.home() / ".config" / "mtqt"
SETTINGS_PATH = _SETTINGS_DIR / "scene_viewport.json"

#: Maya's default viewport background (0.36 gray, gradient off).
MAYA_BACKGROUND = "#5c5c5c"
#: pygfx already writes sRGB, so this display gamma means "no extra correction".
SRGB_GAMMA = 2.2


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
    show_axes: bool = Field(
        default=True,
        title="Show Axes",
        description="Tint the grid's center X (red) and Z (blue) lines.",
    )
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
        description="Display gamma. 2.2 is standard sRGB; lower brightens.",
    )
    background_mode: BackgroundMode = Field(
        default="solid",
        title="Background Mode",
        description=(
            "Solid uses Background Color. Gradient and skybox use the "
            "environment sky and ground colors."
        ),
    )
    background_color: str = Field(
        default=MAYA_BACKGROUND,
        title="Background Color",
        description="Flat viewport color. Not lit and not used for IBL.",
        json_schema_extra={"mtqt_editor": "color"},
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
        migrated = _migrate_background_defaults(
            _migrate_shadow_defaults(settings, payload),
            payload,
        )
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


def _migrate_background_defaults(
    settings: AppSettings,
    payload: dict[str, object],
) -> AppSettings:
    """Move files saved before the solid background existed onto it.

    Older files always stored ``background_mode`` as gradient or skybox,
    because solid wasn't an option, so they open on the Maya gray once.
    """
    if "background_color" in payload:
        return settings
    return settings.model_copy(update={"background_mode": "solid"})


def renderer_gamma(gamma: float) -> float:
    """Convert a display gamma to pygfx's ``gamma_correction`` exponent.

    pygfx renders to an sRGB target and then raises each output channel
    to ``gamma_correction``, so ``1.0`` is already correct sRGB. Passing a
    display gamma of 2.2 straight through squares the image darker.

    Args:
        gamma: Display gamma from settings (2.2 is standard sRGB).

    Returns:
        The exponent for ``WgpuRenderer.gamma_correction``.
    """
    return gamma / SRGB_GAMMA


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
