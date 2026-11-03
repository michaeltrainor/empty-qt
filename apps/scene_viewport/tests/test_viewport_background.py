"""Tests for the viewport background, grid, and gamma settings."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pygfx")

import pygfx as gfx

from scene_viewport.settings import (
    MAYA_BACKGROUND,
    AppSettings,
    load_settings,
    renderer_gamma,
)
from scene_viewport.viewport import ViewportController


def _owner(settings: AppSettings) -> SimpleNamespace:
    return SimpleNamespace(
        _settings=settings,
        _background=None,
        _env_map=None,
        _grid=None,
        scene=gfx.Scene(),
        _tint_grid_axes=ViewportController._tint_grid_axes,
    )


def test_default_background_is_maya_solid_gray() -> None:
    """New settings open on Maya's flat 0.36 gray."""
    settings = AppSettings()
    assert settings.background_mode == "solid"
    assert settings.background_color == MAYA_BACKGROUND


def test_solid_background_ignores_environment_colors() -> None:
    """Solid mode draws one unlit color, not the IBL sky or ground."""
    owner = _owner(AppSettings(background_top="#ff0000", background_bottom="#00ff00"))
    ViewportController._rebuild_environment(owner)

    material = owner._background.material
    assert type(material) is gfx.BackgroundMaterial
    expected = gfx.Color(MAYA_BACKGROUND)
    corners = (
        material.color_bottom_left,
        material.color_bottom_right,
        material.color_top_left,
        material.color_top_right,
    )
    assert all(gfx.Color(color) == expected for color in corners)


def test_grid_is_unlit_and_shadow_free() -> None:
    """The grid uses an unlit line material and never casts or receives."""
    owner = _owner(AppSettings())
    ViewportController._rebuild_viewport_grid(owner)

    grid = owner._grid
    assert isinstance(grid.material, gfx.LineSegmentMaterial)
    assert grid.cast_shadow is False
    assert grid.receive_shadow is False


@pytest.mark.parametrize(("gamma", "expected"), [(2.2, 1.0), (1.1, 0.5)])
def test_renderer_gamma_treats_srgb_as_no_correction(
    gamma: float, expected: float
) -> None:
    """A display gamma of 2.2 leaves pygfx's sRGB output untouched."""
    assert renderer_gamma(gamma) == pytest.approx(expected)


def test_old_settings_file_moves_to_solid_background(tmp_path: Path) -> None:
    """Files saved before solid mode existed open on the Maya gray."""
    path = tmp_path / "scene_viewport.json"
    path.write_text(json.dumps({"background_mode": "skybox"}), encoding="utf-8")

    settings = load_settings(path)

    assert settings.background_mode == "solid"
    assert json.loads(path.read_text(encoding="utf-8"))["background_mode"] == "solid"


def test_new_settings_file_keeps_chosen_background(tmp_path: Path) -> None:
    """Once background_color is saved, the chosen mode is respected."""
    path = tmp_path / "scene_viewport.json"
    payload = {"background_mode": "skybox", "background_color": "#333333"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    settings = load_settings(path)

    assert settings.background_mode == "skybox"
    assert settings.background_color == "#333333"
