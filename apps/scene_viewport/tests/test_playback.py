"""PlaybackController: poses go to the viewport, never to the store.

The GPU window is never opened. The viewport is a ``SimpleNamespace`` that
records the components it is asked to apply; the timeline bar is real.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("pygfx")

from mtqt import TimelineBar
from scene_viewport.animation import AnimationChannel, AnimationClip
from scene_viewport.components import TransformComponent
from scene_viewport.history import SceneObjects
from scene_viewport.playback import PlaybackController
from scene_viewport.viewport import ViewportController
from scene_viewport.window import SceneViewportWindow
from scene_viewport.world import SceneWorld


def _spin(target: str, name: str = "spin", seconds: float = 2.0) -> AnimationClip:
    """A clip turning ``target`` 180 degrees about Y over ``seconds``."""
    return AnimationClip(
        name=name,
        channels=[
            AnimationChannel(
                transform=target,
                path="rotation",
                times=[0.0, seconds],
                values=[[0.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0]],
            ),
            AnimationChannel(
                transform=target,
                path="translation",
                times=[0.0, seconds],
                values=[[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]],
            ),
        ],
    )


def _world() -> SceneWorld:
    return SceneWorld(
        transforms=[
            TransformComponent(name="hub", position=(1.0, 1.0, 1.0)),
            TransformComponent(name="spoke", parent="hub"),
            TransformComponent(name="still"),
        ],
        animations=[_spin("hub"), _spin("still", name="slide", seconds=1.0)],
    )


def _controller(qtbot, world: SceneWorld):
    applied: list[list[TransformComponent]] = []
    viewport = SimpleNamespace(
        apply_transforms_local=lambda components: applied.append(list(components))
    )
    bar = TimelineBar()
    qtbot.addWidget(bar)
    controller = PlaybackController(world, viewport, bar)
    return controller, bar, applied


def test_refresh_fills_the_bar_and_hides_it_without_clips(qtbot) -> None:
    world = _world()
    controller, bar, applied = _controller(qtbot, world)
    assert bar.clips == ["spin", "slide"]
    assert controller.clip == "spin"
    assert controller.duration == 2.0
    assert bar.duration == 2.0
    assert not controller.pose_displayed
    assert controller.status_text() is None
    assert applied == []
    assert controller.affects("hub")
    assert controller.affects("spoke")  # child of an animated transform
    assert not controller.affects("still")  # animated by the other clip only
    world.clear()
    controller.refresh()
    assert bar.clips == []
    assert controller.clip == ""
    assert controller.duration == 0.0
    controller.play()
    assert not controller.playing


def test_seek_poses_the_viewport_and_leaves_the_store(qtbot) -> None:
    world = _world()
    controller, bar, applied = _controller(qtbot, world)
    settled: list[int] = []
    controller.poseSettled.connect(lambda: settled.append(1))
    controller.seek(1.0)
    assert controller.time == 1.0
    assert bar.time == 1.0
    assert controller.pose_displayed
    (components,) = applied
    (posed,) = components
    assert posed.name == "hub"
    assert posed.position == pytest.approx((2.0, 0.0, 0.0))
    assert posed.rotation == pytest.approx((0.0, math.pi / 2, 0.0))
    assert world.transforms["hub"].position == (1.0, 1.0, 1.0)
    assert world.transforms["hub"].rotation == (0.0, 0.0, 0.0)
    assert "Paused spin at 1.00 s" in (controller.status_text() or "")
    assert settled == [1]
    # Stop restores the stored rest pose and rewinds.
    controller.stop()
    assert not controller.pose_displayed
    assert controller.time == 0.0
    assert bar.time == 0.0
    rest = applied[-1]
    assert [c.name for c in rest] == ["hub"]
    assert rest[0] == world.transforms["hub"]
    assert controller.status_text() is None
    # Seeking past the end clamps; the slider never emits back.
    controller.seek(9.0)
    assert controller.time == 2.0


def test_play_ticks_pause_holds_and_bar_drives_the_controller(qtbot) -> None:
    world = _world()
    controller, bar, applied = _controller(qtbot, world)
    bar._play.click()
    assert controller.playing
    assert bar.playing
    qtbot.waitUntil(lambda: controller.time > 0.05, timeout=2000)
    assert controller.pose_displayed
    assert "Playing spin" in (controller.status_text() or "")
    bar._play.click()
    assert not controller.playing
    held = controller.time
    qtbot.wait(60)
    assert controller.time == held
    # Scrubbing the slider while paused re-poses at that time.
    applied.clear()
    bar._slider.setValue(1500)
    assert controller.time == 1.5
    assert applied
    assert applied[-1][0].position == pytest.approx((3.0, 0.0, 0.0))
    bar._stop.click()
    assert controller.time == 0.0
    assert not controller.pose_displayed
    controller.set_speed(2.0)
    assert bar.speed == 2.0
    bar._loop.click()
    assert controller._state.loop is False


def test_loop_off_stops_at_the_end_and_play_restarts(qtbot) -> None:
    world = _world()
    controller, bar, _applied = _controller(qtbot, world)
    controller.set_loop(False)
    controller.set_speed(100.0)
    controller.play()
    qtbot.waitUntil(lambda: not controller.playing, timeout=3000)
    assert controller.time == 2.0
    assert not bar.playing
    assert controller.pose_displayed
    controller.play()
    assert controller.playing
    assert controller.time < 2.0


def test_switching_clips_rewinds_and_keeps_the_play_state(qtbot) -> None:
    world = _world()
    controller, bar, applied = _controller(qtbot, world)
    controller.seek(1.0)
    bar._clips.setCurrentIndex(1)
    assert controller.clip == "slide"
    assert controller.time == 0.0
    assert controller.duration == 1.0
    assert bar.duration == 1.0
    # Switching restored hub's rest pose and did not pose the new clip.
    assert applied[-1][0] == world.transforms["hub"]
    assert not controller.pose_displayed
    controller.play()
    controller.set_clip("spin")
    assert controller.playing
    assert controller.clip == "spin"
    controller.set_clip("missing")
    assert controller.clip == "spin"


def test_refresh_keeps_a_paused_pose_through_rename_and_delete(qtbot) -> None:
    world = _world()
    controller, bar, applied = _controller(qtbot, world)
    controller.seek(1.0)
    world.rename_transform("hub", "wheel")
    applied.clear()
    controller.refresh()
    assert controller.clip == "spin"
    assert controller.time == 1.0
    assert applied[-1][0].name == "wheel"
    assert controller.affects("wheel")
    assert not controller.affects("hub")
    # Deleting the only target drops the clip; the pose can no longer show.
    world.remove_transform("wheel")
    controller.refresh()
    assert bar.clips == ["slide"]
    assert controller.clip == "slide"
    assert controller.time == 0.0
    assert not controller.pose_displayed


def test_stop_for_edit_only_when_a_pose_touches_the_edit(qtbot) -> None:
    world = _world()
    controller, _bar, _applied = _controller(qtbot, world)
    assert not controller.stop_for_edit(["hub"])  # nothing displayed yet
    controller.seek(0.5)
    assert not controller.stop_for_edit(["still"])  # not moved by this clip
    assert controller.pose_displayed
    assert controller.stop_for_edit(["spoke"])  # child of the posed hub
    assert not controller.pose_displayed
    assert controller.time == 0.0


def test_window_restore_objects_puts_clips_back(qtbot) -> None:
    """Undo of a delete restores the clip snapshot, pruned to what exists."""
    world = SceneWorld(transforms=[TransformComponent(name="a")])
    registered: list[str] = []
    ns = SimpleNamespace(
        _world=world,
        _register_transform=lambda t: (
            world.add_transform(t),
            registered.append(t.name),
        ),
        _register_mesh=lambda m: None,
        _register_light=lambda light: None,
        _register_camera=lambda c: None,
        _apply_stored_transform=lambda t: None,
        _viewport=SimpleNamespace(),
    )
    clip = _spin("a")
    world.add_animation(clip)
    world.remove_transform("a")
    assert world.animations == {}
    SceneViewportWindow._restore_objects(
        ns,
        SceneObjects(
            transforms=[TransformComponent(name="a")],
            animations=[clip, _spin("ghost", name="orphan")],
        ),
    )
    assert registered == ["a"]
    assert list(world.animations) == ["spin"]
    # Restoring over an existing clip replaces it.
    world.set_animation(_spin("a").model_copy(update={"channels": clip.channels[:1]}))
    SceneViewportWindow._restore_objects(ns, SceneObjects(animations=[clip]))
    assert len(world.animations["spin"].channels) == 2


def test_rebase_drag_reanchors_on_the_restored_rest_pose() -> None:
    """After the rest pose is restored mid-drag, the delta applies to it."""
    gfx = pytest.importorskip("pygfx")
    group = gfx.Group()
    group.world.position = (5.0, 0.0, 0.0)  # rest pose, just restored
    proxy = gfx.Group()
    proxy.world.position = (1.0, 0.0, 0.0)  # gizmo moved one unit so far
    drawn: list[bool] = []
    ns = SimpleNamespace(
        _drag_active=True,
        _drag_rels={"a": np.eye(4)},  # stale: measured from the animated pose
        _selected_order=["a"],
        _groups={"a": group},
        _manip_proxy=proxy,
        _proxy_rest=np.eye(4),
        _canvas=SimpleNamespace(request_draw=lambda: drawn.append(True)),
    )
    ns._ensure_drag_started = lambda: ViewportController._ensure_drag_started(ns)
    ViewportController.rebase_drag(ns)
    assert ns._drag_active
    assert np.asarray(group.world.position) == pytest.approx((6.0, 0.0, 0.0))
    assert drawn
    # Without an active drag nothing happens.
    ns._drag_active = False
    group.world.position = (0.0, 0.0, 0.0)
    ViewportController.rebase_drag(ns)
    assert np.asarray(group.world.position) == pytest.approx((0.0, 0.0, 0.0))
