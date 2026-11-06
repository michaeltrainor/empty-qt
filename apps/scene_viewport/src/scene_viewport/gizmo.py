"""Maya-style transform gizmo wrapping :class:`pygfx.TransformGizmo`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import numpy as np
import pygfx as gfx
import pylinalg as la

from scene_viewport.xform_math import closest_axis_offset, ray_plane_offset

GizmoTool = Literal["translate", "rotate", "scale"]
GizmoSpace = Literal["local", "world"]
InteractionTool = Literal["select", "translate", "rotate", "scale"]

# Width of the invisible grab strip along each axis line. pygfx draws the
# lines 3px wide and never picks them, so only the small tips were grabbable.
_AXIS_HIT_PX = 12.0
_HANDLE_ORDER = 10_000


class MayaTransformGizmo(gfx.TransformGizmo):
    """``TransformGizmo`` limited to one Maya tool and local/world space.

    Host window hotkeys (Maya-aligned):

    * **Q** select (no manipulator)
    * **W** translate
    * **E** rotate
    * **R** scale
    * Toolbar toggles local / world (default **world**)

    The center-sphere mode cycle from stock pygfx is disabled. Unused handles
    are hidden after each visibility update. Defaults to world space. As in
    Maya, the whole axis line is a move (or scale) handle, not only its tip.

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
        self._drag_modifiers: tuple[str, ...] = ()
        # pygfx's constructor already runs visibility updates.
        self._axis_hit_lines: tuple[gfx.Line, ...] = ()
        super().__init__(object=object, screen_size=screen_size)  # pyright: ignore[reportArgumentType]  # pygfx param is unannotated, inferred int
        self.toggle_mode("world")
        self._axis_hit_lines = self._add_axis_hit_lines()
        self._promote_overlay()

    def _add_axis_hit_lines(self) -> tuple[gfx.Line, ...]:
        """Add a wide, invisible, pickable strip over each axis line.

        Each strip is a child of its line, so it follows the line's rotation
        (all three share one +X geometry) and hides along with it.
        """
        hits: list[gfx.Line] = []
        for line in self._line_children:
            hit = gfx.Line(
                line.geometry,
                gfx.LineMaterial(
                    thickness=_AXIS_HIT_PX,
                    color=(0.0, 0.0, 0.0, 0.0),
                    pick_write=True,
                    depth_test=False,
                    depth_write=False,
                ),
            )
            # Draw before the handles so overlapping tips and plane squares
            # win the pick.
            hit.render_order = _HANDLE_ORDER - 1
            line.add(hit)
            hits.append(hit)
        return tuple(hits)

    def _axis_line_for(self, ob: gfx.WorldObject) -> gfx.WorldObject:
        """Map a grab strip to the visible axis line pygfx gave a ``dim``."""
        if ob in self._axis_hit_lines:
            return self._line_children[self._axis_hit_lines.index(ob)]
        return ob

    def _request_draw(self) -> None:
        """Ask the viewport's renderer for a redraw, if one is attached."""
        viewport = self._viewport
        if viewport is None:
            return
        renderer = viewport.renderer
        if isinstance(renderer, gfx.WgpuRenderer):
            renderer.request_draw()

    def _promote_overlay(self) -> None:
        """Draw handles on top of scene meshes (no depth occlusion)."""
        self.render_order = _HANDLE_ORDER
        for child in self.children:
            child.render_order = _HANDLE_ORDER
            material = getattr(child, "material", None)
            if material is None:
                continue
            material.depth_test = False
            material.depth_write = False

    @property
    def drag_modifiers(self) -> tuple[str, ...]:
        """Keyboard modifiers held on the latest drag move."""
        return self._drag_modifiers

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

    def _handle_translate_move(self, event: gfx.PointerEvent) -> None:
        """Move along the grabbed axis or plane without off-axis drift.

        pygfx slides the handle along the axis on screen, then unprojects at
        the start depth. Under perspective that point is off the axis, so an
        X drag also changed Y. Here the pointer's target is cast as a ray and
        met with the axis line (or the handle's plane) in world space.
        """
        ref = self._ref
        camera = self._camera
        controlled = self._object_to_control
        if ref is None or camera is None or controlled is None:
            return
        dims = (ref["dim"],) if isinstance(ref["dim"], int) else tuple(ref["dim"])
        world_to_screen = self._ndc_to_screen @ camera.camera_matrix
        screen_to_world = la.mat_inverse(world_to_screen)
        travel = np.array(
            (event.x - ref["event_pos"][0], event.y - ref["event_pos"][1]),
            dtype=np.float64,
        )
        # Same screen-space measure of travel along the handle as pygfx.
        screen_dirs = np.asarray(ref["screen_directions"], dtype=np.float64)[
            list(dims), :2
        ]
        if len(dims) == 1:
            units = np.array(
                [travel @ screen_dirs[0] / (screen_dirs[0] @ screen_dirs[0])]
            )
        else:
            units = np.linalg.solve(screen_dirs.T, travel)
        start = la.vec_transform(ref["world_pos"], world_to_screen)
        target = start[:2] + screen_dirs.T @ units
        near = la.vec_transform((target[0], target[1], 0.0), screen_to_world)
        far = la.vec_transform((target[0], target[1], 1.0), screen_to_world)
        ray_dir = np.asarray(far, dtype=np.float64) - near
        world_dirs = np.asarray(ref["world_directions"], dtype=np.float64)
        origin = np.asarray(ref["world_pos"], dtype=np.float64)
        if len(dims) == 1:
            offset = closest_axis_offset(origin, world_dirs[dims[0]], near, ray_dir)
        else:
            normal = np.cross(world_dirs[dims[0]], world_dirs[dims[1]])
            offset = ray_plane_offset(origin, normal, near, ray_dir)
        if offset is None:
            # Axis or plane edge-on to the view: keep pygfx's behaviour.
            super()._handle_translate_move(event)
            return
        controlled.world.position = ref["pos"] + offset

    def process_event(self, event: gfx.PointerEvent) -> None:
        """Handle gizmo pointers without the stock center-click mode cycle."""
        if not self._object_to_control:
            return

        if event.type == "pointer_down":
            # Shift and Ctrl still grab (Ctrl toggles snapping); Alt belongs
            # to camera navigation. A press the gizmo ignores starts a box.
            if event.button != 1 or "Alt" in (event.modifiers or ()):
                return
            self._ref = None
            ob = event.target
            if not isinstance(ob, gfx.WorldObject):
                return
            ob = self._axis_line_for(ob)
            if ob not in self.children:
                return
            if ob in self._line_children:
                # Axis line: move along it, or scale along it in scale mode.
                if self._tool not in {"translate", "scale"}:
                    return
                self._handle_start(self._tool, event, ob)
            elif ob == self._center_sphere:
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
            self._request_draw()
            self.set_pointer_capture(event.pointer_id, event.root)
            event.stop_propagation()
            return

        if event.type == "pointer_up":
            if not self._ref:
                return
            # Skip stock toggle_mode on center click — toolbar owns space.
            self._ref = None
            # Keep the last move's modifiers: releasing Ctrl just before the
            # button would otherwise snap or unsnap the final position.
            self._highlight()
            self._request_draw()
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
            self._drag_modifiers = tuple(event.modifiers or ())
            if self._ref["maxdist"] >= 3:
                if self._ref["kind"] == "translate":
                    self._handle_translate_move(event)
                elif self._ref["kind"] == "scale":
                    self._handle_scale_move(event)
                elif self._ref["kind"] == "rotate":
                    self._handle_rotate_move(event)
                if self._on_changed is not None:
                    self._on_changed(False)
            self._request_draw()
            event.stop_propagation()
