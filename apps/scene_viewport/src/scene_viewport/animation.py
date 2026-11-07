"""Keyframe clips that animate transform TRS over time.

A clip is document data, saved with the scene next to the components.
Playback (see :mod:`scene_viewport.animation_player`) reads clips and
writes poses to the viewport only; it never changes the stored
:class:`~scene_viewport.components.TransformComponent` values, which stay
the rest pose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Interpolation = Literal["step", "linear", "cubicspline"]
ChannelPath = Literal["translation", "rotation", "scale"]


class AnimationChannel(BaseModel):
    """Keyframes for one TRS property of one transform.

    ``values`` holds one row per key: three floats for ``translation`` and
    ``scale``, four (a unit quaternion as ``x, y, z, w``, as pylinalg
    orders it) for ``rotation``. ``cubicspline`` channels keep glTF's
    layout of three rows per key: in-tangent, value, out-tangent.
    Rotations are stored as quaternions so playback can interpolate them
    properly; they become the component's Euler XYZ only when a pose is
    written.

    Args:
        transform: Name of the animated transform.
        path: Which property the channel animates.
        interpolation: How values between keys are computed.
        times: Key times in seconds, strictly increasing.
        values: Key values, as described above.
    """

    transform: str = Field(title="Transform")
    path: ChannelPath = Field(title="Path")
    interpolation: Interpolation = Field(default="linear", title="Interpolation")
    times: list[float] = Field(default_factory=list, title="Times")
    values: list[list[float]] = Field(default_factory=list, title="Values")

    @property
    def components(self) -> int:
        """Floats per value: four for a rotation, three otherwise."""
        return 4 if self.path == "rotation" else 3

    @model_validator(mode="after")
    def _check_layout(self) -> AnimationChannel:
        if not self.times:
            msg = "a channel needs at least one key"
            raise ValueError(msg)
        if any(b <= a for a, b in zip(self.times, self.times[1:], strict=False)):
            msg = "channel times must be strictly increasing"
            raise ValueError(msg)
        rows = len(self.times) * (3 if self.interpolation == "cubicspline" else 1)
        if len(self.values) != rows:
            msg = f"channel has {len(self.times)} keys but {len(self.values)} values"
            raise ValueError(msg)
        if any(len(value) != self.components for value in self.values):
            msg = f"{self.path} values need {self.components} floats each"
            raise ValueError(msg)
        return self


class AnimationClip(BaseModel):
    """A named set of channels played together.

    Args:
        name: Clip name, unique within a scene.
        channels: The transforms and properties the clip animates.
    """

    name: str = Field(title="Name")
    channels: list[AnimationChannel] = Field(default_factory=list, title="Channels")

    def duration(self) -> float:
        """Return the last key time over all channels, in seconds."""
        return max((channel.times[-1] for channel in self.channels), default=0.0)

    def targets(self) -> set[str]:
        """Return the names of the transforms the clip animates."""
        return {channel.transform for channel in self.channels}
