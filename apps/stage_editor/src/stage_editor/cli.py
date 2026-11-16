"""Command line: usdview's flags for what to open and how to show it."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from stage_editor.display import COMPLEXITIES, DRAW_MODES


def build_parser() -> argparse.ArgumentParser:
    """Return the ``stage-editor`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="stage-editor",
        description="Edit and inspect a USD stage. Flags follow usdview.",
    )
    parser.add_argument("file", nargs="?", type=Path, help="USD file to open")
    parser.add_argument(
        "--camera",
        "--cam",
        default="",
        help="look through this camera: a prim path, or a camera prim's name",
    )
    parser.add_argument(
        "--unloaded",
        action="store_true",
        help="open with every payload unloaded",
    )
    parser.add_argument(
        "--mask",
        default="",
        help="comma-separated prim paths to populate; everything else is skipped",
    )
    parser.add_argument(
        "--complexity",
        "-c",
        choices=[key for _label, key, _value in COMPLEXITIES],
        default=None,
        help="subdivision refine complexity",
    )
    parser.add_argument(
        "--draw-mode",
        choices=[name.removeprefix("DRAW_").lower() for _label, name in DRAW_MODES],
        default=None,
        help="viewport draw mode",
    )
    parser.add_argument(
        "--select",
        default="",
        help="comma-separated prim paths to select once open",
    )
    parser.add_argument(
        "--cf",
        "--frame",
        dest="frame",
        type=float,
        default=None,
        help="time code to show first",
    )
    return parser


def split_paths(text: str) -> list[str]:
    """Split a comma-separated prim path list, dropping blanks.

    Args:
        text: For example ``"/World/A, /World/B"``.
    """
    return [part.strip() for part in text.split(",") if part.strip()]


def resolve_camera(cameras: Sequence[str], name: str) -> str:
    """Return the camera path ``name`` refers to, or ``""`` if none matches.

    Like usdview, a bare name matches the first camera prim with that name.

    Args:
        cameras: Camera prim paths on the stage.
        name: A path or a prim name.
    """
    if not name:
        return ""
    if name in cameras:
        return name
    for path in cameras:
        if path.rsplit("/", 1)[-1] == name:
            return path
    return ""
