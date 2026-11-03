"""Set the Qt platform, and skip this suite when the app is not installed."""

from __future__ import annotations

import importlib.util
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# A plain `uv sync` installs only mtqt. `uv sync --all-packages` adds this app.
if importlib.util.find_spec("scene_viewport") is None:
    collect_ignore_glob = ["*"]
