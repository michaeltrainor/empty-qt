"""Standalone QApplication helper.

Library widgets and models never call this. Use it only from a standalone
script when you do not already have a ``QApplication`` (a DCC host does).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication


def ensure_application(argv: Sequence[str] | None = None) -> QApplication:
    """Return the process ``QApplication``, creating one only if needed.

    Args:
        argv: Arguments forwarded to ``QApplication`` when one is created.
            Defaults to ``sys.argv``. Ignored when an instance already exists.

    Returns:
        The existing or newly created ``QApplication``.

    Raises:
        RuntimeError: If a non-widget ``QCoreApplication`` is already running.
    """
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    if instance is not None:
        msg = "A QCoreApplication exists that is not a QApplication"
        raise RuntimeError(msg)
    args = list(argv) if argv is not None else sys.argv
    return QApplication(args)
