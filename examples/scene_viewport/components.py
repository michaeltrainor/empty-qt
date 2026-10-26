"""Scene components attached to named transform nodes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Space = Literal["local", "world"]
LightKind = Literal["directional", "point", "spot"]
PrimitiveKind = Literal["box", "plane", "sphere"]


class TransformComponent(BaseModel):
    """An empty transform node: local TRS, optional parent, no geometry.

    ``position`` / ``rotation`` / ``scale`` are always stored in **local**
    space relative to ``parent`` (or the scene root when ``parent`` is
    empty). ``space`` is the panel/gizmo edit preference (default
    ``world``): world mode shows and edits world-space values while the
    store keeps local TRS. ``pivot`` is a local-space offset for the
    manipulator (and Edit Pivot mode).

    Meshes and lights attach to a transform via their ``transform`` field.
    Selection always resolves to the transform (the spatial object).
    """

    name: str = Field(frozen=True, title="Name")
    parent: str = Field(
        default="",
        frozen=True,
        title="Parent",
        description="Parent transform name, or empty for scene root.",
    )
    space: Space = Field(
        default="world",
        title="Space",
        description="Panel/gizmo edit space. TRS is always stored local.",
    )
    position: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        title="Position",
    )
    rotation: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        title="Rotation",
        description="Euler XYZ rotation in radians.",
    )
    scale: tuple[float, float, float] = Field(
        default=(1.0, 1.0, 1.0),
        title="Scale",
    )
    pivot: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        title="Pivot",
        description="Local-space manipulator offset from the transform origin.",
    )
    visible: bool = Field(
        default=True,
        title="Visible",
        description="Show this transform and its shapes in the viewport.",
    )


class MeshComponent(BaseModel):
    """A drawable mesh shape parented under a transform.

    Has no local TRS — placement comes from :attr:`transform`. Shown as the
    Mesh tab when that transform is selected.
    """

    name: str = Field(frozen=True, title="Name")
    transform: str = Field(frozen=True, title="Transform")
    kind: PrimitiveKind = Field(frozen=True, title="Kind")
    color: str = Field(
        default="#8899aa",
        title="Color",
        json_schema_extra={"mtqt_editor": "color"},
    )
    roughness: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        title="Roughness",
    )
    metalness: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        title="Metalness",
    )
    cast_shadow: bool = Field(default=True, title="Cast Shadow")
    receive_shadow: bool = Field(default=True, title="Receive Shadow")


class LightComponent(BaseModel):
    """A light shape parented under a transform.

    Placement comes from :attr:`transform`. Shown as the Light tab when that
    transform is selected. Viewport helpers make the light pickable.

    ``distance`` and ``decay`` apply to point (and spot) lights; ``0``
    distance means unlimited range. Directional lights ignore them.
    """

    name: str = Field(frozen=True, title="Name")
    transform: str = Field(frozen=True, title="Transform")
    kind: LightKind = Field(default="directional", frozen=True, title="Kind")
    color: str = Field(
        default="#fff5e6",
        title="Color",
        json_schema_extra={"mtqt_editor": "color"},
    )
    intensity: float = Field(default=1.4, ge=0.0, le=20.0, title="Intensity")
    distance: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        title="Distance",
        description="Point/spot range. 0 = unlimited.",
    )
    decay: float = Field(
        default=2.0,
        ge=0.0,
        le=4.0,
        title="Decay",
        description="Point/spot falloff. 2 is physically based.",
    )
    cast_shadow: bool = Field(
        default=True,
        title="Cast Shadow",
        description="Enable shadow maps for this light (meshes must receive).",
    )
