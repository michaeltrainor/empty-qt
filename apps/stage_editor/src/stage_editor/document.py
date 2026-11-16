"""The stage as the document: open, reference, save, time, and change notices."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pxr import Sdf, Tf, Usd, UsdGeom, UsdLux
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack

from stage_editor.inspection import format_value
from stage_editor.undo import SdfEditCommand

log = logging.getLogger(__name__)

#: Suffixes the editor opens and references.
USD_SUFFIXES: tuple[str, ...] = (".usd", ".usda", ".usdc", ".usdz")
#: Root-layer ``customLayerData`` key that marks a scene this editor wrote.
LAYER_MARKER = "stage_editor:version"
LAYER_VERSION = 1
ROOT = Sdf.Path("/World")
#: Session-layer prims the editor authors for viewing (the dome light).
#: The outliner hides this subtree.
SESSION_ROOT = Sdf.Path("/_StageEditor")
DOME_LIGHT = SESSION_ROOT.AppendChild("DomeLight")
TIME_CODES_PER_SECOND = 24.0


def is_usd_file(path: Path) -> bool:
    """Return whether ``path`` has a USD layer suffix.

    Args:
        path: Candidate file path.
    """
    return path.suffix.lower() in USD_SUFFIXES


def _new_root_layer(up_axis: str = UsdGeom.Tokens.y, meters: float = 1.0) -> Sdf.Layer:
    """Return an anonymous layer with a ``/World`` default prim and stage metadata."""
    layer = Sdf.Layer.CreateAnonymous("untitled.usda")
    Sdf.PrimSpec(layer, ROOT.name, Sdf.SpecifierDef, "Xform")
    layer.defaultPrim = ROOT.name
    layer.pseudoRoot.SetInfo(UsdGeom.Tokens.upAxis, up_axis)
    layer.pseudoRoot.SetInfo(UsdGeom.Tokens.metersPerUnit, meters)
    layer.timeCodesPerSecond = TIME_CODES_PER_SECOND
    layer.framesPerSecond = TIME_CODES_PER_SECOND
    data = dict(layer.customLayerData)
    data[LAYER_MARKER] = LAYER_VERSION
    layer.customLayerData = data
    return layer


def _reference_target(layer: Sdf.Layer, path: Path) -> Sdf.Path:
    """Return the prim a reference to ``layer`` targets: default or first root.

    Raises:
        ValueError: The layer holds no prims.
    """
    if layer.defaultPrim:
        return Sdf.Path.absoluteRootPath.AppendChild(layer.defaultPrim)
    roots = layer.rootPrims
    if not roots:
        msg = f"no prims in {path}"
        raise ValueError(msg)
    return roots[0].path


def _copy_time_range(source: Sdf.Layer, target: Sdf.Layer) -> None:
    """Give ``target`` the time range and rates of ``source``, where authored."""
    if not (source.HasStartTimeCode() and source.HasEndTimeCode()):
        return
    target.startTimeCode = source.startTimeCode
    target.endTimeCode = source.endTimeCode
    if source.HasTimeCodesPerSecond():
        target.timeCodesPerSecond = source.timeCodesPerSecond
        target.framesPerSecond = source.framesPerSecond or source.timeCodesPerSecond


class StageDocument(QObject):
    """One open scene: a ``Usd.Stage`` whose root layer stack is what Save writes.

    A new document is an in-memory stage with a ``/World`` default prim.
    Opening a file the editor saved earlier edits that file in place. Any
    other USD file is foreign: it is referenced under ``/World`` of a new
    untitled document, so Save never overwrites someone else's asset.

    View state that is never saved (visibility and active overrides, the
    dome light) goes to the stage's session layer through
    :meth:`session_edits`; edits there do not mark the document unsaved.

    The document relays ``Usd.Notice.ObjectsChanged`` as :attr:`changed`
    and owns the current time code, which the view and the timeline share.

    Signals:
        stageReplaced(): A new stage is in place (new, open, save as).
        changed(list, list): Resynced prim paths and info-only changed
            paths, as strings, from one ``ObjectsChanged`` notice.
        timeChanged(float): The current time code moved.
        dirtyChanged(bool): :meth:`has_unsaved_changes` flipped.
        layersChanged(): A layer's dirtiness, muting, or the edit target
            changed; the layer panel refreshes.

    Args:
        parent: Owning ``QObject``.
    """

    stageReplaced = Signal()
    changed = Signal(list, list)
    timeChanged = Signal(float)
    dirtyChanged = Signal(bool)
    layersChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stage: Usd.Stage | None = None
        self._listener: Tf.Listener | None = None
        self._path: Path | None = None
        self._edited = False
        self._time = 0.0
        self._source_map: tuple[Sdf.Path, Sdf.Path] | None = None
        #: Undoable edits; cleared whenever the stage is replaced.
        self.undo_stack = QUndoStack(self)
        # Tf holds Python callbacks weakly; these listeners live as long as self.
        self._layer_listeners = [
            Tf.Notice.RegisterGlobally(Sdf.Notice.LayersDidChange, self._on_layers),
            Tf.Notice.RegisterGlobally(
                Sdf.Notice.LayerDirtinessChanged, self._on_dirtiness
            ),
        ]
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
        """Return whether the root layer stack changed since new, open, or save.

        Session-layer edits do not count.
        """
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

    def map_source_path(self, path: str) -> str:
        """Map a path in an opened foreign file to its path on this stage.

        A foreign file is referenced under ``/World``, so ``/Kitchen/Arch``
        in the file is ``/World/Kitchen/Arch`` here. Paths of a scene this
        editor saved, and paths outside the referenced prim, are returned
        unchanged.

        Args:
            path: An absolute prim path as a string.
        """
        if self._source_map is None:
            return path
        source, target = self._source_map
        sdf_path = Sdf.Path(path)
        if not sdf_path.HasPrefix(source):
            return path
        return str(sdf_path.ReplacePrefix(source, target))

    # -- lifecycle ------------------------------------------------------------

    def new(self) -> None:
        """Replace the document with an empty untitled stage."""
        self._source_map = None
        self._bind(Usd.Stage.Open(_new_root_layer()), None)

    def open(self, path: Path, *, mask: Sequence[str] = (), load: bool = True) -> bool:
        """Open a USD file.

        Args:
            path: A ``.usd``, ``.usda``, ``.usdc``, or ``.usdz`` file.
            mask: Prim paths to populate (``usdview --mask``); empty for all.
                For a foreign file they are paths in that file.
            load: Load payloads; ``False`` opens with everything unloaded.

        Returns:
            ``True`` when the file is a scene this editor saved and is now
            edited in place; ``False`` when it was foreign and has been
            referenced into a new untitled document instead.

        Raises:
            ValueError: The file is not a USD layer or cannot be opened, or a
                mask entry is not an absolute prim path.
        """
        if not is_usd_file(path):
            msg = f"not a USD file: {path}"
            raise ValueError(msg)
        layer = Sdf.Layer.FindOrOpen(str(path))
        if layer is None:
            msg = f"cannot open {path}"
            raise ValueError(msg)
        bad = [
            p
            for p in mask
            if not Sdf.Path.IsValidPathString(p) or not p.startswith("/")
        ]
        if bad:
            msg = f"not absolute prim paths: {', '.join(bad)}"
            raise ValueError(msg)
        load_set = Usd.Stage.LoadAll if load else Usd.Stage.LoadNone
        if LAYER_MARKER in layer.customLayerData:
            self._source_map = None
            stage = self._open_stage(layer, mask, load_set)
            if stage is None:
                msg = f"cannot compose {path}"
                raise ValueError(msg)
            self._bind(stage, path)
            return True

        target = _reference_target(layer, path)
        info = layer.pseudoRoot
        root = _new_root_layer(
            info.GetInfo(UsdGeom.Tokens.upAxis)
            if info.HasInfo(UsdGeom.Tokens.upAxis)
            else UsdGeom.Tokens.y,
            info.GetInfo(UsdGeom.Tokens.metersPerUnit)
            if info.HasInfo(UsdGeom.Tokens.metersPerUnit)
            else 1.0,
        )
        prim_path = ROOT.AppendChild(Tf.MakeValidIdentifier(path.stem))
        spec = Sdf.CreatePrimInLayer(root, prim_path)
        spec.specifier = Sdf.SpecifierOver
        reference = Sdf.Reference(
            str(path), Sdf.Path.emptyPath if layer.defaultPrim else target
        )
        spec.referenceList.Prepend(reference)
        _copy_time_range(layer, root)
        self._source_map = (target, prim_path)
        mapped = [self.map_source_path(p) for p in mask]
        stage = self._open_stage(root, mapped, load_set)
        if stage is None:
            msg = f"cannot compose {path}"
            raise ValueError(msg)
        self._bind(stage, None)
        return False

    def _open_stage(
        self, layer: Sdf.Layer, mask: Sequence[str], load_set: object
    ) -> Usd.Stage | None:
        if not mask:
            return Usd.Stage.Open(layer, load_set)
        population = Usd.StagePopulationMask()
        for path in mask:
            population.Add(Sdf.Path(path))
        return Usd.Stage.OpenMasked(layer, population, load_set)

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
        target = _reference_target(layer, path)
        prim_path = self._unique_child(parent, Tf.MakeValidIdentifier(path.stem))
        prim = self.stage.OverridePrim(prim_path)
        prim.GetReferences().AddReference(
            str(path), Sdf.Path.emptyPath if layer.defaultPrim else target
        )
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
        """Write the root layer and any dirty sublayers it owns.

        Raises:
            ValueError: The document is untitled; use :meth:`save_as`.
        """
        if self._path is None:
            raise ValueError("untitled document; use save_as")
        for layer in self.stage.GetLayerStack(includeSessionLayers=False):
            if layer.dirty and not layer.anonymous:
                layer.Save()
        self._set_edited(False)

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

    # -- layers ---------------------------------------------------------------

    @property
    def edit_target_layer(self) -> Sdf.Layer:
        """The layer that edits go to."""
        return self.stage.GetEditTarget().GetLayer()

    def set_edit_target(self, layer: Sdf.Layer) -> None:
        """Send edits to ``layer``, a layer of the stage's local layer stack.

        Args:
            layer: The session layer, the root layer, or one of its sublayers.

        Raises:
            ValueError: ``layer`` is not in the local layer stack.
        """
        stage = self.stage
        if layer not in stage.GetLayerStack(includeSessionLayers=True):
            msg = f"{layer.identifier} is not in the stage's layer stack"
            raise ValueError(msg)
        stage.SetEditTarget(Usd.EditTarget(layer))
        self.layersChanged.emit()

    def set_layer_muted(self, layer: Sdf.Layer, muted: bool) -> None:
        """Mute or unmute ``layer``; the root and session layers cannot be muted.

        Args:
            layer: Any layer the stage uses.
            muted: The new state.

        Raises:
            ValueError: ``layer`` is the root or session layer.
        """
        stage = self.stage
        if layer in (stage.GetRootLayer(), stage.GetSessionLayer()):
            raise ValueError("the root and session layers cannot be muted")
        if muted:
            stage.MuteLayer(layer.identifier)
        else:
            stage.UnmuteLayer(layer.identifier)
        self.layersChanged.emit()

    # -- session overrides ------------------------------------------------------

    @contextmanager
    def session_edits(self) -> Iterator[Usd.Stage]:
        """Send edits made inside the block to the session layer.

        Yields:
            The stage.
        """
        stage = self.stage
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            yield stage

    def _prims(self, paths: Sequence[str]) -> list[Usd.Prim]:
        stage = self.stage
        prims = [stage.GetPrimAtPath(p) for p in paths]
        return [p for p in prims if p and p.IsValid()]

    def set_visible(self, paths: Sequence[str], visible: bool) -> None:
        """Show or hide prims with a session-layer visibility override.

        Args:
            paths: Prim paths as strings; non-imageable prims are skipped.
            visible: ``True`` makes each prim and its ancestors visible.
        """
        with self.session_edits():
            for prim in self._prims(paths):
                imageable = UsdGeom.Imageable(prim)
                if not imageable:
                    continue
                if visible:
                    imageable.MakeVisible()
                else:
                    imageable.MakeInvisible()

    def show_only(self, paths: Sequence[str]) -> None:
        """Hide every root prim, then make ``paths`` visible (usdview "Vis Only").

        Args:
            paths: Prim paths as strings.
        """
        stage = self.stage
        roots = [
            str(p.GetPath())
            for p in stage.GetPseudoRoot().GetChildren()
            if p.GetPath() != SESSION_ROOT
        ]
        self.set_visible(roots, False)
        self.set_visible(paths, True)

    def set_active(self, paths: Sequence[str], active: bool) -> None:
        """Activate or deactivate prims with a session-layer override.

        Args:
            paths: Prim paths as strings.
            active: The new state.
        """
        with self.session_edits():
            for prim in self._prims(paths):
                prim.SetActive(active)

    def clear_session_overrides(self, paths: Sequence[str]) -> None:
        """Remove session-layer visibility and active opinions at and below paths.

        Args:
            paths: Prim paths as strings.
        """
        session = self.stage.GetSessionLayer()
        specs: list[Sdf.PrimSpec] = []

        def collect(path: Sdf.Path) -> None:
            if path.IsPrimPath():
                spec = session.GetPrimAtPath(path)
                if spec is not None:
                    specs.append(spec)

        for path in paths:
            if session.GetPrimAtPath(path) is not None:
                session.Traverse(Sdf.Path(path), collect)
        with Sdf.ChangeBlock():
            for spec in specs:
                if "visibility" in spec.properties:
                    spec.RemoveProperty(spec.properties["visibility"])
                if spec.HasInfo("active"):
                    spec.ClearInfo("active")

    def load(self, paths: Sequence[str], load: bool = True) -> None:
        """Load or unload payloads at and below ``paths``.

        Args:
            paths: Prim paths as strings.
            load: ``False`` unloads.
        """
        sdf_paths = [Sdf.Path(p) for p in paths]
        if load:
            self.stage.LoadAndUnload(sdf_paths, [])
        else:
            self.stage.LoadAndUnload([], sdf_paths)

    def set_variant(self, path: str, variant_set: str, selection: str) -> None:
        """Select a variant on the prim at ``path`` in the current edit target.

        Args:
            path: Prim path as a string.
            variant_set: Name of a variant set on the prim.
            selection: The variant to select; ``""`` clears the selection.
        """
        stage = self.stage
        prim = stage.GetPrimAtPath(path)
        if not prim:
            return
        target = stage.GetEditTarget()

        prim_path = prim.GetPath()

        def edit() -> None:
            with Usd.EditContext(stage, target):
                current = stage.GetPrimAtPath(prim_path)
                variants = current.GetVariantSets().GetVariantSet(variant_set)
                if selection:
                    variants.SetVariantSelection(selection)
                else:
                    variants.ClearVariantSelection()

        self.undo_stack.push(
            SdfEditCommand(
                f"Select {variant_set} = {selection or 'none'}",
                target.GetLayer(),
                [target.MapToSpecPath(prim.GetPath())],
                edit,
            )
        )

    def set_attribute(self, path: str, value: object) -> bool:
        """Set an attribute in the current edit target, undoably.

        An attribute with time samples gets a sample at the current time;
        any other gets its default value.

        Args:
            path: Attribute path as a string, such as ``/World/Ball.radius``.
            value: A value of the attribute's type.

        Returns:
            Whether the composed value is now ``value``; ``False`` means a
            stronger layer's opinion still wins.

        Raises:
            ValueError: No attribute exists at ``path``.
        """
        stage = self.stage
        attr = stage.GetAttributeAtPath(path)
        if not attr:
            msg = f"no attribute at {path}"
            raise ValueError(msg)
        time = (
            Usd.TimeCode(self._time)
            if attr.GetNumTimeSamples()
            else Usd.TimeCode.Default()
        )
        target = stage.GetEditTarget()

        attr_path = attr.GetPath()

        def edit() -> None:
            with Usd.EditContext(stage, target):
                stage.GetAttributeAtPath(attr_path).Set(value, time)

        self.undo_stack.push(
            SdfEditCommand(
                f"Set {attr.GetName()}",
                target.GetLayer(),
                [target.MapToSpecPath(attr.GetPath())],
                edit,
            )
        )
        return format_value(attr.Get(time)) == format_value(value)

    def set_dome_light(self, enabled: bool) -> None:
        """Add or remove a white dome light in the session layer.

        Args:
            enabled: Whether the light exists.
        """
        stage = self.stage
        exists = bool(stage.GetPrimAtPath(DOME_LIGHT))
        if enabled == exists:
            return
        with self.session_edits():
            if enabled:
                UsdLux.DomeLight.Define(stage, DOME_LIGHT)
            else:
                stage.RemovePrim(SESSION_ROOT)

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

    def _set_edited(self, edited: bool) -> None:
        if edited != self._edited:
            self._edited = edited
            self.dirtyChanged.emit(edited)

    def _bind(self, stage: Usd.Stage, path: Path | None) -> None:
        if self._listener is not None:
            self._listener.Revoke()
        self._stage = stage
        self._path = path
        self.undo_stack.clear()
        self._set_edited(False)
        start, _end = self.time_range()
        self._time = start
        self._listener = Tf.Notice.Register(
            Usd.Notice.ObjectsChanged, self._on_objects_changed, stage
        )
        self.stageReplaced.emit()
        self.timeChanged.emit(self._time)

    def _on_layers(self, notice: Sdf.Notice.LayersDidChange, _sender: object) -> None:
        if self._stage is None or self._edited:
            return
        local = {
            layer.identifier
            for layer in self._stage.GetLayerStack(includeSessionLayers=False)
        }
        if any(layer.identifier in local for layer in notice.GetLayers()):
            self._set_edited(True)

    def _on_dirtiness(self, _notice: object, _sender: object) -> None:
        self.layersChanged.emit()

    def _on_objects_changed(
        self, notice: Usd.Notice.ObjectsChanged, _sender: Usd.Stage
    ) -> None:
        resynced = [str(p) for p in notice.GetResyncedPaths()]
        info = [str(p) for p in notice.GetChangedInfoOnlyPaths()]
        self.changed.emit(resynced, info)
