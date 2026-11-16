"""USD stage editor: the stage is the document, Hydra Storm is the renderer.

This app is the step-2 package from ``docs/usd-architecture.md``. It composes
USD files instead of converting them, draws them with ``UsdImagingGL`` when
the imaging build from ``scripts/build_usd_imaging.cmd`` is on ``PYTHONPATH``,
and keeps the PySide6 shell and mtqt widgets.
"""

from __future__ import annotations

from stage_editor.document import StageDocument
from stage_editor.hydra_view import HydraView, hydra_available
from stage_editor.outliner import Outliner, PrimModel

__all__ = ["HydraView", "Outliner", "PrimModel", "StageDocument", "hydra_available"]
