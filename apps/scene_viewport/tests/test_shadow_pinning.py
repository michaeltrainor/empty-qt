"""Tests for perspective shadow depth pinning and material lookups."""

from __future__ import annotations

import weakref
from types import SimpleNamespace

import pytest

pytest.importorskip("pygfx")

import pygfx as gfx

from scene_viewport.viewport import (
    ViewportController,
    _light_shadow,
    _material_of,
)


def _pinner() -> SimpleNamespace:
    return SimpleNamespace(_shadow_depth=weakref.WeakKeyDictionary())


@pytest.mark.parametrize("light_type", [gfx.SpotLight, gfx.PointLight])
def test_pinned_depth_survives_matrix_update(light_type) -> None:
    owner = _pinner()
    light = light_type(cast_shadow=True)
    light.local.position = (0.0, 5.0, 2.0)

    ViewportController._pin_perspective_shadow_depth(owner, light, near=0.25, far=12.0)
    light.shadow._update_matrix(light)
    assert tuple(light.shadow.camera.depth_range) == (0.25, 12.0)

    # A second pin updates the values without wrapping _update_matrix again.
    ViewportController._pin_perspective_shadow_depth(owner, light, near=0.5, far=20.0)
    light.shadow._update_matrix(light)
    assert tuple(light.shadow.camera.depth_range) == (0.5, 20.0)
    assert len(owner._shadow_depth) == 1


def test_pinned_far_stays_past_near() -> None:
    owner = _pinner()
    light = gfx.SpotLight(cast_shadow=True)
    light.local.position = (0.0, 5.0, 2.0)
    ViewportController._pin_perspective_shadow_depth(owner, light, near=3.0, far=1.0)
    light.shadow._update_matrix(light)
    assert tuple(light.shadow.camera.depth_range) == (3.0, 4.0)


def test_light_shadow_rejects_light_without_shadow() -> None:
    with pytest.raises(TypeError, match="no shadow"):
        _light_shadow(gfx.AmbientLight())


def test_material_of_checks_type() -> None:
    mesh = gfx.Mesh(gfx.box_geometry(), gfx.MeshBasicMaterial())
    assert isinstance(_material_of(mesh, gfx.MeshBasicMaterial), gfx.MeshBasicMaterial)
    with pytest.raises(TypeError, match="expected LineMaterial"):
        _material_of(mesh, gfx.LineMaterial)
