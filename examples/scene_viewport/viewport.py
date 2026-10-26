"""pygfx renderer wiring for transform, mesh, and light components."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
import pygfx as gfx
import pylinalg as la
from PySide6.QtWidgets import QWidget  # noqa: F401  # select Qt before rendercanvas
from rendercanvas.qt import QRenderWidget

from examples.scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from examples.scene_viewport.env import average_horizon_color, make_studio_cubemap
from examples.scene_viewport.gizmo import (
    GizmoSpace,
    GizmoTool,
    InteractionTool,
    MayaTransformGizmo,
)
from examples.scene_viewport.mesh.geometry import geometry_for
from examples.scene_viewport.settings import AppSettings
from examples.scene_viewport.textures import load_albedo_texture
from examples.scene_viewport.xform_math import (
    as_vec3,
    average_positions,
    local_pivot_from_world_point,
    pivot_world_matrix,
    translate_mat,
)

_OUTLINE_COLOR = "#5eb0ff"
_DRAW_WIRE_COLOR = "#1c1c1c"
_BBOX_COLOR = "#c8c8c8"
_BBOX_SELECTED_COLOR = "#5eb0ff"
_ADDITIVE_MODIFIERS = frozenset({"Shift", "Control", "Meta"})

Vec3 = tuple[float, float, float]
DrawMode = Literal[
    "bounding_box",
    "wire_shaded",
    "flat",
    "wireframe",
    "shaded",
    "textured",
]
_LIT_DRAW_MODES = frozenset({"shaded", "textured", "wire_shaded"})
DRAW_MODE_HOTKEYS: tuple[tuple[str, DrawMode, str], ...] = (
    ("1", "bounding_box", "Bounding Box"),
    ("2", "wire_shaded", "Wireframe on Shaded"),
    ("3", "flat", "Flat"),
    ("4", "wireframe", "Wireframe"),
    ("5", "shaded", "Shaded"),
    ("6", "textured", "Textured"),
)


class ViewportController:
    """Lit pygfx scene with hierarchical transforms, meshes, and lights.

    Each :class:`TransformComponent` becomes a ``gfx.Group``. Meshes and
    lights parent under that group. Selection is always a transform UID;
    picking a mesh or light helper selects its transform.

    The manipulator attaches to a hidden proxy at the selection pivot
    (average for multi-select). Drag deltas rewrite transform world
    matrices (or local pivots in affect-pivot mode).
    """

    def __init__(
        self,
        canvas: QRenderWidget,
        transforms: list[TransformComponent],
        meshes: list[MeshComponent],
        lights: list[LightComponent],
        *,
        on_selection_changed: Callable[[list[str]], None] | None = None,
        on_transform_manipulated: Callable[[list[str], bool], None] | None = None,
    ) -> None:
        self._canvas = canvas
        self._on_selection_changed = on_selection_changed
        self._on_transform_manipulated = on_transform_manipulated
        self._selected: set[str] = set()
        self._selected_order: list[str] = []
        self._groups: dict[str, gfx.Group] = {}
        self._pivots: dict[str, Vec3] = {}
        self._meshes: dict[str, gfx.Mesh] = {}
        self._mesh_components: dict[str, MeshComponent] = {}
        self._outlines: dict[str, gfx.Mesh] = {}
        self._draw_wires: dict[str, gfx.Mesh] = {}
        self._bboxes: dict[str, gfx.Line] = {}
        self._lights: dict[str, gfx.Light] = {}
        self._light_helpers: dict[str, gfx.WorldObject] = {}
        self._mesh_to_transform: dict[str, str] = {}
        self._light_to_transform: dict[str, str] = {}
        self._helper_to_transform: dict[str, str] = {}
        self._transform_to_meshes: dict[str, list[str]] = {}
        self._transform_to_lights: dict[str, list[str]] = {}
        self._updating = False
        self._interaction_tool: InteractionTool = "select"
        self._draw_mode: DrawMode = "shaded"
        self._affect_pivot = False
        self._drag_active = False
        self._drag_rels: dict[str, np.ndarray] = {}
        self._settings = AppSettings()
        self._background: gfx.Background | None = None
        self._ambient: gfx.AmbientLight | None = None
        self._grid: gfx.GridHelper | None = None
        self._env_map: gfx.Texture | None = None
        self._albedo_cache: dict[str, gfx.Texture | None] = {}
        self._light_aim: gfx.WorldObject | None = None

        self.renderer = gfx.renderers.WgpuRenderer(
            canvas,
            gamma_correction=self._settings.gamma,
        )
        self.scene = gfx.Scene()
        self._rebuild_environment()
        # Non-selectable fill; scene lights are LightComponents.
        self._ambient = gfx.AmbientLight(
            "#ffffff",
            intensity=self._effective_ambient(),
        )
        self.scene.add(self._ambient)
        # Shared aim point for directional lights (shadow camera look-at).
        self._light_aim = gfx.WorldObject()
        self._light_aim.name = "lightAim"
        self._light_aim.local.position = (0.0, 0.0, 0.0)
        self.scene.add(self._light_aim)
        # Viewport chrome (not in the outliner / component store).
        self._install_viewport_grid()

        self._manip_proxy = gfx.Group()
        self._manip_proxy.name = "manipProxy"
        self.scene.add(self._manip_proxy)

        for transform in transforms:
            group = gfx.Group()
            group.name = transform.name
            self._groups[transform.name] = group
            self._pivots[transform.name] = transform.pivot
            self._transform_to_meshes[transform.name] = []
            self._transform_to_lights[transform.name] = []

        for transform in transforms:
            group = self._groups[transform.name]
            parent_name = transform.parent
            if parent_name:
                parent = self._groups.get(parent_name)
                if parent is None:
                    msg = f"transform {transform.name!r} missing parent {parent_name!r}"
                    raise KeyError(msg)
                parent.add(group)
            else:
                self.scene.add(group)
            self._apply_local(group, transform)

        for component in meshes:
            self._add_mesh(component)

        for component in lights:
            self._add_light(component)

        self.camera = gfx.PerspectiveCamera(50, 16 / 9)
        self.camera.local.position = (5.5, 4.0, 6.5)
        self.camera.depth_range = (0.05, 500.0)
        self.camera.show_pos((0.0, 0.4, 0.0))
        gfx.OrbitController(self.camera, register_events=self.renderer)

        self._gizmo = MayaTransformGizmo(
            screen_size=self._settings.gizmo_size,
            on_changed=self._on_gizmo_changed,
        )
        self._gizmo.name = "mayaGizmo"
        self.scene.add(self._gizmo)
        self._gizmo.add_default_event_handlers(self.renderer, self.camera)

        self.renderer.add_event_handler(self._on_renderer_click, "click")
        canvas.request_draw(self._draw)

    def apply_settings(self, settings: AppSettings) -> None:
        """Push application settings onto viewport chrome and lighting.

        Args:
            settings: Preferences to apply (grid, ambient, shadows, gizmo).
        """
        self._settings = settings.model_copy(deep=True)
        self.renderer.gamma_correction = settings.gamma
        self._rebuild_environment()
        if self._ambient is not None:
            self._ambient.intensity = self._effective_ambient()
        self._gizmo.set_screen_size(settings.gizmo_size)
        self._rebuild_viewport_grid()
        self._apply_env_to_meshes()
        self._refresh_outline_thickness()
        for light in self._lights.values():
            kind = "point"
            if isinstance(light, gfx.DirectionalLight):
                kind = "directional"
            elif isinstance(light, gfx.SpotLight):
                kind = "spot"
            if light.cast_shadow:
                self._configure_light_shadow(light, kind)
        self._canvas.request_draw()

    def _effective_ambient(self) -> float:
        return self._settings.ambient_intensity * self._settings.exposure

    def _effective_env_intensity(self) -> float:
        return self._settings.env_map_intensity * self._settings.exposure

    def _rebuild_environment(self) -> None:
        """Recreate background and shared IBL cubemap from settings."""
        settings = self._settings
        horizon = average_horizon_color(
            settings.background_top,
            settings.background_bottom,
        )
        self._env_map = make_studio_cubemap(
            sky=settings.background_top,
            ground=settings.background_bottom,
            horizon=horizon,
        )
        if self._background is not None and self._background.parent is not None:
            self._background.parent.remove(self._background)
        if settings.background_mode == "skybox":
            self._background = gfx.Background(
                None,
                gfx.BackgroundSkyboxMaterial(map=self._env_map),
            )
        else:
            self._background = gfx.Background.from_color(
                settings.background_bottom,
                settings.background_top,
            )
        self._background.name = "viewportBackground"
        # Keep background behind chrome / scene content.
        self.scene.add(self._background)

    def _apply_env_to_meshes(self) -> None:
        intensity = self._effective_env_intensity()
        for mesh in self._meshes.values():
            material = mesh.material
            if isinstance(material, gfx.MeshStandardMaterial):
                material.env_map = self._env_map
                material.env_map_intensity = intensity

    def _refresh_outline_thickness(self) -> None:
        thickness = self._settings.outline_thickness
        for outline in self._outlines.values():
            outline.material.wireframe_thickness = thickness

    def _albedo_texture(self, component: MeshComponent) -> gfx.Texture | None:
        """Return a cached albedo texture for ``component``, if any."""
        key = str(component.albedo_map).strip()
        if not key:
            return None
        if key not in self._albedo_cache:
            self._albedo_cache[key] = load_albedo_texture(component.albedo_map)
        return self._albedo_cache[key]

    def _mesh_material(
        self,
        component: MeshComponent,
        *,
        textured: bool = False,
    ) -> gfx.MeshStandardMaterial:
        material = gfx.MeshStandardMaterial(
            color=component.color,
            roughness=component.roughness,
            metalness=component.metalness,
            env_map=self._env_map,
            env_map_intensity=self._effective_env_intensity(),
            pick_write=True,
        )
        if textured:
            texture = self._albedo_texture(component)
            if texture is not None:
                material.map = gfx.TextureMap(texture)
        return material

    @property
    def draw_mode(self) -> DrawMode:
        """Active viewport shading / draw mode."""
        return self._draw_mode

    def set_draw_mode(self, mode: DrawMode) -> None:
        """Switch Maya-style viewport draw mode (hotkeys 1–6).

        Args:
            mode: ``bounding_box``, ``wire_shaded``, ``flat``, ``wireframe``,
                ``shaded``, or ``textured`` (textured matches shaded until maps).
        """
        valid = {item[1] for item in DRAW_MODE_HOTKEYS}
        if mode not in valid:
            msg = f"unsupported draw mode {mode!r}"
            raise ValueError(msg)
        if mode == self._draw_mode:
            return
        self._draw_mode = mode
        for name in self._meshes:
            self._apply_draw_mode_to_mesh(name)
        self._apply_highlights()
        self._canvas.request_draw()

    def _basic_mesh_material(
        self,
        component: MeshComponent,
        *,
        wireframe: bool = False,
    ) -> gfx.MeshBasicMaterial:
        return gfx.MeshBasicMaterial(
            color=component.color,
            wireframe=wireframe,
            wireframe_thickness=self._settings.outline_thickness,
            pick_write=True,
        )

    def _apply_draw_mode_to_mesh(self, name: str) -> None:
        """Rebuild materials / visibility for one mesh under the active mode."""
        mesh = self._meshes.get(name)
        component = self._mesh_components.get(name)
        if mesh is None or component is None:
            return
        mode = self._draw_mode
        bbox = self._bboxes.get(name)
        draw_wire = self._draw_wires.get(name)
        lit = mode in _LIT_DRAW_MODES

        mesh.visible = mode != "bounding_box"
        if bbox is not None:
            bbox.visible = mode == "bounding_box"
        if draw_wire is not None:
            draw_wire.visible = mode == "wire_shaded"

        if mode == "bounding_box":
            mesh.cast_shadow = False
            mesh.receive_shadow = False
            return

        if mode == "wireframe":
            mesh.material = self._basic_mesh_material(component, wireframe=True)
        elif mode == "flat":
            mesh.material = self._basic_mesh_material(component)
        else:
            # shaded, textured, wire_shaded
            mesh.material = self._mesh_material(
                component,
                textured=(mode == "textured"),
            )

        mesh.cast_shadow = bool(component.cast_shadow and lit)
        mesh.receive_shadow = bool(component.receive_shadow and lit)

    @staticmethod
    def _aabb_line_geometry(mesh: gfx.Mesh) -> gfx.Geometry:
        """Build 12 line segments for the mesh geometry AABB."""
        bounds = mesh.get_geometry_bounding_box()
        mn = np.asarray(bounds[0], dtype=np.float32)
        mx = np.asarray(bounds[1], dtype=np.float32)
        corners = np.array(
            [
                [mn[0], mn[1], mn[2]],
                [mx[0], mn[1], mn[2]],
                [mx[0], mx[1], mn[2]],
                [mn[0], mx[1], mn[2]],
                [mn[0], mn[1], mx[2]],
                [mx[0], mn[1], mx[2]],
                [mx[0], mx[1], mx[2]],
                [mn[0], mx[1], mx[2]],
            ],
            dtype=np.float32,
        )
        edges = (
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 0),
            (4, 5),
            (5, 6),
            (6, 7),
            (7, 4),
            (0, 4),
            (1, 5),
            (2, 6),
            (3, 7),
        )
        positions = np.empty((len(edges) * 2, 3), dtype=np.float32)
        for index, (start, end) in enumerate(edges):
            positions[index * 2] = corners[start]
            positions[index * 2 + 1] = corners[end]
        return gfx.Geometry(positions=positions)

    @property
    def selected_names(self) -> list[str]:
        """Selected transform UIDs in selection order (last is primary)."""
        return list(self._selected_order)

    @property
    def affect_pivot(self) -> bool:
        """Whether the gizmo edits pivot offsets instead of object TRS."""
        return self._affect_pivot

    def set_affect_pivot(self, enabled: bool) -> None:
        """Toggle Maya-style insert / affect-pivot mode.

        Args:
            enabled: When true, gizmo translation updates local pivots only
                (single selection). Multi-select hides the gizmo.
        """
        self._affect_pivot = bool(enabled)
        self._end_drag()
        self._sync_gizmo_target()
        self._canvas.request_draw()

    def set_selected_names(self, names: Sequence[str]) -> None:
        """Replace the viewport selection and refresh highlights.

        Args:
            names: Transform UIDs (mesh/light names map to their transform).
        """
        next_order: list[str] = []
        seen: set[str] = set()
        for name in names:
            resolved = self._resolve_transform(name)
            if resolved is None or resolved in seen:
                continue
            seen.add(resolved)
            next_order.append(resolved)
        if seen == self._selected and next_order == self._selected_order:
            return
        self._end_drag()
        self._selected = seen
        self._selected_order = next_order
        self._apply_highlights()
        self._sync_gizmo_target()
        self._canvas.request_draw()

    @property
    def interaction_tool(self) -> InteractionTool:
        """Active tool: select, translate, rotate, or scale."""
        return self._interaction_tool

    @property
    def gizmo_tool(self) -> GizmoTool:
        """Active manipulator tool (ignored while select is active)."""
        return self._gizmo.tool

    def set_interaction_tool(self, tool: InteractionTool) -> None:
        """Set the Maya-style interaction tool.

        Args:
            tool: ``select`` hides the gizmo; move/rotate/scale show it.
        """
        if tool not in {"select", "translate", "rotate", "scale"}:
            msg = f"unsupported interaction tool {tool!r}"
            raise ValueError(msg)
        self._interaction_tool = tool
        if tool != "select":
            self._gizmo.set_tool(tool)
        self._sync_gizmo_target()
        self._canvas.request_draw()

    def set_gizmo_tool(self, tool: GizmoTool) -> None:
        """Set translate / rotate / scale and leave select mode.

        Args:
            tool: Active manipulation tool.
        """
        self.set_interaction_tool(tool)

    @property
    def gizmo_space(self) -> GizmoSpace:
        """Gizmo local/world space."""
        return self._gizmo.space

    def set_gizmo_space(self, space: GizmoSpace) -> None:
        """Set gizmo local or world space.

        Args:
            space: ``local`` or ``world``.
        """
        self._gizmo.set_space(space)
        if not self._drag_active:
            self._sync_gizmo_target()
        self._canvas.request_draw()

    def toggle_gizmo_space(self) -> GizmoSpace:
        """Flip gizmo local/world space.

        Returns:
            The space after the toggle.
        """
        space = self._gizmo.toggle_space()
        if not self._drag_active:
            self._sync_gizmo_target()
        self._canvas.request_draw()
        return space

    def read_local_trs(
        self,
        name: str,
    ) -> tuple[Vec3, Vec3, Vec3]:
        """Return local position, euler XYZ, and scale for ``name``."""
        group = self._groups[name]
        return (
            as_vec3(group.local.position),
            as_vec3(group.local.euler),
            as_vec3(group.local.scale),
        )

    def read_world_trs(
        self,
        name: str,
    ) -> tuple[Vec3, Vec3, Vec3]:
        """Return world position, euler XYZ, and scale for ``name``."""
        group = self._groups[name]
        return (
            as_vec3(group.world.position),
            as_vec3(group.world.euler),
            as_vec3(group.world.scale),
        )

    def read_pivot(self, name: str) -> Vec3:
        """Return the local pivot offset for ``name``."""
        return self._pivots.get(name, (0.0, 0.0, 0.0))

    def set_pivot(self, name: str, pivot: Vec3) -> None:
        """Set the local pivot offset for ``name``.

        Args:
            name: Transform UID.
            pivot: Local-space offset from the transform origin.
        """
        if name not in self._groups:
            return
        self._pivots[name] = (
            float(pivot[0]),
            float(pivot[1]),
            float(pivot[2]),
        )
        if not self._drag_active:
            self._sync_gizmo_target()
        self._canvas.request_draw()

    def apply_transform_local(self, component: TransformComponent) -> None:
        """Push **local** TRS, pivot, and visibility from ``component``."""
        group = self._groups.get(component.name)
        if group is None:
            return
        self._apply_local(group, component)
        self._pivots[component.name] = component.pivot
        group.visible = component.visible
        if not self._drag_active:
            self.refresh_shadows_for_transforms([component.name])
            self._sync_gizmo_target()
        self._canvas.request_draw()

    def set_visible(self, name: str, visible: bool) -> None:
        """Show or hide a transform group in the viewport.

        Args:
            name: Transform UID.
            visible: When false, the group and its shapes are hidden.
        """
        group = self._groups.get(name)
        if group is None:
            return
        group.visible = bool(visible)
        self._canvas.request_draw()

    def rename_transform(self, old_name: str, new_name: str) -> None:
        """Rename a transform group and remap internal indexes.

        Args:
            old_name: Existing transform UID.
            new_name: Unique new UID.

        Raises:
            KeyError: When ``old_name`` is missing or ``new_name`` exists.
        """
        if old_name not in self._groups:
            msg = f"transform not found: {old_name!r}"
            raise KeyError(msg)
        if new_name in self._groups:
            msg = f"transform already exists: {new_name!r}"
            raise KeyError(msg)
        group = self._groups.pop(old_name)
        group.name = new_name
        self._groups[new_name] = group
        self._pivots[new_name] = self._pivots.pop(old_name, (0.0, 0.0, 0.0))
        self._transform_to_meshes[new_name] = self._transform_to_meshes.pop(
            old_name, []
        )
        self._transform_to_lights[new_name] = self._transform_to_lights.pop(
            old_name, []
        )
        for mesh_name, transform in list(self._mesh_to_transform.items()):
            if transform == old_name:
                self._mesh_to_transform[mesh_name] = new_name
        for light_name, transform in list(self._light_to_transform.items()):
            if transform == old_name:
                self._light_to_transform[light_name] = new_name
        for helper_name, transform in list(self._helper_to_transform.items()):
            if transform == old_name:
                self._helper_to_transform[helper_name] = new_name
        if old_name in self._selected:
            self._selected.discard(old_name)
            self._selected.add(new_name)
            self._selected_order = [
                new_name if item == old_name else item for item in self._selected_order
            ]
        if not self._drag_active:
            self._sync_gizmo_target()
        self._canvas.request_draw()

    def frame_selection(self, names: Sequence[str] | None = None) -> None:
        """Frame the camera on ``names``, or the whole scene when empty.

        Args:
            names: Transform UIDs to frame. ``None`` uses the current
                selection; an empty selection frames every transform.
        """
        targets = list(names) if names is not None else list(self._selected_order)
        if not targets:
            targets = list(self._groups)
        boxes: list[np.ndarray] = []
        for name in targets:
            group = self._groups.get(name)
            if group is None:
                continue
            box = group.get_world_bounding_box()
            if box is not None:
                boxes.append(np.asarray(box, dtype=np.float64))
                continue
            pos = as_vec3(group.world.position)
            boxes.append(
                np.array(
                    [
                        [pos[0] - 0.5, pos[1] - 0.5, pos[2] - 0.5],
                        [pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5],
                    ],
                    dtype=np.float64,
                )
            )
        if not boxes:
            return
        mins = np.min([box[0] for box in boxes], axis=0)
        maxs = np.max([box[1] for box in boxes], axis=0)
        center = (mins + maxs) * 0.5
        extent = np.maximum(maxs - mins, 0.25)
        size = float(np.linalg.norm(extent))
        proxy = gfx.Mesh(
            gfx.box_geometry(
                float(extent[0]),
                float(extent[1]),
                float(extent[2]),
            ),
            gfx.MeshBasicMaterial(opacity=0.0),
        )
        proxy.local.position = (float(center[0]), float(center[1]), float(center[2]))
        self.camera.show_object(proxy, scale=1.35)
        near = max(0.05, size * 0.02)
        far = max(near * 200.0, size * 40.0, 50.0)
        self.camera.depth_range = (near, min(far, 5000.0))
        self._canvas.request_draw()

    def apply_transform_world(
        self,
        name: str,
        position: Vec3,
        rotation: Vec3,
        scale: Vec3,
    ) -> TransformComponent | None:
        """Set **world** TRS on a group, then return updated local values.

        Args:
            name: Transform UID.
            position: World-space translation.
            rotation: World-space euler XYZ radians.
            scale: World-space scale.

        Returns:
            A partial component update with local TRS, or ``None`` if missing.
        """
        group = self._groups.get(name)
        if group is None:
            return None
        group.world.scale = scale
        group.world.rotation = la.quat_from_euler(rotation, order="xyz")
        group.world.position = position
        local_position, local_rotation, local_scale = self.read_local_trs(name)
        if not self._drag_active:
            self.refresh_shadows_for_transforms([name])
            self._sync_gizmo_target()
        self._canvas.request_draw()
        return TransformComponent(
            name=name,
            position=local_position,
            rotation=local_rotation,
            scale=local_scale,
            pivot=self.read_pivot(name),
        )

    def set_parent(self, name: str, new_parent: str) -> bool:
        """Change hierarchy only (local TRS kept as-is — may jump in world).

        Args:
            name: Transform UID to move.
            new_parent: Destination parent UID, or ``\"\"`` for scene root.

        Returns:
            ``True`` when the parent link changed.
        """
        group = self._groups.get(name)
        if group is None:
            return False
        if new_parent == name:
            return False
        if new_parent and new_parent not in self._groups:
            return False
        if new_parent and self._is_descendant(name, new_parent):
            return False

        current_parent = group.parent
        if current_parent is not None:
            current_parent.remove(group)
        if new_parent:
            self._groups[new_parent].add(group)
        else:
            self.scene.add(group)
        if not self._drag_active:
            self._sync_gizmo_target()
        self._canvas.request_draw()
        return True

    def reparent_transform(
        self,
        name: str,
        new_parent: str,
    ) -> tuple[Vec3, Vec3, Vec3] | None:
        """Reparent ``name`` under ``new_parent`` while preserving world pose.

        Args:
            name: Transform UID to move.
            new_parent: Destination parent UID, or ``\"\"`` for scene root.

        Returns:
            Updated local TRS, or ``None`` when the request is invalid.
        """
        group = self._groups.get(name)
        if group is None:
            return None
        if new_parent == name:
            return None
        if new_parent and new_parent not in self._groups:
            return None
        if new_parent and self._is_descendant(name, new_parent):
            return None

        world = np.asarray(group.world.matrix, dtype=np.float64).copy()
        if not self.set_parent(name, new_parent):
            return None
        group.world.matrix = world
        if not self._drag_active:
            self._sync_gizmo_target()
        self._canvas.request_draw()
        return self.read_local_trs(name)

    def apply_mesh(self, component: MeshComponent) -> None:
        """Push material and shadow flags from ``component`` onto its mesh."""
        if component.name not in self._meshes:
            return
        # Drop cached albedo so path edits / file replacements reload.
        key = str(component.albedo_map).strip()
        if key:
            self._albedo_cache.pop(key, None)
        self._mesh_components[component.name] = component.model_copy(deep=True)
        self._apply_draw_mode_to_mesh(component.name)
        self._apply_highlights()
        self._canvas.request_draw()

    def add_transform(self, component: TransformComponent) -> None:
        """Create a ``gfx.Group`` for ``component`` and parent it in the scene.

        Args:
            component: Transform to add. ``name`` must be unique.
        """
        if component.name in self._groups:
            msg = f"transform already exists: {component.name!r}"
            raise KeyError(msg)
        group = gfx.Group()
        group.name = component.name
        self._groups[component.name] = group
        self._pivots[component.name] = component.pivot
        self._transform_to_meshes[component.name] = []
        self._transform_to_lights[component.name] = []
        if component.parent:
            parent = self._groups.get(component.parent)
            if parent is None:
                msg = (
                    f"transform {component.name!r} missing parent {component.parent!r}"
                )
                raise KeyError(msg)
            parent.add(group)
        else:
            self.scene.add(group)
        self._apply_local(group, component)
        self._canvas.request_draw()

    def add_mesh(self, component: MeshComponent) -> None:
        """Add a mesh shape under its transform group.

        Args:
            component: Mesh to add. ``name`` must be unique.
        """
        if component.name in self._meshes:
            msg = f"mesh already exists: {component.name!r}"
            raise KeyError(msg)
        self._add_mesh(component)
        self._canvas.request_draw()

    def add_light(self, component: LightComponent) -> None:
        """Add a light shape under its transform group.

        Args:
            component: Light to add. ``name`` must be unique.
        """
        if component.name in self._lights:
            msg = f"light already exists: {component.name!r}"
            raise KeyError(msg)
        self._add_light(component)
        self._canvas.request_draw()

    def remove_mesh(self, name: str) -> None:
        """Remove a mesh shape and its outline from the scene.

        Args:
            name: Mesh component UID.
        """
        mesh = self._meshes.pop(name, None)
        if mesh is None:
            return
        self._mesh_components.pop(name, None)
        outline = self._outlines.pop(name, None)
        draw_wire = self._draw_wires.pop(name, None)
        bbox = self._bboxes.pop(name, None)
        if bbox is not None:
            self._helper_to_transform.pop(bbox.name, None)
        transform = self._mesh_to_transform.pop(name, None)
        if transform is not None:
            meshes = self._transform_to_meshes.get(transform, [])
            if name in meshes:
                meshes.remove(name)
        parent = mesh.parent
        if parent is not None:
            parent.remove(mesh)
        for child in (outline, draw_wire, bbox):
            if child is not None and child.parent is not None:
                child.parent.remove(child)
        self._apply_highlights()
        self._canvas.request_draw()

    def remove_light(self, name: str) -> None:
        """Remove a light shape and its helper from the scene.

        Args:
            name: Light component UID.
        """
        light = self._lights.pop(name, None)
        if light is None:
            return
        helper = self._light_helpers.pop(name, None)
        transform = self._light_to_transform.pop(name, None)
        if transform is not None:
            lights = self._transform_to_lights.get(transform, [])
            if name in lights:
                lights.remove(name)
        if helper is not None:
            self._helper_to_transform.pop(helper.name, None)
            parent = helper.parent
            if parent is not None:
                parent.remove(helper)
            # Point lights are parented under the helper.
            if light.parent is helper:
                helper.remove(light)
        parent = light.parent
        if parent is not None:
            parent.remove(light)
        self._canvas.request_draw()

    def remove_transform(self, name: str) -> None:
        """Remove a transform group (shapes must already be removed).

        Args:
            name: Transform UID.
        """
        group = self._groups.pop(name, None)
        if group is None:
            return
        self._transform_to_meshes.pop(name, None)
        self._transform_to_lights.pop(name, None)
        self._pivots.pop(name, None)
        self._selected.discard(name)
        if name in self._selected_order:
            self._selected_order = [
                item for item in self._selected_order if item != name
            ]
        parent = group.parent
        if parent is not None:
            parent.remove(group)
        self._end_drag()
        self._sync_gizmo_target()
        self._canvas.request_draw()

    def apply_light(self, component: LightComponent) -> None:
        """Push light fields from ``component`` onto its pygfx light."""
        light = self._lights.get(component.name)
        if light is None:
            return
        light.color = component.color
        light.intensity = component.intensity
        light.cast_shadow = component.cast_shadow
        if isinstance(light, (gfx.PointLight, gfx.SpotLight)):
            light.distance = component.distance
            light.decay = component.decay
        if component.cast_shadow:
            self._configure_light_shadow(light, component.kind)
        helper = self._light_helpers.get(component.name)
        if helper is not None and hasattr(helper, "material"):
            helper.material.color = component.color
        self._canvas.request_draw()

    def _is_descendant(self, ancestor: str, name: str) -> bool:
        """Return True when ``name`` is under ``ancestor`` in the group tree."""
        current = self._groups.get(name)
        while current is not None:
            parent = current.parent
            if parent is None:
                return False
            if parent.name == ancestor:
                return True
            current = parent if parent.name in self._groups else None
        return False

    def _shadow_reach(self, light: gfx.Light) -> float:
        """World-unit reach used for perspective shadow far planes."""
        distance = float(getattr(light, "distance", 0.0) or 0.0)
        # ``0`` means unlimited illumination — still clamp the shadow frustum.
        if distance <= 0.0:
            return 40.0
        return max(distance, 4.0)

    def _pin_perspective_shadow_depth(
        self,
        light: gfx.Light,
        *,
        near: float,
        far: float,
    ) -> None:
        """Keep a sane near/far after pygfx overwrites depth each frame.

        Point/Spot shadow ``_update_matrix`` sets
        ``depth_range = (far / 1e6, far)``, which is effectively near≈0 and
        causes the same contact gap we saw on directionals.
        """
        shadow = light.shadow
        shadow._mtqt_shadow_near = float(near)
        shadow._mtqt_shadow_far = float(max(far, near + 1.0))
        if getattr(shadow, "_mtqt_depth_pinned", False):
            return

        original = shadow._update_matrix

        def _update_matrix(
            light_obj: gfx.Light,
            *,
            _original=original,
            _shadow=shadow,
        ) -> None:
            _original(light_obj)
            pinned_near = float(_shadow._mtqt_shadow_near)
            pinned_far = float(_shadow._mtqt_shadow_far)
            _shadow.camera.depth_range = (
                pinned_near,
                max(pinned_far, pinned_near + 1.0),
            )

        shadow._update_matrix = _update_matrix  # type: ignore[method-assign]
        shadow._mtqt_depth_pinned = True

    def _configure_light_shadow(self, light: gfx.Light, kind: str) -> None:
        """Tune shadow map bias and frustum for this scene's scale.

        Directional: tight ortho + positive near/far along the light view.
        Point/spot: pin perspective near/far so pygfx cannot reset near≈0.
        """
        settings = self._settings
        shadow = light.shadow
        # Tiny bias; large values detach shadows from casters (peter-panning).
        shadow.bias = settings.shadow_bias
        shadow.cull_mode = settings.shadow_cull
        camera = shadow.camera
        if kind == "directional":
            # Default ortho is 1000² — far too coarse for a ~10-unit set.
            extent = settings.shadow_extent
            camera.width = extent
            camera.height = extent
            aim = (
                np.asarray(self._light_aim.world.position, dtype=np.float64)
                if self._light_aim is not None
                else np.zeros(3, dtype=np.float64)
            )
            light_pos = np.asarray(light.world.position, dtype=np.float64)
            distance = float(np.linalg.norm(light_pos - aim))
            # Scene sits in front of the shadow camera; keep near > 0.
            near = max(0.25, distance - extent * 0.75)
            far = max(near + 1.0, distance + extent)
            camera.depth_range = (near, far)
            if self._light_aim is not None:
                light.target = self._light_aim
        elif kind == "point":
            reach = self._shadow_reach(light)
            self._pin_perspective_shadow_depth(
                light,
                near=0.2,
                far=max(reach * 1.5, 8.0),
            )
        elif kind == "spot":
            reach = self._shadow_reach(light)
            self._pin_perspective_shadow_depth(
                light,
                near=0.25,
                far=max(reach * 1.5, 8.0),
            )
            if self._light_aim is not None:
                light.target = self._light_aim

    def refresh_shadows_for_transforms(self, names: Sequence[str]) -> None:
        """Retune shadow frustums for lights under the given transforms.

        Args:
            names: Transform UIDs that moved (e.g. after a gizmo drag).
        """
        for transform_name in names:
            for light_name in self._transform_to_lights.get(transform_name, ()):
                light = self._lights.get(light_name)
                if light is None or not light.cast_shadow:
                    continue
                kind = "point"
                if isinstance(light, gfx.DirectionalLight):
                    kind = "directional"
                elif isinstance(light, gfx.SpotLight):
                    kind = "spot"
                self._configure_light_shadow(light, kind)
        self._canvas.request_draw()

    def _install_viewport_grid(self) -> None:
        """Add a Maya-style XZ ground grid that is not a scene object."""
        self._rebuild_viewport_grid()

    def _rebuild_viewport_grid(self) -> None:
        """Recreate the XZ ground grid from current settings.

        When axes are enabled, the grid's center lines are tinted (X red,
        Z blue). No separate AxesHelper — that looked like a giant gizmo.
        """
        settings = self._settings
        if self._grid is not None and self._grid.parent is not None:
            self._grid.parent.remove(self._grid)

        # Same muted tone for all lines; axis tint is applied below.
        line = (0.32, 0.32, 0.32, 1.0)
        grid = gfx.GridHelper(
            size=settings.grid_size,
            divisions=int(settings.grid_divisions),
            color1=line,
            color2=line,
            thickness=1.0,
        )
        if settings.show_axes:
            self._tint_grid_axes(grid)
        grid.name = "viewportGrid"
        grid.cast_shadow = False
        grid.receive_shadow = False
        # Sit just under y=0 to reduce z-fight with ground planes.
        grid.local.position = (0.0, -0.001, 0.0)
        grid.visible = settings.show_grid
        self.scene.add(grid)
        self._grid = grid

    @staticmethod
    def _tint_grid_axes(grid: gfx.GridHelper) -> None:
        """Color the grid's X (red) and Z (blue) center lines."""
        colors = grid.geometry.colors
        # GridHelper packs lines as (axis, line_index, endpoint, rgba) with
        # axis 0 = constant-x (along Z), axis 1 = constant-z (along X).
        n_lines = int(colors.nitems // 4)
        mid = n_lines // 2
        shaped = np.asarray(colors.data, dtype=np.float32).reshape(2, n_lines, 2, 4)
        shaped[0, mid, :, :] = (0.29, 0.62, 1.0, 1.0)  # Z
        shaped[1, mid, :, :] = (0.91, 0.30, 0.24, 1.0)  # X
        colors.data[:] = shaped.reshape((-1, 4))
        colors.update_range(0, colors.nitems)

    def _resolve_transform(self, name: str) -> str | None:
        if name in self._groups:
            return name
        if name in self._mesh_to_transform:
            return self._mesh_to_transform[name]
        if name in self._light_to_transform:
            return self._light_to_transform[name]
        if name in self._helper_to_transform:
            return self._helper_to_transform[name]
        return None

    def _add_mesh(self, component: MeshComponent) -> None:
        group = self._groups.get(component.transform)
        if group is None:
            msg = f"mesh {component.name!r} missing transform {component.transform!r}"
            raise KeyError(msg)

        geometry = geometry_for(component.kind)
        mesh = gfx.Mesh(
            geometry,
            self._mesh_material(component),
            cast_shadow=component.cast_shadow,
            receive_shadow=component.receive_shadow,
        )
        mesh.name = component.name
        mesh.add_event_handler(self._on_pickable_click, "click")

        outline = gfx.Mesh(
            geometry,
            gfx.MeshBasicMaterial(
                color=_OUTLINE_COLOR,
                wireframe=True,
                wireframe_thickness=self._settings.outline_thickness,
            ),
            cast_shadow=False,
            receive_shadow=False,
        )
        outline.visible = False
        # Slight inflate so the wire rim clears the shaded surface.
        outline.local.scale = (1.015, 1.015, 1.015)
        mesh.add(outline)

        draw_wire = gfx.Mesh(
            geometry,
            gfx.MeshBasicMaterial(
                color=_DRAW_WIRE_COLOR,
                wireframe=True,
                wireframe_thickness=1.0,
            ),
            cast_shadow=False,
            receive_shadow=False,
        )
        draw_wire.visible = False
        draw_wire.local.scale = (1.008, 1.008, 1.008)
        mesh.add(draw_wire)

        # Sibling of the mesh so bbox stays visible when the mesh is hidden.
        bbox = gfx.Line(
            self._aabb_line_geometry(mesh),
            gfx.LineSegmentMaterial(
                thickness=1.5,
                color=_BBOX_COLOR,
                aa=True,
                pick_write=True,
            ),
        )
        bbox.name = f"{component.name}BBox"
        bbox.visible = False
        bbox.cast_shadow = False
        bbox.receive_shadow = False
        bbox.add_event_handler(self._on_pickable_click, "click")
        group.add(mesh)
        group.add(bbox)

        self._meshes[component.name] = mesh
        self._mesh_components[component.name] = component.model_copy(deep=True)
        self._outlines[component.name] = outline
        self._draw_wires[component.name] = draw_wire
        self._bboxes[component.name] = bbox
        self._mesh_to_transform[component.name] = component.transform
        self._helper_to_transform[bbox.name] = component.transform
        self._transform_to_meshes[component.transform].append(component.name)
        self._apply_draw_mode_to_mesh(component.name)

    def _add_light(self, component: LightComponent) -> None:
        group = self._groups.get(component.transform)
        if group is None:
            msg = f"light {component.name!r} missing transform {component.transform!r}"
            raise KeyError(msg)

        if component.kind == "directional":
            light = gfx.DirectionalLight(
                component.color,
                intensity=component.intensity,
                cast_shadow=component.cast_shadow,
                target=self._light_aim,
            )
            helper = gfx.DirectionalLightHelper(ray_length=1.5)
        elif component.kind == "point":
            light = gfx.PointLight(
                component.color,
                intensity=component.intensity,
                cast_shadow=component.cast_shadow,
                distance=component.distance,
                decay=component.decay,
            )
            helper = gfx.PointLightHelper(size=0.35, color=component.color)
            helper.material.pick_write = True
        elif component.kind == "spot":
            light = gfx.SpotLight(
                component.color,
                intensity=component.intensity,
                cast_shadow=component.cast_shadow,
                distance=component.distance,
                decay=component.decay,
            )
            if self._light_aim is not None:
                light.target = self._light_aim
            helper = gfx.SpotLightHelper()
            helper.material.pick_write = True
        else:
            msg = f"unsupported light kind {component.kind!r}"
            raise ValueError(msg)

        light.name = component.name
        if component.cast_shadow:
            self._configure_light_shadow(light, component.kind)
        helper.name = f"{component.name}Helper"
        helper.add_event_handler(self._on_pickable_click, "click")
        if component.kind == "point":
            # PointLightHelper expects to parent the light (pygfx docs).
            helper.add(light)
            group.add(helper)
        else:
            light.add(helper)
            group.add(light)

        self._lights[component.name] = light
        self._light_helpers[component.name] = helper
        self._light_to_transform[component.name] = component.transform
        self._helper_to_transform[helper.name] = component.transform
        self._transform_to_lights[component.transform].append(component.name)

    def _apply_local(self, group: gfx.Group, component: TransformComponent) -> None:
        group.local.scale = component.scale
        group.local.rotation = la.quat_from_euler(component.rotation, order="xyz")
        group.local.position = component.position
        group.visible = component.visible

    def _draw(self) -> None:
        self.renderer.render(self.scene, self.camera)

    def _apply_highlights(self) -> None:
        selected_meshes: set[str] = set()
        for transform_name in self._selected:
            selected_meshes.update(self._transform_to_meshes.get(transform_name, ()))
        show_outline = self._draw_mode != "bounding_box"
        for name, outline in self._outlines.items():
            outline.visible = show_outline and name in selected_meshes
        for name, bbox in self._bboxes.items():
            selected = name in selected_meshes
            bbox.material.color = _BBOX_SELECTED_COLOR if selected else _BBOX_COLOR

    def _end_drag(self) -> None:
        self._drag_active = False
        self._drag_rels.clear()

    def _place_manip_proxy(self) -> bool:
        """Position the manip proxy at the selection pivot. Return False if none."""
        if not self._selected_order:
            return False
        if self._affect_pivot and len(self._selected_order) != 1:
            return False

        if len(self._selected_order) == 1:
            name = self._selected_order[0]
            group = self._groups.get(name)
            if group is None:
                return False
            pivot = self._pivots.get(name, (0.0, 0.0, 0.0))
            self._manip_proxy.world.matrix = pivot_world_matrix(
                np.asarray(group.world.matrix, dtype=np.float64),
                pivot,
            )
            return True

        world_points: list[Vec3] = []
        for name in self._selected_order:
            group = self._groups.get(name)
            if group is None:
                continue
            pivot = self._pivots.get(name, (0.0, 0.0, 0.0))
            matrix = pivot_world_matrix(
                np.asarray(group.world.matrix, dtype=np.float64),
                pivot,
            )
            world_points.append(as_vec3(matrix[:3, 3]))
        if not world_points:
            return False
        center = average_positions(world_points)
        last = self._selected_order[-1]
        last_group = self._groups.get(last)
        if last_group is None:
            self._manip_proxy.world.matrix = translate_mat(center)
            return True
        last_pivot = self._pivots.get(last, (0.0, 0.0, 0.0))
        base = pivot_world_matrix(
            np.asarray(last_group.world.matrix, dtype=np.float64),
            last_pivot,
        ).copy()
        base[0, 3] = center[0]
        base[1, 3] = center[1]
        base[2, 3] = center[2]
        self._manip_proxy.world.matrix = base
        return True

    def _sync_gizmo_target(self) -> None:
        if self._drag_active:
            return
        if self._interaction_tool == "select" or not self._selected_order:
            self._gizmo.set_object(None)
            return
        if self._affect_pivot and len(self._selected_order) != 1:
            self._gizmo.set_object(None)
            return
        if not self._place_manip_proxy():
            self._gizmo.set_object(None)
            return
        self._gizmo.set_object(self._manip_proxy)

    def _ensure_drag_started(self) -> None:
        if self._drag_active:
            return
        self._drag_active = True
        self._drag_rels.clear()
        proxy = np.asarray(self._manip_proxy.world.matrix, dtype=np.float64)
        proxy_inv = np.asarray(la.mat_inverse(proxy), dtype=np.float64)
        for name in self._selected_order:
            group = self._groups.get(name)
            if group is None:
                continue
            world = np.asarray(group.world.matrix, dtype=np.float64)
            self._drag_rels[name] = proxy_inv @ world

    def _apply_proxy_to_scene(self) -> None:
        if self._affect_pivot:
            if len(self._selected_order) != 1:
                return
            name = self._selected_order[0]
            group = self._groups.get(name)
            if group is None:
                return
            world_point = as_vec3(self._manip_proxy.world.position)
            self._pivots[name] = local_pivot_from_world_point(
                np.asarray(group.world.matrix, dtype=np.float64),
                world_point,
            )
            return

        self._ensure_drag_started()
        proxy = np.asarray(self._manip_proxy.world.matrix, dtype=np.float64)
        for name, rel in self._drag_rels.items():
            group = self._groups.get(name)
            if group is None:
                continue
            group.world.matrix = proxy @ rel

    def _on_gizmo_changed(self, finalized: bool) -> None:
        if not self._selected_order:
            return
        self._apply_proxy_to_scene()
        names = list(self._selected_order)
        if self._on_transform_manipulated is not None:
            self._on_transform_manipulated(names, finalized)
        if finalized:
            self.refresh_shadows_for_transforms(names)
            self._end_drag()
            self._sync_gizmo_target()
        self._canvas.request_draw()

    def _is_under_gizmo(self, world: gfx.WorldObject | None) -> bool:
        current = world
        while current is not None:
            if current is self._gizmo or current is self._manip_proxy:
                return True
            current = current.parent
        return False

    def _emit_selection(self) -> None:
        if self._on_selection_changed is None:
            return
        self._on_selection_changed(self.selected_names)

    def _select_from_click(self, name: str | None, modifiers: Sequence[str]) -> None:
        additive = bool(_ADDITIVE_MODIFIERS.intersection(modifiers))
        if name is None:
            if additive:
                return
            next_order: list[str] = []
        elif additive:
            next_order = list(self._selected_order)
            if name in self._selected:
                next_order = [item for item in next_order if item != name]
            else:
                next_order.append(name)
        else:
            next_order = [name]

        next_selected = set(next_order)
        if next_selected == self._selected and next_order == self._selected_order:
            return
        self._end_drag()
        self._selected = next_selected
        self._selected_order = next_order
        self._apply_highlights()
        self._sync_gizmo_target()
        self._canvas.request_draw()
        self._updating = True
        try:
            self._emit_selection()
        finally:
            self._updating = False

    def _on_pickable_click(self, event: gfx.PointerEvent) -> None:
        modifiers = event.modifiers or ()
        target_name = event.target.name
        transform_name = self._resolve_transform(target_name)
        self._select_from_click(transform_name, modifiers)
        event.stop_propagation()

    def _on_renderer_click(self, event: gfx.PointerEvent) -> None:
        if self._updating:
            return
        modifiers = event.modifiers or ()
        pick = self.renderer.get_pick_info((event.x, event.y))
        world = pick.get("world_object")
        if world is None:
            self._select_from_click(None, modifiers)
            return
        if self._is_under_gizmo(world):
            return
        resolved = self._resolve_transform(getattr(world, "name", ""))
        if resolved is not None:
            return
        self._select_from_click(None, modifiers)


def attach_viewport(
    canvas: QRenderWidget,
    transforms: list[TransformComponent],
    meshes: list[MeshComponent],
    lights: list[LightComponent],
    *,
    on_selection_changed: Callable[[list[str]], None] | None = None,
    on_transform_manipulated: Callable[[list[str], bool], None] | None = None,
) -> ViewportController:
    """Build a lit selectable pygfx scene on ``canvas``.

    Args:
        canvas: rendercanvas Qt widget.
        transforms: Empty transform nodes (``gfx.Group`` parents).
        meshes: Mesh shapes parented under those transforms.
        lights: Light shapes parented under those transforms.
        on_selection_changed: Called with selected transform UIDs after a pick.
        on_transform_manipulated: ``(transform_names, finalized)`` after a
            gizmo drag step or release.

    Returns:
        The ``ViewportController`` bound to ``canvas``.
    """
    return ViewportController(
        canvas,
        transforms,
        meshes,
        lights,
        on_selection_changed=on_selection_changed,
        on_transform_manipulated=on_transform_manipulated,
    )
