"""Command-stack undo/redo for scene viewport edits."""

from __future__ import annotations

from dataclasses import dataclass, field

from examples.scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)


@dataclass
class SceneObjects:
    """A bag of components involved in a create or delete step."""

    transforms: list[TransformComponent] = field(default_factory=list)
    meshes: list[MeshComponent] = field(default_factory=list)
    lights: list[LightComponent] = field(default_factory=list)


@dataclass
class HistoryStep:
    """One undoable gesture.

    ``transforms_before`` / ``transforms_after`` cover TRS, pivot, and parent
    changes for existing nodes. ``added`` / ``removed`` cover create/delete.
    ``renames`` maps old transform UID → new UID (inverted on undo).
    """

    label: str
    transforms_before: dict[str, TransformComponent] = field(default_factory=dict)
    transforms_after: dict[str, TransformComponent] = field(default_factory=dict)
    added: SceneObjects | None = None
    removed: SceneObjects | None = None
    renames: dict[str, str] = field(default_factory=dict)


class SceneHistory:
    """Linear undo/redo stack for scene edits.

    The host applies scene mutations itself, then :meth:`push` with before/after
    snapshots. Undo/redo call host restore callbacks — this class does not
    touch the viewport.
    """

    def __init__(self, *, capacity: int = 100) -> None:
        """Create an empty history.

        Args:
            capacity: Maximum sealed steps retained.
        """
        self._capacity = max(1, capacity)
        self._undo: list[HistoryStep] = []
        self._redo: list[HistoryStep] = []

    @property
    def can_undo(self) -> bool:
        """Whether :meth:`undo` has a step to restore."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """Whether :meth:`redo` has a step to reapply."""
        return bool(self._redo)

    def clear(self) -> None:
        """Drop both stacks."""
        self._undo.clear()
        self._redo.clear()

    def push(self, step: HistoryStep) -> None:
        """Record a completed gesture and clear the redo stack.

        Args:
            step: Before/after snapshots for the gesture.
        """
        self._undo.append(step)
        if len(self._undo) > self._capacity:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> HistoryStep | None:
        """Pop the last gesture for the host to invert.

        Returns:
            The step to invert, or ``None`` when empty.
        """
        if not self._undo:
            return None
        step = self._undo.pop()
        self._redo.append(step)
        return step

    def redo(self) -> HistoryStep | None:
        """Pop the last undone gesture for the host to reapply.

        Returns:
            The step to reapply, or ``None`` when empty.
        """
        if not self._redo:
            return None
        step = self._redo.pop()
        self._undo.append(step)
        return step
