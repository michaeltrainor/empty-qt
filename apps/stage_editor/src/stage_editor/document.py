"""The stage as the document: open, reference, save, time, and change notices."""

from __future__ import annotations

import logging
from pathlib import Path

from pxr import Sdf, Tf, Usd, UsdGeom
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

#: Suffixes the editor opens and references.
USD_SUFFIXES: tuple[str, ...] = (".usd", ".usda", ".usdc", ".usdz")
#: Root-layer ``customLayerData`` key that marks a scene this editor wrote.
LAYER_MARKER = "stage_editor:version"
LAYER_VERSION = 1
ROOT = Sdf.Path("/World")
TIME_CODES_PER_SECOND = 24.0


def is_usd_file(path: Path) -> bool:
    """Return whether ``path`` has a USD layer suffix.

    Args:
        path: Candidate file path.
    """
    return path.suffix.lower() in USD_SUFFIXES


class StageDocument(QObject):
    """One open scene: a ``Usd.Stage`` whose root layer is what Save writes.

    A new document is an in-memory stage with a ``/World`` default prim.
    Opening a file the editor saved earlier edits that file in place. Any
    other USD file is foreign: it is referenced under ``/World`` of a new
    untitled document, so Save never overwrites someone else's asset.

    The document relays ``Usd.Notice.ObjectsChanged`` as :attr:`changed`
    and owns the current time code, which the view and the timeline share.

    Signals:
        stageReplaced(): A new stage is in place (new, open, save as).
        changed(list, list): Resynced prim paths and info-only changed
            paths, as strings, from one ``ObjectsChanged`` notice.
        timeChanged(float): The current time code moved.

    Args:
        parent: Owning ``QObject``.
    """

    stageReplaced = Signal()
    changed = Signal(list, list)
    timeChanged = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stage: Usd.Stage | None = None
        self._listener: Tf.Listener | None = None
        self._path: Path | None = None
        self._edited = False
        self._time = 0.0
        self.new()

    # -- state ----------------------------------------------------------------

    @property
    def stage(self) -> Usd.Stage:
        """The composed stage. Always set after construction."""
        assert self._stage is not None
        return self._stage

    @property
    def path(self) -> Path | None:
        """Where Save writes, or ``None`` for an untitled document."""
        return self._path

    @property
    def title(self) -> str:
        """The file name, or ``Untitled``."""
        return self._path.name if self._path else "Untitled"

    def has_unsaved_changes(self) -> bool:
        """Return whether the stage changed since the last new, open, or save."""
        return self._edited

    @property
    def time(self) -> float:
        """Current time code."""
        return self._time

    @time.setter
    def time(self, value: float) -> None:
        if value == self._time:
            return
        self._time = value
        self.timeChanged.emit(value)

    def time_range(self) -> tuple[float, float]:
        """Return the stage's authored start and end time codes, or ``(0, 0)``."""
        stage = self.stage
        if not stage.HasAuthoredTimeCodeRange():
            return (0.0, 0.0)
        return (stage.GetStartTimeCode(), stage.GetEndTimeCode())

    def time_codes_per_second(self) -> float:
        """Return the stage's time codes per second, never zero."""
        return self.stage.GetTimeCodesPerSecond() or TIME_CODES_PER_SECOND

    # -- lifecycle ------------------------------------------------------------

    def new(self) -> None:
        """Replace the document with an empty untitled stage."""
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
        stage.SetFramesPerSecond(TIME_CODES_PER_SECOND)
        world = UsdGeom.Xform.Define(stage, ROOT).GetPrim()
        stage.SetDefaultPrim(world)
        self._mark(stage.GetRootLayer())
        self._bind(stage, None)

    def open(self, path: Path) -> bool:
        """Open a USD file.

        Args:
            path: A ``.usd``, ``.usda``, ``.usdc``, or ``.usdz`` file.

        Returns:
            ``True`` when the file is a scene this editor saved and is now
            edited in place; ``False`` when it was foreign and has been
            referenced into a new untitled document instead.

        Raises:
            ValueError: The file is not a USD layer or cannot be opened.
        """
        if not is_usd_file(path):
            msg = f"not a USD file: {path}"
            raise ValueError(msg)
        layer = Sdf.Layer.FindOrOpen(str(path))
        if layer is None:
            msg = f"cannot open {path}"
            raise ValueError(msg)
        if LAYER_MARKER not in layer.customLayerData:
            self.new()
            self.add_reference(path)
            return False
        stage = Usd.Stage.Open(layer, Usd.Stage.LoadAll)
        if stage is None:
            msg = f"cannot compose {path}"
            raise ValueError(msg)
        self._bind(stage, path)
        return True

    def add_reference(self, path: Path, parent: Sdf.Path = ROOT) -> Sdf.Path:
        """Reference a USD file under ``parent`` and return the new prim's path.

        The prim is an ``over`` named after the file, so the referenced
        prim's type comes through. A file without a default prim is
        referenced at its first root prim.

        Args:
            path: The file to reference.
            parent: Prim under which the reference is placed.

        Raises:
            ValueError: The file cannot be opened or holds no prims.
        """
        layer = Sdf.Layer.FindOrOpen(str(path))
        if layer is None:
            msg = f"cannot open {path}"
            raise ValueError(msg)
        target = Sdf.Path.emptyPath
        if not layer.defaultPrim:
            roots = layer.rootPrims
            if not roots:
                msg = f"no prims in {path}"
                raise ValueError(msg)
            target = roots[0].path
        prim_path = self._unique_child(parent, Tf.MakeValidIdentifier(path.stem))
        prim = self.stage.OverridePrim(prim_path)
        prim.GetReferences().AddReference(str(path), target)
        self._adopt_time_range(layer)
        return prim_path

    def _adopt_time_range(self, layer: Sdf.Layer) -> None:
        """Widen the stage's time range to cover ``layer``'s authored range.

        Stage time comes from the root layer only, so a referenced
        animation would otherwise have no range to play.
        """
        if not (layer.HasStartTimeCode() and layer.HasEndTimeCode()):
            return
        stage = self.stage
        start, end = layer.startTimeCode, layer.endTimeCode
        if stage.HasAuthoredTimeCodeRange():
            start = min(start, stage.GetStartTimeCode())
            end = max(end, stage.GetEndTimeCode())
        elif layer.HasTimeCodesPerSecond():
            stage.SetTimeCodesPerSecond(layer.timeCodesPerSecond)
            stage.SetFramesPerSecond(layer.framesPerSecond or layer.timeCodesPerSecond)
        stage.SetStartTimeCode(start)
        stage.SetEndTimeCode(end)
        self._time = start
        self.timeChanged.emit(start)

    def save(self) -> None:
        """Write the root layer to :attr:`path`.

        Raises:
            ValueError: The document is untitled; use :meth:`save_as`.
        """
        if self._path is None:
            raise ValueError("untitled document; use save_as")
        self.stage.GetRootLayer().Save()
        self._edited = False

    def save_as(self, path: Path) -> None:
        """Write the document to ``path`` and keep editing that file.

        Args:
            path: Destination; its suffix picks the USD file format.

        Raises:
            ValueError: The suffix is not a USD suffix or the write failed.
        """
        if not is_usd_file(path):
            msg = f"not a USD file: {path}"
            raise ValueError(msg)
        root = self.stage.GetRootLayer()
        self._mark(root)
        if not root.Export(str(path)):
            msg = f"cannot write {path}"
            raise ValueError(msg)
        self.open(path)

    # -- internals ------------------------------------------------------------

    def _mark(self, layer: Sdf.Layer) -> None:
        data = dict(layer.customLayerData)
        data[LAYER_MARKER] = LAYER_VERSION
        layer.customLayerData = data

    def _unique_child(self, parent: Sdf.Path, stem: str) -> Sdf.Path:
        stage = self.stage
        candidate = parent.AppendChild(stem)
        suffix = 1
        while stage.GetPrimAtPath(candidate):
            suffix += 1
            candidate = parent.AppendChild(f"{stem}_{suffix}")
        return candidate

    def _bind(self, stage: Usd.Stage, path: Path | None) -> None:
        if self._listener is not None:
            self._listener.Revoke()
        self._stage = stage
        self._path = path
        self._edited = False
        start, _end = self.time_range()
        self._time = start
        self._listener = Tf.Notice.Register(
            Usd.Notice.ObjectsChanged, self._on_objects_changed, stage
        )
        self.stageReplaced.emit()
        self.timeChanged.emit(self._time)

    def _on_objects_changed(
        self, notice: Usd.Notice.ObjectsChanged, _sender: Usd.Stage
    ) -> None:
        resynced = [str(p) for p in notice.GetResyncedPaths()]
        info = [str(p) for p in notice.GetChangedInfoOnlyPaths()]
        self._edited = True
        self.changed.emit(resynced, info)
