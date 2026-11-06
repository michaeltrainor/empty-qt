"""Tests for marquee selection math, hit testing, and the drag state machine."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
gfx = pytest.importorskip("pygfx")
pytest.importorskip("rendercanvas")

from scene_viewport.box_select import (  # noqa: E402
    normalized_rect,
    rects_overlap,
    screen_bounds,
    selection_after_box,
)
from scene_viewport.viewport import ViewportController  # noqa: E402

_SIZE = (200.0, 100.0)


def test_normalized_rect_orders_corners() -> None:
    """Dragging up-left gives the same rect as dragging down-right."""
    assert normalized_rect((50.0, 40.0), (10.0, 5.0)) == (10.0, 5.0, 50.0, 40.0)


def test_screen_bounds_maps_ndc_to_pixels() -> None:
    """With an identity matrix, NDC corners land on the viewport corners."""
    points = np.array([[-1.0, 1.0, 0.0], [1.0, -1.0, 0.0]])
    assert screen_bounds(np.eye(4), points, _SIZE) == (0.0, 0.0, 200.0, 100.0)


def test_screen_bounds_drops_points_behind_camera() -> None:
    """Points with w <= 0 are ignored; all-behind returns None."""
    behind = np.eye(4)
    behind[3, 3] = -1.0
    assert screen_bounds(behind, np.array([[0.0, 0.0, 0.0]]), _SIZE) is None


def test_rects_overlap_includes_touching_edges() -> None:
    """Touching edges count, separate rects do not."""
    assert rects_overlap((0, 0, 10, 10), (10, 10, 20, 20))
    assert not rects_overlap((0, 0, 10, 10), (11, 0, 20, 10))


@pytest.mark.parametrize(
    ("shift", "ctrl", "expected"),
    [
        (False, False, ["b", "c"]),
        (True, False, ["a", "c"]),
        (False, True, ["a"]),
        (True, True, ["a", "b", "c"]),
    ],
)
def test_selection_after_box_maya_modifiers(
    shift: bool,
    ctrl: bool,
    expected: list[str],
) -> None:
    """Plain replaces, Shift toggles, Ctrl removes, Ctrl+Shift adds."""
    result = selection_after_box(["a", "b"], ["b", "c"], shift=shift, ctrl=ctrl)
    assert result == expected


def test_plain_box_over_nothing_clears() -> None:
    """A plain drag over empty space empties the selection."""
    assert selection_after_box(["a"], [], shift=False, ctrl=False) == []


def _cube(name: str, x: float) -> tuple[gfx.Group, gfx.Mesh]:
    group = gfx.Group()
    group.name = name
    group.local.position = (x, 0.0, 0.0)
    mesh = gfx.Mesh(gfx.box_geometry(1, 1, 1), gfx.MeshBasicMaterial())
    mesh.name = f"{name}Shape"
    group.add(mesh)
    return group, mesh


def _hit_tester(*cubes: tuple[gfx.Group, gfx.Mesh]) -> SimpleNamespace:
    """Stand-in controller with a camera at +Z looking at the origin."""
    camera = gfx.PerspectiveCamera(50, 2)  # pyright: ignore[reportArgumentType]
    camera.set_view_size(*_SIZE)
    camera.local.position = (0.0, 0.0, 10.0)
    camera.look_at((0.0, 0.0, 0.0))
    ns = SimpleNamespace(
        _groups={group.name: group for group, _mesh in cubes},
        _transform_to_meshes={group.name: [mesh.name] for group, mesh in cubes},
        _meshes={mesh.name: mesh for _group, mesh in cubes},
        renderer=SimpleNamespace(logical_size=_SIZE),
        camera=camera,
    )
    ns._shown = lambda name: ViewportController._shown(ns, name)  # type: ignore[arg-type]
    ns._shape_points = lambda name: ViewportController._shape_points(ns, name)  # type: ignore[arg-type]
    return ns


def test_box_hits_selects_only_touched_objects() -> None:
    """A box over the left half hits the left cube only."""
    ns = _hit_tester(_cube("left", -3.0), _cube("right", 3.0))
    hits = ViewportController.box_hits(ns, (0.0, 0.0, 90.0, 100.0))  # type: ignore[arg-type]
    assert hits == ["left"]
    everything = ViewportController.box_hits(ns, (0.0, 0.0, 200.0, 100.0))  # type: ignore[arg-type]
    assert everything == ["left", "right"]


def test_box_hits_skips_hidden_and_selects_child_not_parent() -> None:
    """Hidden transforms are skipped; a child hit does not pull in its parent."""
    parent, parent_mesh = _cube("parent", -3.0)
    child, child_mesh = _cube("child", 6.0)  # world x = +3
    parent.add(child)
    hidden, hidden_mesh = _cube("hidden", 3.0)
    hidden.visible = False
    ns = _hit_tester((parent, parent_mesh), (child, child_mesh), (hidden, hidden_mesh))
    hits = ViewportController.box_hits(ns, (110.0, 0.0, 200.0, 100.0))  # type: ignore[arg-type]
    assert hits == ["child"]


def _event(kind: str, x: float, y: float, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        type=kind,
        x=x,
        y=y,
        button=extra.get("button", 1),
        modifiers=extra.get("mods", ()),
    )


def _drag_tester(controls: dict[str, object] | None = None) -> SimpleNamespace:
    applied: list[list[str]] = []
    ns = SimpleNamespace(
        _suppress_click=False,
        _box_start=None,
        _box_end=(0.0, 0.0),
        _box_active=False,
        _selected_order=["old"],
        _camera_controller=SimpleNamespace(controls=controls or {}),
        _show_box=lambda _rect: None,
        _hide_box=lambda: None,
        box_hits=lambda _rect: ["hit"],
        _apply_picked_selection=applied.append,
        applied=applied,
    )
    ns._box_enabled = lambda: ViewportController._box_enabled(ns)  # type: ignore[arg-type]
    return ns


def _send(ns: SimpleNamespace, *events: SimpleNamespace) -> None:
    for event in events:
        ViewportController._on_box_pointer(ns, event)  # type: ignore[arg-type]


def test_drag_past_threshold_box_selects_and_eats_the_click() -> None:
    """A real drag selects hits, then swallows exactly one follow-up click."""
    ns = _drag_tester()
    _send(
        ns,
        _event("pointer_down", 10, 10),
        _event("pointer_move", 60, 40),
        _event("pointer_up", 60, 40, mods=("Shift",)),
    )
    assert ns.applied == [["old", "hit"]]
    assert ViewportController._consume_box_click(ns) is True  # type: ignore[arg-type]
    assert ViewportController._consume_box_click(ns) is False  # type: ignore[arg-type]


def test_short_drag_stays_a_click() -> None:
    """Moving less than the threshold leaves selection to the click handler."""
    ns = _drag_tester()
    _send(
        ns,
        _event("pointer_down", 10, 10),
        _event("pointer_move", 12, 11),
        _event("pointer_up", 12, 11),
    )
    assert ns.applied == []
    assert ns._suppress_click is False


def test_alt_right_and_orbit_presets_do_not_start_a_box() -> None:
    """Alt-drag, non-left buttons, and LMB-orbit presets never box-select."""
    for down in (
        _event("pointer_down", 10, 10, mods=("Alt",)),
        _event("pointer_down", 10, 10, button=2),
    ):
        ns = _drag_tester()
        _send(ns, down, _event("pointer_move", 80, 80), _event("pointer_up", 80, 80))
        assert ns.applied == []
    ns = _drag_tester({"mouse1": ("rotate", "drag", (0.005, 0.005))})
    _send(
        ns,
        _event("pointer_down", 10, 10),
        _event("pointer_move", 80, 80),
        _event("pointer_up", 80, 80),
    )
    assert ns.applied == []


def test_next_press_clears_a_stale_click_suppression() -> None:
    """A drag released off-canvas sends no click; the next press resets."""
    ns = _drag_tester()
    ns._suppress_click = True
    _send(ns, _event("pointer_down", 10, 10))
    assert ns._suppress_click is False
