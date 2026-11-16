"""Undo for Sdf edits: snapshot the specs a command touches, restore on undo.

USD has no undo. A :class:`SdfEditCommand` names the specs its edit will
touch, snapshots them on one layer before the first run, and on undo puts
them back inside one ``Sdf.ChangeBlock``: a property spec is copied back
whole, a prim spec gets its metadata fields back, and specs the edit
created (including ``over`` ancestors) are removed. Redo runs the edit
again, which yields the same result because undo restored the exact
starting state.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pxr import Sdf
from PySide6.QtGui import QUndoCommand

_INTEGERS = {"int", "unsigned int", "int64_t", "uint64_t", "unsigned char"}
_FLOATS = {"float", "double", "GfHalf", "SdfTimeCode"}
_STRINGS = {"std::string", "TfToken"}


def parse_value(type_name: Sdf.ValueTypeName, text: str) -> Any:
    """Parse inspector text into a value of an attribute's type.

    Accepts what :func:`stage_editor.inspection.format_value` prints for
    scalars, strings, tokens, asset paths, and vectors, colours, and
    quaternions written as ``(1, 2, 3)``.

    Args:
        type_name: The attribute's ``Sdf.ValueTypeName``.
        text: The edited text.

    Raises:
        ValueError: The text does not parse as that type, or the type is
            an array or matrix, which the inspector does not edit.
    """
    if type_name.isArray:
        msg = f"{type_name} arrays are not editable as text"
        raise ValueError(msg)
    cpp = type_name.cppTypeName
    stripped = text.strip()
    if cpp == "bool":
        lowered = stripped.lower()
        if lowered in ("true", "1", "on", "yes"):
            return True
        if lowered in ("false", "0", "off", "no"):
            return False
        msg = f"not a bool: {text!r}"
        raise ValueError(msg)
    if cpp in _INTEGERS:
        return int(stripped)
    if cpp in _FLOATS:
        return float(stripped)
    if cpp in _STRINGS:
        return text
    if cpp == "SdfAssetPath":
        return Sdf.AssetPath(stripped.strip("@"))
    python_class = type_name.type.pythonClass
    if python_class is None or python_class.__name__.startswith("Matrix"):
        msg = f"{type_name} is not editable as text"
        raise ValueError(msg)
    try:
        parsed = ast.literal_eval(stripped)
    except (SyntaxError, ValueError) as exc:
        msg = f"not a {type_name}: {text!r}"
        raise ValueError(msg) from exc
    numbers = parsed if isinstance(parsed, tuple | list) else (parsed,)
    try:
        return python_class(*(float(n) for n in numbers))
    except TypeError as exc:  # Boost.Python.ArgumentError is a TypeError
        msg = f"not a {type_name}: {text!r}"
        raise ValueError(msg) from exc


def _highest_missing(layer: Sdf.Layer, path: Sdf.Path) -> Sdf.Path | None:
    """Return the highest prim path at or above ``path`` with no spec in ``layer``."""
    missing = None
    probe = path.GetPrimPath()
    while probe != Sdf.Path.absoluteRootPath and not probe.isEmpty:
        if layer.GetPrimAtPath(probe) is None:
            missing = probe
        probe = probe.GetParentPath()
    return missing


def _remove_prim_spec(layer: Sdf.Layer, path: Sdf.Path) -> None:
    parent_path = path.GetParentPath()
    parent = (
        layer.pseudoRoot
        if parent_path == Sdf.Path.absoluteRootPath
        else layer.GetPrimAtPath(parent_path)
    )
    if parent is not None and path.name in parent.nameChildren:
        del parent.nameChildren[path.name]


@dataclass
class _Snapshot:
    """The state of one spec on one layer before an edit."""

    layer: Sdf.Layer
    path: Sdf.Path
    missing_root: Sdf.Path | None = None
    existed: bool = False
    scratch: Sdf.Layer | None = None
    info: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def capture(cls, layer: Sdf.Layer, path: Sdf.Path) -> _Snapshot:
        snapshot = cls(layer, path, _highest_missing(layer, path))
        if path.IsPropertyPath():
            spec = layer.GetPropertyAtPath(path)
            snapshot.existed = spec is not None
            if spec is not None:
                scratch = Sdf.Layer.CreateAnonymous(".usda")
                Sdf.CreatePrimInLayer(scratch, path.GetPrimPath())
                Sdf.CopySpec(layer, path, scratch, path)
                snapshot.scratch = scratch
        else:
            spec = layer.GetPrimAtPath(path)
            snapshot.existed = spec is not None
            if spec is not None:
                snapshot.info = {key: spec.GetInfo(key) for key in spec.ListInfoKeys()}
        return snapshot

    def restore(self) -> None:
        layer, path = self.layer, self.path
        if self.missing_root is not None:
            # The edit created this spec, and maybe over ancestors: drop them.
            _remove_prim_spec(layer, self.missing_root)
            return
        if path.IsPropertyPath():
            owner = layer.GetPrimAtPath(path.GetPrimPath())
            current = layer.GetPropertyAtPath(path)
            if current is not None and owner is not None:
                owner.RemoveProperty(current)
            if self.existed and self.scratch is not None:
                Sdf.CopySpec(self.scratch, path, layer, path)
            return
        spec = layer.GetPrimAtPath(path)
        if spec is None:
            return
        for key in spec.ListInfoKeys():
            if key not in self.info:
                spec.ClearInfo(key)
        for key, value in self.info.items():
            if spec.GetInfo(key) != value:
                spec.SetInfo(key, value)


class SdfEditCommand(QUndoCommand):
    """An undoable edit of named specs on one layer.

    Args:
        text: Label for the Edit menu, such as ``Set radius``.
        layer: The layer the edit writes, normally the edit target's.
        paths: Prim or property paths the edit touches on that layer.
        edit: Performs the edit; called on the first redo and every redo.
    """

    def __init__(
        self,
        text: str,
        layer: Sdf.Layer,
        paths: Sequence[Sdf.Path],
        edit: Callable[[], None],
    ) -> None:
        super().__init__(text)
        self._layer = layer
        self._paths = [Sdf.Path(p) for p in paths]
        self._edit = edit
        self._snapshots: list[_Snapshot] | None = None

    def capture(self) -> None:
        """Snapshot the specs now, for an edit already being previewed live.

        Call this before the first live change (a gizmo drag) and push the
        command when the drag ends; the edit then only sets final values.
        """
        if self._snapshots is None:
            self._snapshots = [_Snapshot.capture(self._layer, p) for p in self._paths]

    def redo(self) -> None:
        if self._snapshots is None:
            self._snapshots = [_Snapshot.capture(self._layer, p) for p in self._paths]
        self._edit()

    def undo(self) -> None:
        with Sdf.ChangeBlock():
            for snapshot in reversed(self._snapshots or []):
                snapshot.restore()


class LayerSnapshotCommand(QUndoCommand):
    """An undoable edit that may touch anything on a few whole layers.

    Structural edits (delete, rename, reparent, eject) move specs around
    and fix up paths across layers, so naming the touched specs up front is
    not practical. This command copies each layer's whole content before
    and after the first run and transfers the copies back on undo and redo,
    so redo never depends on the edit being repeatable (an eject writes its
    file only once).

    Args:
        text: Label for the Edit menu.
        layers: Every layer the edit may change.
        edit: Performs the edit. If it raises, the layers are restored, the
            command is marked obsolete, and the exception propagates.
    """

    def __init__(
        self, text: str, layers: Sequence[Sdf.Layer], edit: Callable[[], None]
    ) -> None:
        super().__init__(text)
        self._layers = list(dict.fromkeys(layers))
        self._edit = edit
        self._before: list[Sdf.Layer] | None = None
        self._after: list[Sdf.Layer] | None = None

    def _copies(self) -> list[Sdf.Layer]:
        copies = []
        for layer in self._layers:
            copy = Sdf.Layer.CreateAnonymous(".usda")
            copy.TransferContent(layer)
            copies.append(copy)
        return copies

    def _restore(self, copies: list[Sdf.Layer]) -> None:
        with Sdf.ChangeBlock():
            for layer, copy in zip(self._layers, copies, strict=True):
                layer.TransferContent(copy)

    def redo(self) -> None:
        if self._after is not None:
            self._restore(self._after)
            return
        self._before = self._copies()
        try:
            self._edit()
        except Exception:
            self._restore(self._before)
            self.setObsolete(True)
            raise
        self._after = self._copies()

    def undo(self) -> None:
        if self._before is not None:
            self._restore(self._before)
