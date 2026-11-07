"""Tests for camera presets, snapping math, and Maya click selection."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
gfx = pytest.importorskip("pygfx")

from scene_viewport.navigation import (  # noqa: E402
    NAVIGATION_CONTROLS,
    NAVIGATION_HINTS,
    navigation_controls,
)
from scene_viewport.settings import AppSettings  # noqa: E402
from scene_viewport.xform_math import (  # noqa: E402
    mat_from_trs,
    snap_rotation,
    snap_translation,
    trs_from_mat,
)


@pytest.mark.parametrize("style", list(NAVIGATION_CONTROLS))
def test_navigation_presets_are_valid_pygfx_controls(style: str) -> None:
    """Every preset is accepted by the orbit controller and has a hint."""
    controller = gfx.OrbitController(gfx.PerspectiveCamera(50, 1))
    controller.controls = navigation_controls(style)  # type: ignore[arg-type]
    assert dict(controller.controls) == NAVIGATION_CONTROLS[style]  # type: ignore[index]
    assert NAVIGATION_HINTS[style]  # type: ignore[index]


def test_maya_preset_leaves_plain_left_click_free() -> None:
    """Maya navigation needs Alt, so LMB stays for picking and the gizmo."""
    assert "mouse1" not in navigation_controls("maya")
    assert "alt+mouse1" in navigation_controls("maya")


def test_unknown_navigation_style_raises() -> None:
    """Unknown presets fail loudly."""
    with pytest.raises(ValueError, match="navigation style"):
        navigation_controls("houdini")  # type: ignore[arg-type]


def test_settings_default_to_maya_navigation() -> None:
    """The app defaults to Maya bindings and sensible snap steps."""
    settings = AppSettings()
    assert settings.navigation_style == "maya"
    assert settings.snap_translate == 0.5
    assert settings.snap_rotate_degrees == 15.0


def test_snap_translation_world_rounds_delta() -> None:
    """World snapping rounds the drag delta, not the absolute position."""
    start = mat_from_trs((0.2, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    current = mat_from_trs((0.9, 0.1, -0.4), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    snapped = snap_translation(start, current, 0.5)
    assert np.allclose(snapped[:3, 3], (0.7, 0.0, -0.5))


def test_snap_translation_local_stays_on_axis() -> None:
    """Local snapping quantizes along the rotated axis."""
    rotation = (0.0, math.pi / 4, 0.0)
    start = mat_from_trs((0.0, 0.0, 0.0), rotation, (2.0, 2.0, 2.0))
    axis = start[:3, 0] / np.linalg.norm(start[:3, 0])
    current = start.copy()
    current[:3, 3] = axis * 1.3
    snapped = snap_translation(start, current, 0.5, local_axes=True)
    assert np.allclose(snapped[:3, 3], axis * 1.5)


def test_snap_translation_local_sheared_frame_stays_on_axis() -> None:
    """Under shear, a drag along one local axis snaps along that axis only."""
    # A rotated child under a non-uniformly scaled parent: columns not orthogonal.
    parent = mat_from_trs((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (3.0, 1.0, 1.0))
    child = mat_from_trs((0.0, 0.0, 0.0), (0.0, 0.0, math.pi / 6), (1.0, 1.0, 1.0))
    start = parent @ child
    axes = start[:3, :3] / np.linalg.norm(start[:3, :3], axis=0)
    assert abs(axes[:, 0] @ axes[:, 1]) > 0.1
    current = start.copy()
    current[:3, 3] = axes[:, 1] * 1.3
    snapped = snap_translation(start, current, 0.5, local_axes=True)
    assert np.allclose(snapped[:3, 3], axes[:, 1] * 1.5)


def test_snap_translation_local_zero_scale_stays_finite() -> None:
    """A zero-scale axis used to divide by zero and return NaN."""
    rotation = (0.0, math.pi / 4, 0.0)
    start = mat_from_trs((0.0, 0.0, 0.0), rotation, (1.0, 0.0, 1.0))
    current = start.copy()
    current[:3, 3] = (0.7, 0.2, -0.6)
    snapped = snap_translation(start, current, 0.5, local_axes=True)
    assert np.all(np.isfinite(snapped))


def test_snap_translation_non_positive_step_is_noop() -> None:
    """A zero step returns the current matrix unchanged."""
    start = mat_from_trs((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    current = mat_from_trs((0.3, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert np.allclose(snap_translation(start, current, 0.0), current)


def test_snap_rotation_rounds_angle() -> None:
    """A 20 degree turn snaps to 15 degrees and keeps position and scale."""
    start = mat_from_trs((1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
    current = mat_from_trs((1.0, 2.0, 3.0), (0.0, math.radians(20), 0.0), (2, 2, 2))
    snapped = snap_rotation(start, current, math.radians(15))
    position, rotation, scale = trs_from_mat(snapped)
    assert np.allclose(position, (1.0, 2.0, 3.0))
    assert np.allclose(scale, (2.0, 2.0, 2.0))
    assert np.allclose(rotation, (0.0, math.radians(15), 0.0), atol=1e-6)


def test_snap_rotation_small_turn_returns_start() -> None:
    """Turns under half a step snap back to the start orientation."""
    start = mat_from_trs((0.0, 0.0, 0.0), (0.0, 0.3, 0.0), (1.0, 1.0, 1.0))
    current = mat_from_trs((0.0, 0.0, 0.0), (0.0, 0.35, 0.0), (1.0, 1.0, 1.0))
    snapped = snap_rotation(start, current, math.radians(15))
    assert np.allclose(snapped, start, atol=1e-6)


@pytest.mark.parametrize(
    ("shift", "ctrl", "name", "expected"),
    [
        (False, False, "b", ["b"]),
        (True, False, "c", ["a", "b", "c"]),
        (True, False, "a", ["b"]),
        (False, True, "a", ["b"]),
        (False, True, "c", ["a", "b"]),
        (True, True, "c", ["a", "b", "c"]),
        (True, True, "a", ["a", "b"]),
    ],
)
def test_selection_after_click_maya_modifiers(
    shift: bool,
    ctrl: bool,
    name: str,
    expected: list[str],
) -> None:
    """Shift toggles, Ctrl removes, Ctrl+Shift adds, plain replaces."""
    pytest.importorskip("rendercanvas")
    from scene_viewport.viewport import selection_after_click

    result = selection_after_click(["a", "b"], name, shift=shift, ctrl=ctrl)
    assert result == expected


@pytest.mark.parametrize("fov", [0.0, 50.0])
def test_framing_keeps_the_target_between_the_clip_planes(fov: float) -> None:
    """Orthographic framing used to clip a flat object at the camera plane."""
    from types import SimpleNamespace

    from scene_viewport.viewport import ViewportController

    camera = gfx.PerspectiveCamera(fov, 1.0)
    host = SimpleNamespace(
        camera=camera, _canvas=SimpleNamespace(request_draw=lambda: None)
    )
    center = np.array([1.25, 0.0, 0.0])
    ViewportController._show_sphere(host, (*center, 1.5), (0.0, 0.0, -1.0))  # type: ignore[arg-type]
    forward = np.asarray(camera.world.forward)
    depth = float((center - np.asarray(camera.world.position)) @ forward)
    near, far = camera.depth_range
    # The whole sphere lies inside the depth range.
    assert near < depth - 1.5
    assert depth + 1.5 < far
