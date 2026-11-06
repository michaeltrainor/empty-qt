"""Tests for gizmo axis grab strips and the drift-free axis/plane math."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
gfx = pytest.importorskip("pygfx")

from scene_viewport.gizmo import MayaTransformGizmo  # noqa: E402
from scene_viewport.xform_math import (  # noqa: E402
    closest_axis_offset,
    ray_plane_offset,
)


def test_closest_axis_offset_stays_on_axis() -> None:
    """A ray passing off to the side still yields a point on the axis."""
    offset = closest_axis_offset(
        np.zeros(3),
        np.array([2.0, 0.0, 0.0]),
        np.array([3.0, 5.0, 1.0]),
        np.array([0.0, -1.0, 0.0]),
    )
    assert offset is not None
    assert np.allclose(offset, (3.0, 0.0, 0.0))


def test_closest_axis_offset_parallel_ray_is_none() -> None:
    """A ray along the axis has no single nearest point."""
    offset = closest_axis_offset(
        np.zeros(3),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
    )
    assert offset is None


def test_ray_plane_offset_lands_in_plane() -> None:
    """The hit point lies in the plane, so the normal component is zero."""
    origin = np.array([1.0, 2.0, 3.0])
    offset = ray_plane_offset(
        origin,
        np.array([0.0, 0.0, 1.0]),
        np.array([4.0, 6.0, 10.0]),
        np.array([0.0, 0.0, -2.0]),
    )
    assert offset is not None
    assert np.allclose(offset, (3.0, 4.0, 0.0))


def test_ray_plane_offset_parallel_ray_is_none() -> None:
    """A ray parallel to the plane never meets it."""
    offset = ray_plane_offset(
        np.zeros(3),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
    )
    assert offset is None


def test_each_axis_line_has_a_pickable_grab_strip() -> None:
    """Strips follow their line, take picks, and draw under the handles."""
    gizmo = MayaTransformGizmo()
    lines = gizmo._line_children
    strips = gizmo._axis_hit_lines
    assert len(strips) == len(lines) == 3
    for line, strip in zip(lines, strips, strict=True):
        # A child of its line, so it shares the line's rotation and hides
        # with it (all three lines share one +X geometry).
        assert strip.parent is line
        assert strip.geometry is line.geometry
        material = strip.material
        assert isinstance(material, gfx.LineMaterial)
        assert material.pick_write
        assert material.thickness > line.material.thickness
        assert not material.depth_test
        assert strip.render_order < line.render_order
        assert gizmo._axis_line_for(strip) is line
    # Other handles map to themselves.
    tip = gizmo._translate_children[0]
    assert gizmo._axis_line_for(tip) is tip


def test_rotate_tool_hides_axis_lines_and_their_strips() -> None:
    """In rotate mode the lines (and so their strips) are hidden."""
    gizmo = MayaTransformGizmo()
    gizmo.set_tool("rotate")
    assert not any(line.visible for line in gizmo._line_children)
    gizmo.set_tool("translate")
    assert all(line.visible for line in gizmo._line_children)
