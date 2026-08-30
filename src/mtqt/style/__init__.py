"""Load and apply packaged Qt Style Sheets."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtWidgets import QApplication, QWidget

_KNOWN = ("base", "widgets")


def load_stylesheet(*names: str) -> str:
    """Return concatenated QSS for the named sheets.

    Args:
        names: Sheet names without the ``.qss`` suffix (``base``, ``widgets``).

    Returns:
        Combined stylesheet text.

    Raises:
        ValueError: If ``names`` is empty or a name is unknown.
        FileNotFoundError: If a sheet file is missing from the package.
    """
    if not names:
        msg = "At least one stylesheet name is required"
        raise ValueError(msg)
    parts: list[str] = []
    root = files("mtqt.style").joinpath("qss")
    for name in names:
        if name not in _KNOWN:
            msg = f"Unknown stylesheet {name!r}; expected one of {_KNOWN}"
            raise ValueError(msg)
        path = root.joinpath(f"{name}.qss")
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def apply_stylesheet(target: QWidget | QApplication, *names: str) -> None:
    """Apply named sheets to ``target`` via ``setStyleSheet``.

    Call this on your tool's top-level widget, not on ``QApplication``,
    when running inside a host that already has chrome (Maya, Houdini).

    Args:
        target: Widget or application that receives the sheet.
        names: Sheet names forwarded to :func:`load_stylesheet`.
    """
    target.setStyleSheet(load_stylesheet(*names))
