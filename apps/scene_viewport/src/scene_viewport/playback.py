"""Play animation clips into the viewport as view state over the document.

The controller owns the playback timer and the :class:`TimelineBar`
wiring. Each tick samples the current clip and writes posed copies of the
**stored** transforms to the viewport. It never changes
``SceneWorld.transforms``, pushes history, or dirties the document: like
look-through, playback is something the viewport shows, not an edit.
Stop restores the rest pose from the store.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from mtqt import TimelineBar
from scene_viewport.animation_player import ClipSampler, PlaybackState, posed_transform
from scene_viewport.components import TransformComponent
from scene_viewport.world import SceneWorld

#: Timer period in milliseconds; the playhead advances by wall time, not by
#: this, so a slow frame does not slow the clip.
_TICK_MS = 16


class PoseSink(Protocol):
    """What the controller needs from the viewport."""

    def apply_transforms_local(self, components: Iterable[TransformComponent]) -> None:
        """Push local TRS, pivot, and visibility for several transforms."""
        ...


class PlaybackController(QObject):
    """Drives one clip from the world into the viewport.

    Signals:
        stateChanged(): Anything the status bar shows changed: play, pause,
            stop, seek, clip, speed, loop, and every tick.
        poseSettled(): A displayed pose stopped moving (pause, stop, seek
            while paused, clip change). Hosts refresh editors that read the
            viewport.

    Args:
        world: Scene store; clips and rest poses are read from it.
        viewport: Where poses are written.
        bar: Transport controls to drive and reflect.
        parent: Qt parent, usually the window.
    """

    stateChanged = Signal()
    poseSettled = Signal()

    def __init__(
        self,
        world: SceneWorld,
        viewport: PoseSink,
        bar: TimelineBar,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._world = world
        self._viewport = viewport
        self._bar = bar
        self._state = PlaybackState()
        self._sampler: ClipSampler | None = None
        # Transforms whose viewport pose differs from the store right now.
        self._posed: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._last = 0.0
        bar.playToggled.connect(self._on_play_toggled)
        bar.stopRequested.connect(self.stop)
        bar.loopToggled.connect(self.set_loop)
        bar.speedChanged.connect(self.set_speed)
        bar.clipChanged.connect(self.set_clip)
        bar.timeScrubbed.connect(self.seek)
        bar.set_loop(self._state.loop)
        bar.set_speed(self._state.speed)
        self.refresh()

    # -- state -----------------------------------------------------------

    @property
    def clip(self) -> str:
        """Name of the current clip, or ``""`` when the scene has none."""
        return self._state.clip

    @property
    def time(self) -> float:
        """Playhead in seconds."""
        return self._state.time

    @property
    def playing(self) -> bool:
        """Whether the timer is advancing the playhead."""
        return self._state.playing

    @property
    def pose_displayed(self) -> bool:
        """Whether the viewport shows an animated pose instead of the store."""
        return bool(self._posed)

    @property
    def duration(self) -> float:
        """Length of the current clip in seconds."""
        return self._sampler.duration if self._sampler is not None else 0.0

    def affects(self, name: str) -> bool:
        """Whether the current clip moves ``name`` or one of its ancestors.

        Args:
            name: Transform name.
        """
        if self._sampler is None:
            return False
        targets = self._sampler.targets
        seen: set[str] = set()
        while name and name not in seen:
            if name in targets:
                return True
            seen.add(name)
            stored = self._world.transforms.get(name)
            name = stored.parent if stored is not None else ""
        return False

    def status_text(self) -> str | None:
        """Return the status-bar text, or ``None`` when nothing is shown."""
        if self._sampler is None or not self._state.clip:
            return None
        if self._state.playing:
            return (
                f"Playing {self._state.clip} "
                f"{self._state.time:.2f} / {self.duration:.2f} s"
            )
        if self._posed:
            return (
                f"Paused {self._state.clip} at {self._state.time:.2f} s "
                "(Stop to edit animated transforms)"
            )
        return None

    # -- scene changes ---------------------------------------------------

    def refresh(self) -> None:
        """Re-read the world's clips after the scene changed.

        Keeps the current clip, time, and play state when the clip still
        exists; otherwise rewinds and pauses on the first clip, or hides
        the bar when the scene has none. A paused pose is applied again
        so renames and deletes show.
        """
        clips = list(self._world.animations)
        self._bar.setVisible(bool(clips))
        current = self._state.clip if self._state.clip in clips else ""
        if not current and clips:
            current = clips[0]
        self._bar.set_clips(clips, current)
        if current != self._state.clip:
            self._restore_rest()
            self._timer.stop()
            self._state = PlaybackState(
                clip=current, loop=self._state.loop, speed=self._state.speed
            )
        self._sampler = self._build_sampler(current)
        if self._sampler is None:
            self._timer.stop()
            self._state.playing = False
        else:
            self._state.seek(self._state.time, self._sampler.duration)
            if self._posed and not self._state.playing:
                self._apply_pose()
        self._bar.set_duration(self.duration)
        self._bar.set_time(self._state.time)
        self._bar.set_playing(self._state.playing)
        self.stateChanged.emit()

    def _build_sampler(self, name: str) -> ClipSampler | None:
        clip = self._world.get_animation(name) if name else None
        return ClipSampler(clip) if clip is not None else None

    # -- transport -------------------------------------------------------

    def play(self) -> None:
        """Start advancing the playhead; a finished clip starts over."""
        if self._sampler is None or self._state.playing:
            return
        if self._state.time >= self.duration:
            self._state.time = 0.0
        self._state.playing = True
        self._last = time.perf_counter()
        self._timer.start()
        self._bar.set_playing(True)
        self.stateChanged.emit()

    def pause(self) -> None:
        """Hold the current pose."""
        if not self._state.playing:
            return
        self._state.playing = False
        self._timer.stop()
        self._bar.set_playing(False)
        self.stateChanged.emit()
        self.poseSettled.emit()

    def toggle_play(self) -> None:
        """Pause when playing, otherwise play."""
        if self._state.playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        """Rewind to the start and show the rest pose from the store."""
        self._timer.stop()
        self._state.playing = False
        self._state.time = 0.0
        self._restore_rest()
        self._bar.set_playing(False)
        self._bar.set_time(0.0)
        self.stateChanged.emit()
        self.poseSettled.emit()

    def stop_for_edit(self, names: Iterable[str]) -> bool:
        """Stop when an edit of ``names`` would otherwise bake a pose.

        Args:
            names: Transforms about to be edited from viewport values.

        Returns:
            ``True`` when playback was stopped and the rest pose restored.
        """
        if not self._posed or not any(self.affects(name) for name in names):
            return False
        self.stop()
        return True

    def set_clip(self, name: str) -> None:
        """Switch to clip ``name`` from the start, keeping the play state.

        Args:
            name: A clip in the world. Unknown names are ignored.
        """
        if name == self._state.clip or name not in self._world.animations:
            return
        was_playing = self._state.playing
        self._timer.stop()
        self._restore_rest()
        self._state.clip = name
        self._state.time = 0.0
        self._state.playing = False
        self._sampler = self._build_sampler(name)
        self._bar.set_clips(list(self._world.animations), name)
        self._bar.set_duration(self.duration)
        self._bar.set_time(0.0)
        if was_playing:
            self.play()
        else:
            self._bar.set_playing(False)
            self.stateChanged.emit()
            self.poseSettled.emit()

    def seek(self, seconds: float) -> None:
        """Move the playhead and show that pose, playing or not.

        Args:
            seconds: Time from the start; clamped to the clip.
        """
        if self._sampler is None:
            return
        self._state.seek(seconds, self.duration)
        self._apply_pose()
        self._bar.set_time(self._state.time)
        self.stateChanged.emit()
        if not self._state.playing:
            self.poseSettled.emit()

    def set_loop(self, loop: bool) -> None:
        """Wrap at the end (``True``) or stop there.

        Args:
            loop: Whether playback wraps.
        """
        self._state.loop = loop
        self._bar.set_loop(loop)
        self.stateChanged.emit()

    def set_speed(self, speed: float) -> None:
        """Set the playback rate.

        Args:
            speed: Clip seconds per wall second; ``1.0`` is real time.
        """
        self._state.speed = max(0.0, float(speed))
        self._bar.set_speed(self._state.speed)
        self.stateChanged.emit()

    # -- internals -------------------------------------------------------

    def _on_play_toggled(self, playing: bool) -> None:
        if playing:
            self.play()
            if not self._state.playing:
                # Nothing to play: release the button again.
                self._bar.set_playing(False)
        else:
            self.pause()

    def _tick(self) -> None:
        if self._sampler is None:
            self._timer.stop()
            return
        now = time.perf_counter()
        dt = now - self._last
        self._last = now
        finished = self._state.advance(dt, self.duration)
        self._apply_pose()
        self._bar.set_time(self._state.time)
        if finished:
            self._timer.stop()
            self._bar.set_playing(False)
        self.stateChanged.emit()
        if finished:
            self.poseSettled.emit()

    def _apply_pose(self) -> None:
        if self._sampler is None:
            return
        components: list[TransformComponent] = []
        for name, sample in self._sampler.sample(self._state.time).items():
            stored = self._world.transforms.get(name)
            if stored is None:
                continue
            components.append(posed_transform(stored, sample))
            self._posed.add(name)
        if components:
            self._viewport.apply_transforms_local(components)

    def _restore_rest(self) -> None:
        components = [
            stored
            for name in self._posed
            if (stored := self._world.transforms.get(name)) is not None
        ]
        self._posed.clear()
        if components:
            self._viewport.apply_transforms_local(components)
