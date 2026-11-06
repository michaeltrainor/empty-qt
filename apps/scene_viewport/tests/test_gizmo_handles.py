"""Tests for gizmo axis grab strips and the drift-free axis/plane math."""

from __future__ import annotations

from types import SimpleNamespace

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


def _pointer(kind: str, target: object = None, **fields: object) -> SimpleNamespace:
    """A stand-in for a pygfx pointer event with a no-op ``stop_propagation``."""
    stopped: list[bool] = []
    defaults: dict[str, object] = {
        "button": 1,
        "modifiers": (),
        "x": 0.0,
        "y": 0.0,
        "pointer_id": 1,
        "root": None,
    }
    return SimpleNamespace(
        type=kind,
        target=target,
        stopped=stopped,
        stop_propagation=lambda: stopped.append(True),
        **(defaults | fields),
    )


def _grabbing_gizmo(monkeypatch: pytest.MonkeyPatch) -> tuple[object, list[str]]:
    """A gizmo on an object, with drag start and pointer capture recorded."""
    gizmo = MayaTransformGizmo(gfx.WorldObject())
    started: list[str] = []
    monkeypatch.setattr(
        gizmo, "_handle_start", lambda kind, _event, _ob: started.append(kind)
    )
    monkeypatch.setattr(gizmo, "set_pointer_capture", lambda *_args: None)
    return gizmo, started


@pytest.mark.parametrize("modifiers", [(), ("Shift",), ("Control",), ("Meta",)])
def test_shift_or_ctrl_press_still_grabs_a_handle(
    monkeypatch: pytest.MonkeyPatch, modifiers: tuple[str, ...]
) -> None:
    """Modified presses grab instead of falling through to a box select."""
    gizmo, started = _grabbing_gizmo(monkeypatch)
    event = _pointer("pointer_down", gizmo._translate_children[0], modifiers=modifiers)
    gizmo.process_event(event)
    assert started == ["translate"]
    assert event.stopped


def test_alt_press_leaves_the_handle_to_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alt+left orbits in the Maya preset, so the gizmo must not take it."""
    gizmo, started = _grabbing_gizmo(monkeypatch)
    event = _pointer("pointer_down", gizmo._translate_children[0], modifiers=("Alt",))
    gizmo.process_event(event)
    assert started == []
    assert not event.stopped


def test_release_keeps_the_last_move_modifiers() -> None:
    """Letting go of Ctrl just before the button must not change the snap."""
    finals: list[bool] = []
    gizmo = MayaTransformGizmo(gfx.WorldObject(), on_changed=finals.append)
    gizmo._ref = {"event_pos": (0.0, 0.0), "maxdist": 0.0, "kind": "translate"}
    gizmo.process_event(_pointer("pointer_move", modifiers=("Control",)))
    gizmo.process_event(_pointer("pointer_up"))
    assert gizmo.drag_modifiers == ("Control",)
    assert finals == [True]
