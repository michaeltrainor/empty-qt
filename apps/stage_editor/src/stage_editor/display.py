"""View settings the Display menu edits: draw mode, complexity, purposes, lights.

The settings are plain data so the window can persist them and the command
line can set them without a GL context; :class:`~stage_editor.hydra_view.HydraView`
turns them into ``UsdImagingGL.RenderParams``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass

from pxr import Usd

log = logging.getLogger(__name__)

#: Menu label and ``UsdImagingGL.DrawMode`` member name, in usdview's order.
DRAW_MODES: tuple[tuple[str, str], ...] = (
    ("Smooth Shaded", "DRAW_SHADED_SMOOTH"),
    ("Flat Shaded", "DRAW_SHADED_FLAT"),
    ("Wireframe On Surface", "DRAW_WIREFRAME_ON_SURFACE"),
    ("Wireframe", "DRAW_WIREFRAME"),
    ("Points", "DRAW_POINTS"),
    ("Geom Only", "DRAW_GEOM_ONLY"),
    ("Geom Smooth", "DRAW_GEOM_SMOOTH"),
    ("Geom Flat", "DRAW_GEOM_FLAT"),
)

#: Menu label, command-line name, and refine complexity, as usdview defines them.
COMPLEXITIES: tuple[tuple[str, str, float], ...] = (
    ("Low", "low", 1.0),
    ("Medium", "medium", 1.1),
    ("High", "high", 1.2),
    ("Very High", "veryhigh", 1.3),
)

#: Menu label and linear RGBA clear colour.
BACKGROUNDS: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("Black", (0.0, 0.0, 0.0, 1.0)),
    ("Dark Grey", (0.18, 0.18, 0.20, 1.0)),
    ("Grey", (0.5, 0.5, 0.5, 1.0)),
    ("White", (1.0, 1.0, 1.0, 1.0)),
)


#: Menu label and pick mode: what a click in the view selects.
PICK_MODES: tuple[tuple[str, str], ...] = (
    ("Prims", "prims"),
    ("Models", "models"),
    ("Instances", "instances"),
)

#: Storm render outputs the AOV menu offers.
AOVS: tuple[str, ...] = ("color", "depth", "primId", "Neye")


def pick_target(stage: Usd.Stage, path: str, mode: str) -> str:
    """Map a picked gprim path to what ``mode`` selects, as usdview does.

    ``models`` walks up to the nearest ancestor that is a model and not a
    group (a component or subcomponent asset). ``instances`` walks up from
    an instance proxy to its instance prim. Anything without such an
    ancestor keeps the picked path.

    Args:
        stage: The stage that was picked.
        path: The hit prim path, or ``""`` for a miss.
        mode: ``prims``, ``models``, or ``instances``.

    Returns:
        The prim path to select, or ``""`` for a miss.
    """
    if not path or mode == "prims":
        return path
    prim = stage.GetPrimAtPath(path)
    probe = prim
    while probe and not probe.IsPseudoRoot():
        if mode == "models" and probe.IsModel() and not probe.IsGroup():
            return str(probe.GetPath())
        if mode == "instances" and probe.IsInstance():
            return str(probe.GetPath())
        probe = probe.GetParent()
    return path


def complexity_by_name(name: str) -> float:
    """Return the refine complexity for a command-line name such as ``high``.

    Args:
        name: One of ``low``, ``medium``, ``high``, ``veryhigh``.

    Raises:
        ValueError: The name is not a known complexity.
    """
    for _label, key, value in COMPLEXITIES:
        if key == name.lower():
            return value
    msg = f"unknown complexity {name!r}"
    raise ValueError(msg)


def complexity_label(value: float) -> str:
    """Return the menu label closest to a refine complexity value."""
    label, _key, _value = min(COMPLEXITIES, key=lambda c: abs(c[2] - value))
    return label


@dataclass
class ViewSettings:
    """What the viewport draws and how. Field defaults match usdview's.

    Attributes:
        draw_mode: A ``UsdImagingGL.DrawMode`` member name.
        complexity: Subdivision refine complexity, 1.0 to 1.3.
        show_guides: Draw prims with ``guide`` purpose.
        show_proxy: Draw prims with ``proxy`` purpose.
        show_render: Draw prims with ``render`` purpose.
        cull_backfaces: Cull back faces unless a gprim is double sided.
        camera_light: Light the scene from the camera.
        dome_light: Add a white dome light in the session layer.
        scene_lights: Use the stage's UsdLux lights.
        scene_materials: Use bound materials; off draws display colour.
        background: Linear RGBA clear colour.
        show_hud: Draw the heads-up display.
        show_bounds: Draw the world bounds of the selected prims.
        aov: The Storm render output to show, such as ``color``.
        pick_mode: What a click selects; see :func:`pick_target`.
    """

    draw_mode: str = "DRAW_SHADED_SMOOTH"
    complexity: float = 1.0
    show_guides: bool = False
    show_proxy: bool = True
    show_render: bool = False
    cull_backfaces: bool = True
    camera_light: bool = True
    dome_light: bool = False
    scene_lights: bool = True
    scene_materials: bool = True
    background: tuple[float, float, float, float] = BACKGROUNDS[1][1]
    show_hud: bool = True
    show_bounds: bool = False
    aov: str = "color"
    pick_mode: str = "prims"

    def to_json(self) -> str:
        """Return the settings as a JSON object string."""
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, text: str) -> ViewSettings:
        """Read settings written by :meth:`to_json`, ignoring unknown keys.

        Args:
            text: A JSON object string; anything unreadable gives defaults.
        """
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        names = {f.name for f in dataclasses.fields(cls)}
        known = {k: v for k, v in data.items() if k in names}
        if "background" in known:
            known["background"] = tuple(known["background"])
        try:
            return cls(**known)
        except TypeError:
            log.warning("ignoring unreadable view settings")
            return cls()
