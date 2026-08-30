"""Shared pytest fixtures. Set the Qt platform before PySide6 loads."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
