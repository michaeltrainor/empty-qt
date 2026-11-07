"""Evaluate animation clips at a time, and keep a playhead.

No Qt in here: the window's timer calls :meth:`PlaybackState.advance` and
:meth:`ClipSampler.sample` each tick, then :func:`posed_transform` turns a
sample into the component the viewport applies. Keyframe values are kept
as numpy arrays per channel, built once from the pydantic clip.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pylinalg as la
from numpy.typing import NDArray

from scene_viewport.animation import AnimationClip, ChannelPath, Interpolation
from scene_viewport.components import TransformComponent
from scene_viewport.xform_math import Vec3, as_vec3

Quat = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class SampledTrs:
    """The animated parts of one transform at one time.

    Args:
        position: Local position, or ``None`` when the clip leaves it alone.
        rotation: Local rotation as a unit quaternion ``x, y, z, w``, or
            ``None``.
        scale: Local scale, or ``None``.
    """

    position: Vec3 | None = None
    rotation: Quat | None = None
    scale: Vec3 | None = None


@dataclass(frozen=True, slots=True)
class _Track:
    """One channel's keys as arrays.

    ``values`` is ``(n, c)``, or ``(n, 3, c)`` for ``cubicspline`` with the
    in-tangent, value, and out-tangent of each key along the middle axis.
    """

    transform: str
    path: ChannelPath
    interpolation: Interpolation
    times: NDArray[np.float64]
    values: NDArray[np.float64]


class ClipSampler:
    """Evaluates one clip at a time in seconds.

    Args:
        clip: The clip to play. Its channels are converted to arrays once.
    """

    def __init__(self, clip: AnimationClip) -> None:
        self._tracks: list[_Track] = []
        for channel in clip.channels:
            values = np.asarray(channel.values, dtype=np.float64)
            if channel.interpolation == "cubicspline":
                values = values.reshape(len(channel.times), 3, channel.components)
            self._tracks.append(
                _Track(
                    transform=channel.transform,
                    path=channel.path,
                    interpolation=channel.interpolation,
                    times=np.asarray(channel.times, dtype=np.float64),
                    values=values,
                )
            )
        self._duration = clip.duration()
        self._targets = frozenset(clip.targets())

    @property
    def duration(self) -> float:
        """Length of the clip in seconds: its last key time."""
        return self._duration

    @property
    def targets(self) -> frozenset[str]:
        """Names of the transforms the clip animates."""
        return self._targets

    def sample(self, time: float) -> dict[str, SampledTrs]:
        """Evaluate every channel at ``time``.

        Times before the first key hold the first value; times after the
        last key hold the last.

        Args:
            time: Seconds from the start of the clip.

        Returns:
            Transform name → its animated position, rotation, and scale.
        """
        result: dict[str, SampledTrs] = {}
        for track in self._tracks:
            value = _sample_track(track, time)
            current = result.get(track.transform, SampledTrs())
            if track.path == "translation":
                current = SampledTrs(as_vec3(value), current.rotation, current.scale)
            elif track.path == "rotation":
                x, y, z, w = (float(v) for v in value[:4])
                current = SampledTrs(current.position, (x, y, z, w), current.scale)
            else:
                current = SampledTrs(current.position, current.rotation, as_vec3(value))
            result[track.transform] = current
        return result


def _sample_track(track: _Track, time: float) -> NDArray[np.float64]:
    """Return a track's value at ``time`` as a ``(c,)`` array."""
    times = track.times
    cubic = track.interpolation == "cubicspline"
    if time <= times[0]:
        return track.values[0, 1] if cubic else track.values[0]
    if time >= times[-1]:
        return track.values[-1, 1] if cubic else track.values[-1]
    index = int(np.searchsorted(times, time, side="right")) - 1
    t0, t1 = float(times[index]), float(times[index + 1])
    u = (time - t0) / (t1 - t0)
    if track.interpolation == "step":
        return track.values[index]
    if cubic:
        p0, m0 = track.values[index, 1], track.values[index, 2]
        p1, m1 = track.values[index + 1, 1], track.values[index + 1, 0]
        value = hermite(p0, m0, p1, m1, u, t1 - t0)
        if track.path == "rotation":
            value = value / max(float(np.linalg.norm(value)), 1e-12)
        return value
    a, b = track.values[index], track.values[index + 1]
    if track.path == "rotation":
        return quat_slerp(a, b, u)
    return a + (b - a) * u


def hermite(
    p0: NDArray[np.float64],
    m0: NDArray[np.float64],
    p1: NDArray[np.float64],
    m1: NDArray[np.float64],
    u: float,
    dt: float,
) -> NDArray[np.float64]:
    """Return the glTF cubic spline between two keys.

    Args:
        p0: Value at the first key.
        m0: Out-tangent of the first key, per second.
        p1: Value at the second key.
        m1: In-tangent of the second key, per second.
        u: Position between the keys, 0 to 1.
        dt: Seconds between the keys; scales the tangents as glTF requires.

    Returns:
        The interpolated value.
    """
    u2 = u * u
    u3 = u2 * u
    return (
        (2.0 * u3 - 3.0 * u2 + 1.0) * p0
        + dt * (u3 - 2.0 * u2 + u) * m0
        + (-2.0 * u3 + 3.0 * u2) * p1
        + dt * (u3 - u2) * m1
    )


def quat_slerp(
    a: NDArray[np.float64], b: NDArray[np.float64], u: float
) -> NDArray[np.float64]:
    """Interpolate two ``x, y, z, w`` quaternions along the shorter arc.

    Args:
        a: Start rotation.
        b: End rotation.
        u: Position between them, 0 to 1.

    Returns:
        A unit quaternion. Rotations less than a tenth of a degree apart
        are blended linearly and renormalized.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    dot = float(np.dot(a, b))
    if dot < 0.0:
        # q and -q are the same rotation; pick the one on the short arc.
        b = -b
        dot = -dot
    if dot > 0.9995:
        result = a + (b - a) * u
        return result / max(float(np.linalg.norm(result)), 1e-12)
    theta = float(np.arccos(min(dot, 1.0)))
    sin_theta = float(np.sin(theta))
    weight_a = float(np.sin((1.0 - u) * theta)) / sin_theta
    weight_b = float(np.sin(u * theta)) / sin_theta
    return weight_a * a + weight_b * b


def posed_transform(
    stored: TransformComponent, sample: SampledTrs
) -> TransformComponent:
    """Return ``stored`` with the sampled TRS written over its rest pose.

    The quaternion becomes the component's Euler XYZ radians here, at the
    last step, so interpolation never happened on Euler angles.

    Args:
        stored: The rest-pose component from the scene store.
        sample: Animated values; ``None`` fields keep the rest value.

    Returns:
        A copy for the viewport. The store is not changed.
    """
    update: dict[str, object] = {}
    if sample.position is not None:
        update["position"] = sample.position
    if sample.rotation is not None:
        update["rotation"] = as_vec3(la.quat_to_euler(sample.rotation, order="xyz"))
    if sample.scale is not None:
        update["scale"] = sample.scale
    return stored.model_copy(update=update)


@dataclass
class PlaybackState:
    """Where the playhead is and how it moves.

    Args:
        clip: Name of the clip being played, or ``""``.
        time: Playhead in seconds.
        playing: Whether :meth:`advance` moves the playhead.
        loop: Wrap at the end instead of stopping there.
        speed: Playback rate; ``1.0`` is real time.
    """

    clip: str = ""
    time: float = 0.0
    playing: bool = False
    loop: bool = True
    speed: float = 1.0

    def advance(self, dt: float, duration: float) -> bool:
        """Move the playhead by ``dt`` seconds of wall time.

        Args:
            dt: Seconds since the last call.
            duration: Length of the clip.

        Returns:
            ``True`` when playback reached the end and stopped (loop off).
        """
        if not self.playing:
            return False
        if duration <= 0.0:
            self.time = 0.0
            self.playing = False
            return True
        time = self.time + dt * self.speed
        if self.loop:
            self.time = time % duration
            return False
        if time >= duration:
            self.time = duration
            self.playing = False
            return True
        self.time = max(0.0, time)
        return False

    def seek(self, time: float, duration: float) -> None:
        """Move the playhead to ``time``, clamped to the clip.

        Args:
            time: Seconds from the start.
            duration: Length of the clip.
        """
        self.time = min(max(time, 0.0), max(duration, 0.0))
