"""Camera navigation presets for the pygfx orbit controller.

Each preset maps pygfx controller keys (``"alt+mouse1"``) to action tuples.
pygfx numbers mouse buttons 1 = left, 2 = right, 3 = middle.
"""

from __future__ import annotations

from typing import Literal

NavigationStyle = Literal["maya", "blender", "pygfx"]

_ROTATE = ("rotate", "drag", (0.005, 0.005))
_PAN = ("pan", "drag", (1, 1))
# Orbit cameras keep aspect, so only the vertical drag zooms (up = in).
_DOLLY = ("zoom", "drag", (0.005, -0.005))
_WHEEL = ("zoom", "push", -0.001)

NAVIGATION_CONTROLS: dict[NavigationStyle, dict[str, tuple[object, ...]]] = {
    # Alt+LMB tumble, Alt+MMB track, Alt+RMB dolly. Plain LMB stays free
    # for picking and the manipulator.
    "maya": {
        "alt+mouse1": _ROTATE,
        "alt+mouse3": _PAN,
        "alt+mouse2": _DOLLY,
        "wheel": _WHEEL,
    },
    # MMB orbit, Shift+MMB pan, Ctrl+MMB zoom.
    "blender": {
        "mouse3": _ROTATE,
        "shift+mouse3": _PAN,
        "control+mouse3": _DOLLY,
        "wheel": _WHEEL,
    },
    # Stock pygfx: LMB orbit, RMB pan.
    "pygfx": {
        "mouse1": _ROTATE,
        "mouse2": _PAN,
        "wheel": _WHEEL,
        "alt+wheel": ("fov", "push", -0.01),
    },
}

NAVIGATION_HINTS: dict[NavigationStyle, str] = {
    "maya": "Alt+LMB tumble · Alt+MMB track · Alt+RMB / wheel dolly",
    "blender": "MMB orbit · Shift+MMB pan · Ctrl+MMB / wheel zoom",
    "pygfx": "LMB orbit · RMB pan · wheel zoom",
}


def navigation_controls(style: NavigationStyle) -> dict[str, tuple[object, ...]]:
    """Return a fresh controls mapping for ``style``.

    Args:
        style: ``maya``, ``blender``, or ``pygfx``.

    Returns:
        A dict suitable for ``gfx.OrbitController.controls``.

    Raises:
        ValueError: When ``style`` is not a known preset.
    """
    if style not in NAVIGATION_CONTROLS:
        msg = f"unsupported navigation style {style!r}"
        raise ValueError(msg)
    return dict(NAVIGATION_CONTROLS[style])
