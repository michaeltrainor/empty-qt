"""Maya-style transform gizmo wrapping :class:`pygfx.TransformGizmo`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import pygfx as gfx

GizmoTool = Literal["translate", "rotate", "scale"]
GizmoSpace = Literal["local", "world"]
InteractionTool = Literal["select", "translate", "rotate", "scale"]


class MayaTransformGizmo(gfx.TransformGizmo):
    """``TransformGizmo`` limited to one Maya tool and local/world space.

    Host window hotkeys (Maya-aligned):

    * **Q** select (no manipulator)
    * **W** translate
    * **E** rotate
    * **R** scale
    * Toolbar toggles local / world (default **world**)

    The center-sphere mode cycle from stock pygfx is disabled. Unused handles
    are hidden after each visibility update. Defaults to world space.

    Args:
        object: World object to control, or ``None``.
        screen_size: On-screen size in logical pixels.
        on_changed: ``on_changed(finalized)`` after a drag move or release.
    """

    def __init__(
        self,
        object: gfx.WorldObject | None = None,
        screen_size: float = 100,
        *,
        on_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self._tool: GizmoTool = "translate"
        self._on_changed = on_changed
        super().__init__(object=object, screen_size=screen_size)
        self.toggle_mode("world")
        self._promote_overlay()

    def _promote_overlay(self) -> None:
        """Draw handles on top of scene meshes (no depth occlusion)."""
        self.render_order = 10_000
        for child in self.children:
            child.render_order = 10_000
            material = getattr(child, "material", None)
            if material is None:
                continue
            material.depth_test = False
            material.depth_write = False

    @property
    def tool(self) -> GizmoTool:
        """Active manipulation tool."""
        return self._tool

    def set_tool(self, tool: GizmoTool) -> None:
        """Show only handles for ``tool``.

        Args:
            tool: ``translate``, ``rotate``, or ``scale``.
        """
        if tool not in {"translate", "rotate", "scale"}:
            msg = f"unsupported gizmo tool {tool!r}"
            raise ValueError(msg)
        self._tool = tool
        self._apply_tool_visibility()

    @property
    def space(self) -> GizmoSpace:
        """Local (object) or world reference frame."""
        return "local" if self._mode == "object" else "world"

    def set_space(self, space: GizmoSpace) -> None:
        """Set local or world space (never screen).

        Args:
            space: ``local`` maps to gizmo ``object`` mode; ``world`` to
                ``world``.
        """
        mode = "object" if space == "local" else "world"
        if self._mode != mode:
            self.toggle_mode(mode)
        self._apply_tool_visibility()

    def set_screen_size(self, screen_size: float) -> None:
        """Set on-screen manipulator size in logical pixels.

        Args:
            screen_size: Desired radius in pixels.
        """
        self._screen_size = float(screen_size)

    def toggle_space(self) -> GizmoSpace:
        """Flip between local and world.

        Returns:
            The space after the toggle.
        """
        next_space: GizmoSpace = "world" if self.space == "local" else "local"
        self.set_space(next_space)
        return next_space

    def _update_visibility(self) -> None:
        super()._update_visibility()
        self._apply_tool_visibility()

    def _apply_tool_visibility(self) -> None:
        tool = self._tool
        show_translate = tool == "translate"
        show_rotate = tool == "rotate"
        show_scale = tool == "scale"

        for child in self._translate_children:
            child.visible = bool(child.visible and show_translate)
        for child in self._line_children:
            child.visible = show_translate or show_scale
        for child in self._scale_children:
            child.visible = bool(child.visible and show_scale)
        for child in self._rotate_children:
            child.visible = show_rotate
        for child in self._arc_children:
            child.visible = show_rotate
        self._center_sphere.visible = show_scale

    def process_event(self, event: gfx.PointerEvent) -> None:
        """Handle gizmo pointers without the stock center-click mode cycle."""
        if not self._object_to_control:
            return

        if event.type == "pointer_down":
            if event.button != 1 or event.modifiers:
                return
            self._ref = None
            ob = event.target
            if ob not in self.children:
                return
            if ob == self._center_sphere:
                if self._tool != "scale":
                    return
                self._handle_start("scale", event, ob)
            elif ob in self._translate_children:
                if self._tool != "translate":
                    return
                self._handle_start("translate", event, ob)
            elif ob in self._scale_children:
                if self._tool != "scale":
                    return
                self._handle_start("scale", event, ob)
            elif ob in self._rotate_children:
                if self._tool != "rotate":
                    return
                self._handle_start("rotate", event, ob)
            else:
                return
            self._highlight(ob)
            self._viewport.renderer.request_draw()
            self.set_pointer_capture(event.pointer_id, event.root)
            event.stop_propagation()
            return

        if event.type == "pointer_up":
            if not self._ref:
                return
            # Skip stock toggle_mode on center click — toolbar owns space.
            self._ref = None
            self._highlight()
            self._viewport.renderer.request_draw()
            if self._on_changed is not None:
                self._on_changed(True)
            event.stop_propagation()
            return

        if event.type == "pointer_move":
            if not self._ref:
                return
            dist = (
                (event.x - self._ref["event_pos"][0]) ** 2
                + (event.y - self._ref["event_pos"][1]) ** 2
            ) ** 0.5
            self._ref["maxdist"] = max(self._ref["maxdist"], dist)
            if self._ref["maxdist"] >= 3:
                if self._ref["kind"] == "translate":
                    self._handle_translate_move(event)
                elif self._ref["kind"] == "scale":
                    self._handle_scale_move(event)
                elif self._ref["kind"] == "rotate":
                    self._handle_rotate_move(event)
                if self._on_changed is not None:
                    self._on_changed(False)
            self._viewport.renderer.request_draw()
            event.stop_propagation()
