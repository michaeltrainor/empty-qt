"""Main window with a scene graph beside a rendercanvas viewport."""

from __future__ import annotations

from pydantic import BaseModel
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QSplitter, QToolBar, QWidget
from rendercanvas.qt import QRenderWidget

from examples.scene_viewport.components import (
    LightComponent,
    LightKind,
    MeshComponent,
    Space,
    TransformComponent,
)
from examples.scene_viewport.gizmo import InteractionTool
from examples.scene_viewport.history import HistoryStep, SceneHistory, SceneObjects
from examples.scene_viewport.mesh.types import PrimitiveKind
from examples.scene_viewport.scene import (
    FOLDER_NAMES,
    build_forest,
    light_components,
    mesh_components,
    transform_components,
)
from examples.scene_viewport.settings import AppSettings, load_settings, save_settings
from examples.scene_viewport.settings_dialog import SettingsDialog
from examples.scene_viewport.viewport import ViewportController, attach_viewport
from mtqt import (
    PropertiesBook,
    SceneGraphTree,
    apply_stylesheet,
    ensure_application,
)

_MESH_DEFAULTS: dict[PrimitiveKind, dict[str, object]] = {
    "box": {"color": "#c47a3a", "position": (0.0, 0.5, 0.0)},
    "plane": {
        "color": "#5a6358",
        "position": (0.0, 0.0, 0.0),
        "scale": (4.0, 1.0, 4.0),
    },
    "sphere": {"color": "#4a8fd4", "position": (0.0, 0.5, 0.0)},
}

_LIGHT_DEFAULTS: dict[LightKind, dict[str, object]] = {
    "directional": {
        "color": "#fff5e6",
        "intensity": 1.4,
        "position": (4.0, 8.0, 3.0),
    },
    "point": {
        "color": "#a8c4ff",
        "intensity": 2.0,
        "distance": 12.0,
        "decay": 2.0,
        "position": (1.5, 2.2, 2.0),
    },
    "spot": {
        "color": "#fff5e6",
        "intensity": 2.0,
        "position": (2.0, 4.0, 2.0),
    },
}


class SceneViewportWindow(QMainWindow):
    """Tree, pygfx viewport, toolbar, and a tabbed properties book.

    Selection is always a **transform**. Mesh and light shapes map to their
    parent transform. Edit space defaults to **world** (panel + gizmo).

    Maya-aligned tools (toolbar + hotkeys): **Q** select, **W** move,
    **E** rotate, **R** scale; World/Local on the toolbar; **D** edit pivot.
    **P** parent to last selected, **Shift+P** unparent. Ctrl+Z / Ctrl+Y
    undo and redo. Create menu adds empties, meshes, and lights.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scene viewport")
        self.resize(1200, 640)
        self._syncing = False
        self._gizmo_dragging = False
        self._history = SceneHistory()
        self._gizmo_before: dict[str, TransformComponent] = {}
        self._settings: AppSettings = load_settings()
        self._edit_space: Space = self._settings.default_edit_space
        self._tool_actions: dict[InteractionTool, QAction] = {}
        self._space_actions: dict[Space, QAction] = {}
        self._pivot_action: QAction | None = None

        transforms = transform_components()
        meshes = mesh_components()
        lights = light_components()
        self._transforms = {item.name: item for item in transforms}
        self._meshes = {item.name: item for item in meshes}
        self._lights = {item.name: item for item in lights}
        self._mesh_to_transform = {item.name: item.transform for item in meshes}
        self._light_to_transform = {item.name: item.transform for item in lights}
        self._meshes_by_transform: dict[str, list[str]] = {}
        for item in meshes:
            self._meshes_by_transform.setdefault(item.transform, []).append(item.name)
        self._lights_by_transform: dict[str, list[str]] = {}
        for item in lights:
            self._lights_by_transform.setdefault(item.transform, []).append(item.name)

        splitter = QSplitter(self)
        splitter.setObjectName("SceneViewportSplitter")
        splitter.setHandleWidth(2)
        splitter.setChildrenCollapsible(False)
        self._tree = SceneGraphTree(
            build_forest(transforms, meshes, lights),
            parent=splitter,
        )
        self._tree.setMinimumWidth(200)
        self._canvas = QRenderWidget(splitter, update_mode="continuous")
        self._canvas.setObjectName("SceneViewportCanvas")
        self._props = PropertiesBook(
            splitter,
            empty_message="Select a transform (mesh/light picks map to it).",
        )
        self._props.setMinimumWidth(240)
        splitter.addWidget(self._tree)
        splitter.addWidget(self._canvas)
        splitter.addWidget(self._props)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([260, 700, 280])
        self.setCentralWidget(splitter)
        apply_stylesheet(self, "base", "widgets")

        self._viewport: ViewportController = attach_viewport(
            self._canvas,
            transforms,
            meshes,
            lights,
            on_selection_changed=self._on_viewport_selection,
            on_transform_manipulated=self._on_gizmo_manipulated,
        )
        self._viewport.apply_settings(self._settings)
        self._viewport.set_gizmo_space(self._edit_space)
        self._tree.selectedNamesChanged.connect(self._on_tree_selection)

        self._props.set_editable_check(self._is_prop_editable)
        self._props.register_handler(self._on_prop_field)
        self._install_menus()
        self._install_toolbar()

    def _install_menus(self) -> None:
        create_menu = self.menuBar().addMenu("&Create")
        create_menu.addAction("Empty Transform", self._create_empty)

        poly = create_menu.addMenu("Polygon Primitives")
        poly.addAction("Cube", lambda: self._create_mesh("box"))
        poly.addAction("Plane", lambda: self._create_mesh("plane"))
        poly.addAction("Sphere", lambda: self._create_mesh("sphere"))

        lights = create_menu.addMenu("Lights")
        lights.addAction("Directional Light", lambda: self._create_light("directional"))
        lights.addAction("Point Light", lambda: self._create_light("point"))

        edit_menu = self.menuBar().addMenu("&Edit")
        undo_action = QAction("Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._redo)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()

        parent_action = QAction("Parent to Last Selected", self)
        parent_action.setShortcut(QKeySequence("P"))
        parent_action.triggered.connect(self._parent_selection)
        edit_menu.addAction(parent_action)

        unparent_action = QAction("Unparent", self)
        unparent_action.setShortcut(QKeySequence("Shift+P"))
        unparent_action.triggered.connect(self._unparent_selection)
        edit_menu.addAction(unparent_action)
        edit_menu.addSeparator()

        delete_action = QAction("Delete", self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._delete_selection)
        edit_menu.addAction(delete_action)

        # Backspace as a Maya-style alias on macOS (Delete is often Fn-Backspace).
        backspace = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        backspace.activated.connect(self._delete_selection)

        settings_menu = self.menuBar().addMenu("&Settings")
        prefs = QAction("Preferences...", self)
        prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        prefs.triggered.connect(self._open_preferences)
        settings_menu.addAction(prefs)

    def _open_preferences(self) -> None:
        """Show the preferences dialog and apply/save on Accept."""
        dialog = SettingsDialog(self._settings, parent=self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        self._settings = dialog.settings
        save_settings(self._settings)
        self._viewport.apply_settings(self._settings)
        self._set_edit_space(self._settings.default_edit_space)

    def _install_toolbar(self) -> None:
        toolbar = QToolBar("Tools", self)
        toolbar.setObjectName("SceneViewportToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        tool_group = QActionGroup(self)
        tool_group.setExclusive(True)
        tool_specs: list[tuple[InteractionTool, str, str]] = [
            ("select", "Select", "Q"),
            ("translate", "Move", "W"),
            ("rotate", "Rotate", "E"),
            ("scale", "Scale", "R"),
        ]
        for tool, label, shortcut in tool_specs:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            action.setToolTip(f"{label} ({shortcut})")
            action.triggered.connect(
                lambda checked=False, current=tool: self._set_tool(current)
            )
            tool_group.addAction(action)
            toolbar.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions["select"].setChecked(True)

        toolbar.addSeparator()

        space_group = QActionGroup(self)
        space_group.setExclusive(True)
        for space, label in (("world", "World"), ("local", "Local")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(f"Edit in {label.lower()} space")
            action.triggered.connect(
                lambda checked=False, current=space: self._set_edit_space(current)
            )
            space_group.addAction(action)
            toolbar.addAction(action)
            self._space_actions[space] = action
        self._space_actions[self._edit_space].setChecked(True)

        toolbar.addSeparator()
        pivot_action = QAction("Edit Pivot", self)
        pivot_action.setCheckable(True)
        pivot_action.setShortcut(QKeySequence("D"))
        pivot_action.setToolTip("Affect pivot only (D)")
        pivot_action.toggled.connect(self._set_affect_pivot)
        toolbar.addAction(pivot_action)
        self._pivot_action = pivot_action

    def _set_tool(self, tool: InteractionTool) -> None:
        action = self._tool_actions.get(tool)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._viewport.set_interaction_tool(tool)

    def _set_affect_pivot(self, enabled: bool) -> None:
        self._viewport.set_affect_pivot(enabled)

    def _set_edit_space(self, space: Space, *, refresh_panel: bool = True) -> None:
        self._edit_space = space
        self._viewport.set_gizmo_space(space)
        action = self._space_actions.get(space)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        names = self._viewport.selected_names
        if len(names) != 1:
            return
        name = names[0]
        stored = self._transforms.get(name)
        if stored is None or stored.space == space:
            return
        self._transforms[name] = stored.model_copy(update={"space": space})
        if refresh_panel and not self._gizmo_dragging:
            self._props.set_pages(self._pages_for_transform(name))

    def _used_names(self) -> set[str]:
        return (
            set(self._transforms)
            | set(self._meshes)
            | set(self._lights)
            | set(FOLDER_NAMES)
        )

    def _unique_name(self, stem: str) -> str:
        used = self._used_names()
        if stem not in used:
            return stem
        index = 1
        while f"{stem}{index}" in used:
            index += 1
        return f"{stem}{index}"

    def _selected_parent(self) -> str:
        names = self._viewport.selected_names
        if len(names) == 1 and names[0] in self._transforms:
            return names[0]
        return ""

    def _snapshot_transform(self, name: str) -> TransformComponent | None:
        stored = self._transforms.get(name)
        if stored is None:
            return None
        return stored.model_copy(deep=True)

    def _push_transform_edits(
        self,
        label: str,
        before: dict[str, TransformComponent],
        after: dict[str, TransformComponent],
    ) -> None:
        if before == after:
            return
        self._history.push(
            HistoryStep(
                label=label,
                transforms_before=before,
                transforms_after=after,
            )
        )

    def _rebuild_tree(self, select: list[str] | None = None) -> None:
        forest = build_forest(
            self._transforms.values(),
            self._meshes.values(),
            self._lights.values(),
        )
        self._syncing = True
        try:
            self._tree.model.set_nodes(forest)
            self._tree.view.expandAll()
            if select:
                self._tree.set_selected_names(select)
                self._viewport.set_selected_names(select)
            else:
                self._tree.set_selected_names([])
                self._viewport.set_selected_names([])
        finally:
            self._syncing = False
        self._sync_properties(select or [])

    def _register_transform(self, component: TransformComponent) -> None:
        self._transforms[component.name] = component
        self._meshes_by_transform.setdefault(component.name, [])
        self._lights_by_transform.setdefault(component.name, [])
        self._viewport.add_transform(component)

    def _register_mesh(self, component: MeshComponent) -> None:
        self._meshes[component.name] = component
        self._mesh_to_transform[component.name] = component.transform
        self._meshes_by_transform.setdefault(component.transform, []).append(
            component.name
        )
        self._viewport.add_mesh(component)

    def _register_light(self, component: LightComponent) -> None:
        self._lights[component.name] = component
        self._light_to_transform[component.name] = component.transform
        self._lights_by_transform.setdefault(component.transform, []).append(
            component.name
        )
        self._viewport.add_light(component)

    def _unregister_transform(self, name: str) -> None:
        for mesh_name in list(self._meshes_by_transform.get(name, ())):
            self._viewport.remove_mesh(mesh_name)
            self._meshes.pop(mesh_name, None)
            self._mesh_to_transform.pop(mesh_name, None)
        self._meshes_by_transform.pop(name, None)
        for light_name in list(self._lights_by_transform.get(name, ())):
            self._viewport.remove_light(light_name)
            self._lights.pop(light_name, None)
            self._light_to_transform.pop(light_name, None)
        self._lights_by_transform.pop(name, None)
        self._viewport.remove_transform(name)
        self._transforms.pop(name, None)

    def _create_empty(self) -> None:
        name = self._unique_name("null")
        component = TransformComponent(
            name=name,
            parent=self._selected_parent(),
            space=self._edit_space,
        )
        self._register_transform(component)
        self._history.push(
            HistoryStep(
                label="Create Empty",
                added=SceneObjects(transforms=[component.model_copy(deep=True)]),
            )
        )
        self._rebuild_tree([name])

    def _create_mesh(self, kind: PrimitiveKind) -> None:
        defaults = _MESH_DEFAULTS[kind]
        name = self._unique_name(kind)
        shape = f"{name}Shape"
        transform = TransformComponent(
            name=name,
            parent=self._selected_parent(),
            space=self._edit_space,
            position=defaults.get("position", (0.0, 0.0, 0.0)),  # type: ignore[arg-type]
            scale=defaults.get("scale", (1.0, 1.0, 1.0)),  # type: ignore[arg-type]
        )
        mesh = MeshComponent(
            name=shape,
            transform=name,
            kind=kind,
            color=str(defaults["color"]),
        )
        self._register_transform(transform)
        self._register_mesh(mesh)
        self._history.push(
            HistoryStep(
                label=f"Create {kind}",
                added=SceneObjects(
                    transforms=[transform.model_copy(deep=True)],
                    meshes=[mesh.model_copy(deep=True)],
                ),
            )
        )
        self._rebuild_tree([name])

    def _create_light(self, kind: LightKind) -> None:
        if kind == "spot":
            # Spot create is reserved; helpers exist but UI focuses on dir/point.
            kind = "point"
        defaults = _LIGHT_DEFAULTS[kind]
        name = self._unique_name(kind)
        shape = f"{name}Shape"
        transform = TransformComponent(
            name=name,
            parent="",
            space=self._edit_space,
            position=defaults.get("position", (0.0, 2.0, 0.0)),  # type: ignore[arg-type]
        )
        light = LightComponent(
            name=shape,
            transform=name,
            kind=kind,
            color=str(defaults["color"]),
            intensity=float(defaults.get("intensity", 1.4)),
            distance=float(defaults.get("distance", 0.0)),
            decay=float(defaults.get("decay", 2.0)),
            cast_shadow=True,
        )
        self._register_transform(transform)
        self._register_light(light)
        self._history.push(
            HistoryStep(
                label=f"Create {kind} light",
                added=SceneObjects(
                    transforms=[transform.model_copy(deep=True)],
                    lights=[light.model_copy(deep=True)],
                ),
            )
        )
        self._rebuild_tree([name])

    def _descendant_transforms(self, roots: list[str]) -> list[str]:
        """Return ``roots`` plus descendants, parents before children."""
        children: dict[str, list[str]] = {}
        for transform in self._transforms.values():
            children.setdefault(transform.parent, []).append(transform.name)

        ordered: list[str] = []
        seen: set[str] = set()

        def walk(name: str) -> None:
            if name in seen or name not in self._transforms:
                return
            seen.add(name)
            ordered.append(name)
            for child in children.get(name, ()):
                walk(child)

        for root in roots:
            walk(root)
        return ordered

    def _delete_selection(self) -> None:
        selected = self._names_for_viewport(self._tree.selected_names)
        selected = [name for name in selected if name not in FOLDER_NAMES]
        if not selected:
            return
        # Capture deepest-first removal order; store parents-before-children.
        to_remove = list(reversed(self._descendant_transforms(selected)))
        removed_transforms = [
            self._transforms[name].model_copy(deep=True)
            for name in reversed(to_remove)
            if name in self._transforms
        ]
        removed_meshes: list[MeshComponent] = []
        removed_lights: list[LightComponent] = []
        for name in to_remove:
            for mesh_name in list(self._meshes_by_transform.get(name, ())):
                mesh = self._meshes.get(mesh_name)
                if mesh is not None:
                    removed_meshes.append(mesh.model_copy(deep=True))
            for light_name in list(self._lights_by_transform.get(name, ())):
                light = self._lights.get(light_name)
                if light is not None:
                    removed_lights.append(light.model_copy(deep=True))
            self._unregister_transform(name)
        self._history.push(
            HistoryStep(
                label="Delete",
                removed=SceneObjects(
                    transforms=removed_transforms,
                    meshes=removed_meshes,
                    lights=removed_lights,
                ),
            )
        )
        self._rebuild_tree([])

    def _parent_selection(self) -> None:
        """Parent all but last selected under the last selected transform."""
        names = [
            name
            for name in self._viewport.selected_names
            if name in self._transforms and name not in FOLDER_NAMES
        ]
        if len(names) < 2:
            return
        new_parent = names[-1]
        before: dict[str, TransformComponent] = {}
        after: dict[str, TransformComponent] = {}
        for name in names[:-1]:
            if name == new_parent:
                continue
            stored = self._transforms.get(name)
            if stored is None or stored.parent == new_parent:
                continue
            snap = self._snapshot_transform(name)
            if snap is None:
                continue
            local = self._viewport.reparent_transform(name, new_parent)
            if local is None:
                continue
            position, rotation, scale = local
            updated = stored.model_copy(
                update={
                    "parent": new_parent,
                    "position": position,
                    "rotation": rotation,
                    "scale": scale,
                }
            )
            before[name] = snap
            after[name] = updated
            self._transforms[name] = updated
        if not after:
            return
        self._push_transform_edits("Parent", before, after)
        self._rebuild_tree(names)

    def _unparent_selection(self) -> None:
        """Move selected transforms to the scene root, preserving world pose."""
        names = [
            name
            for name in self._viewport.selected_names
            if name in self._transforms and name not in FOLDER_NAMES
        ]
        if not names:
            return
        before: dict[str, TransformComponent] = {}
        after: dict[str, TransformComponent] = {}
        for name in names:
            stored = self._transforms.get(name)
            if stored is None or not stored.parent:
                continue
            snap = self._snapshot_transform(name)
            if snap is None:
                continue
            local = self._viewport.reparent_transform(name, "")
            if local is None:
                continue
            position, rotation, scale = local
            updated = stored.model_copy(
                update={
                    "parent": "",
                    "position": position,
                    "rotation": rotation,
                    "scale": scale,
                }
            )
            before[name] = snap
            after[name] = updated
            self._transforms[name] = updated
        if not after:
            return
        self._push_transform_edits("Unparent", before, after)
        self._rebuild_tree(names)

    def _apply_stored_transform(self, component: TransformComponent) -> None:
        """Write hierarchy + local TRS/pivot from a stored component."""
        self._transforms[component.name] = component.model_copy(deep=True)
        self._viewport.set_parent(component.name, component.parent)
        self._viewport.apply_transform_local(component)

    def _restore_objects(self, objects: SceneObjects) -> None:
        # Parents before children: transforms list is stored that way on delete.
        for transform in objects.transforms:
            if transform.name not in self._transforms:
                self._register_transform(transform)
            else:
                self._apply_stored_transform(transform)
        for mesh in objects.meshes:
            if mesh.name not in self._meshes:
                self._register_mesh(mesh)
            else:
                self._meshes[mesh.name] = mesh
                self._viewport.apply_mesh(mesh)
        for light in objects.lights:
            if light.name not in self._lights:
                self._register_light(light)
            else:
                self._lights[light.name] = light
                self._viewport.apply_light(light)

    def _remove_objects(self, objects: SceneObjects) -> None:
        names = [item.name for item in objects.transforms]
        # Remove deepest first.
        for name in reversed(self._descendant_transforms(names)):
            if name in self._transforms:
                self._unregister_transform(name)

    def _apply_history_step(self, step: HistoryStep, *, undo: bool) -> None:
        if undo:
            if step.added is not None:
                self._remove_objects(step.added)
            if step.removed is not None:
                self._restore_objects(step.removed)
            for component in step.transforms_before.values():
                self._apply_stored_transform(component)
        else:
            if step.removed is not None:
                self._remove_objects(step.removed)
            if step.added is not None:
                self._restore_objects(step.added)
            for component in step.transforms_after.values():
                self._apply_stored_transform(component)
        select = list(step.transforms_after or step.transforms_before)
        if step.added is not None and not undo:
            select = [item.name for item in step.added.transforms]
        if step.removed is not None and undo:
            select = [item.name for item in step.removed.transforms]
        self._rebuild_tree([name for name in select if name in self._transforms])

    def _undo(self) -> None:
        step = self._history.undo()
        if step is None:
            return
        self._apply_history_step(step, undo=True)

    def _redo(self) -> None:
        step = self._history.redo()
        if step is None:
            return
        self._apply_history_step(step, undo=False)

    def _resolve_transform(self, name: str) -> str | None:
        if name in self._transforms:
            return name
        if name in self._mesh_to_transform:
            return self._mesh_to_transform[name]
        if name in self._light_to_transform:
            return self._light_to_transform[name]
        return None

    def _is_prop_editable(self, field_name: str, model: BaseModel) -> bool:
        if isinstance(model, TransformComponent):
            return field_name in {"space", "position", "rotation", "scale", "pivot"}
        if isinstance(model, MeshComponent):
            return field_name in {"color", "cast_shadow", "receive_shadow"}
        if isinstance(model, LightComponent):
            shared = {"color", "intensity", "cast_shadow"}
            if model.kind == "point":
                return field_name in shared | {"distance", "decay"}
            return field_name in shared
        return False

    def _panel_transform(self, stored: TransformComponent) -> TransformComponent:
        """Build a panel model whose TRS matches ``stored.space``."""
        if stored.space == "local":
            return stored
        position, rotation, scale = self._viewport.read_world_trs(stored.name)
        return stored.model_copy(
            update={
                "position": position,
                "rotation": rotation,
                "scale": scale,
            }
        )

    def _pages_for_transform(self, name: str) -> list[tuple[str, BaseModel]]:
        stored = self._transforms[name]
        pages: list[tuple[str, BaseModel]] = [
            ("Transform", self._panel_transform(stored)),
        ]
        for mesh_name in self._meshes_by_transform.get(name, ()):
            pages.append(("Mesh", self._meshes[mesh_name]))
        for light_name in self._lights_by_transform.get(name, ()):
            pages.append(("Light", self._lights[light_name]))
        return pages

    def _on_prop_field(
        self,
        field_name: str,
        value: object,
        model: BaseModel,
    ) -> None:
        if isinstance(model, MeshComponent):
            self._meshes[model.name] = model
            self._viewport.apply_mesh(model)
            return
        if isinstance(model, LightComponent):
            self._lights[model.name] = model
            self._viewport.apply_light(model)
            return
        if not isinstance(model, TransformComponent):
            return

        stored = self._transforms[model.name]
        if field_name == "space":
            self._set_edit_space(model.space, refresh_panel=True)
            return

        before = {model.name: stored.model_copy(deep=True)}
        if field_name == "pivot":
            updated = stored.model_copy(update={"pivot": model.pivot})
            self._transforms[model.name] = updated
            self._viewport.set_pivot(model.name, model.pivot)
            self._push_transform_edits(
                "Edit Pivot",
                before,
                {model.name: updated.model_copy(deep=True)},
            )
            self._props.set_pages(self._pages_for_transform(model.name))
            return

        if model.space == "local":
            updated = stored.model_copy(
                update={
                    "space": "local",
                    "position": model.position,
                    "rotation": model.rotation,
                    "scale": model.scale,
                }
            )
            self._viewport.apply_transform_local(updated)
        else:
            local = self._viewport.apply_transform_world(
                model.name,
                model.position,
                model.rotation,
                model.scale,
            )
            if local is None:
                return
            updated = stored.model_copy(
                update={
                    "space": "world",
                    "position": local.position,
                    "rotation": local.rotation,
                    "scale": local.scale,
                }
            )
        self._transforms[model.name] = updated
        self._push_transform_edits(
            "Edit Transform",
            before,
            {model.name: updated.model_copy(deep=True)},
        )
        self._props.set_pages(self._pages_for_transform(model.name))

    def _names_for_viewport(self, names: list[str]) -> list[str]:
        """Map tree selection to transform UIDs for the viewport."""
        result: list[str] = []
        seen: set[str] = set()
        for name in names:
            transform = self._resolve_transform(name)
            if transform is not None and transform not in seen:
                seen.add(transform)
                result.append(transform)
        return result

    def _sync_properties(self, names: list[str]) -> None:
        transforms = self._names_for_viewport(names)
        if len(transforms) != 1:
            self._props.clear()
            return
        name = transforms[0]
        if name not in self._transforms:
            self._props.clear()
            return
        # Keep panel space aligned with the session edit space (default world).
        stored = self._transforms[name]
        if stored.space != self._edit_space:
            self._transforms[name] = stored.model_copy(
                update={"space": self._edit_space}
            )
        self._viewport.set_gizmo_space(self._edit_space)
        self._props.set_pages(self._pages_for_transform(name))

    def _store_from_viewport(self, names: list[str]) -> None:
        for name in names:
            if name not in self._transforms:
                continue
            position, rotation, scale = self._viewport.read_local_trs(name)
            pivot = self._viewport.read_pivot(name)
            stored = self._transforms[name]
            self._transforms[name] = stored.model_copy(
                update={
                    "position": position,
                    "rotation": rotation,
                    "scale": scale,
                    "pivot": pivot,
                }
            )

    def _on_gizmo_manipulated(self, names: list[str], finalized: bool) -> None:
        if not names:
            return
        if not self._gizmo_dragging:
            self._gizmo_before = {
                name: snap
                for name in names
                if (snap := self._snapshot_transform(name)) is not None
            }
        self._gizmo_dragging = not finalized
        self._store_from_viewport(names)
        if finalized:
            after = {
                name: snap
                for name in names
                if (snap := self._snapshot_transform(name)) is not None
            }
            label = "Edit Pivot" if self._viewport.affect_pivot else "Transform"
            self._push_transform_edits(label, self._gizmo_before, after)
            self._gizmo_before = {}
            if len(names) == 1:
                self._props.set_pages(self._pages_for_transform(names[0]))

    def _on_tree_selection(self, names: list[str]) -> None:
        if self._syncing:
            return
        viewport_names = self._names_for_viewport(names)
        self._syncing = True
        try:
            self._viewport.set_selected_names(viewport_names)
            # Keep the tree on transform UIDs so the intent matches the panel.
            if viewport_names and viewport_names != names:
                self._tree.set_selected_names(viewport_names)
        finally:
            self._syncing = False
        self._sync_properties(viewport_names)

    def _on_viewport_selection(self, names: list[str]) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._tree.set_selected_names(names)
        finally:
            self._syncing = False
        self._sync_properties(names)


def main() -> int:
    """Show the scene viewport window and run the Qt event loop."""
    app = ensure_application()
    window = SceneViewportWindow()
    window.show()
    return app.exec()
