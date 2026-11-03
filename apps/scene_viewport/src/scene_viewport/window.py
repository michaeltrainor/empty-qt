"""Main window with a scene graph beside a rendercanvas viewport."""

from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

from pydantic import BaseModel
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QToolBar,
    QWidget,
)
from rendercanvas.qt import QRenderWidget

from mtqt import (
    PropertiesBook,
    SceneGraphTree,
    apply_stylesheet,
    ensure_application,
)
from scene_viewport.components import (
    LightComponent,
    LightKind,
    MeshComponent,
    Space,
    TransformComponent,
)
from scene_viewport.gizmo import InteractionTool
from scene_viewport.history import HistoryStep, SceneHistory, SceneObjects
from scene_viewport.mesh.types import PrimitiveKind
from scene_viewport.scene import (
    FOLDER_NAMES,
    build_forest,
    light_components,
    mesh_components,
    transform_components,
)
from scene_viewport.scene_io import SceneDocument, load_scene, save_scene
from scene_viewport.settings import AppSettings, load_settings, save_settings
from scene_viewport.settings_dialog import SettingsDialog
from scene_viewport.viewport import (
    DRAW_MODE_HOTKEYS,
    DrawMode,
    ViewportController,
    attach_viewport,
)
from scene_viewport.world import SceneWorld

_Vec3 = tuple[float, float, float]


class _MeshDefaults(TypedDict):
    color: str
    position: _Vec3
    scale: NotRequired[_Vec3]
    cast_shadow: NotRequired[bool]


class _LightDefaults(TypedDict):
    color: str
    intensity: float
    position: _Vec3
    distance: NotRequired[float]
    decay: NotRequired[float]


_MESH_DEFAULTS: dict[PrimitiveKind, _MeshDefaults] = {
    "box": {"color": "#c47a3a", "position": (0.0, 0.5, 0.0)},
    "plane": {
        "color": "#5a6358",
        "position": (0.0, 0.0, 0.0),
        "scale": (4.0, 1.0, 4.0),
        # Ground planes should receive, not cast — casting causes contact artifacts.
        "cast_shadow": False,
    },
    "sphere": {"color": "#4a8fd4", "position": (0.0, 0.5, 0.0)},
}

_LIGHT_DEFAULTS: dict[LightKind, _LightDefaults] = {
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
        "distance": 16.0,
        "decay": 2.0,
        "position": (2.0, 4.0, 2.0),
    },
}


class SceneViewportWindow(QMainWindow):
    """Tree, pygfx viewport, toolbar, and a tabbed properties book.

    Selection is always a **transform**. Mesh and light shapes map to their
    parent transform. Edit space defaults to **world** (panel + gizmo).

    Maya-aligned tools (toolbar + hotkeys): **Q** select, **W** move,
    **E** rotate, **R** scale; World/Local on the toolbar; **D** edit pivot.
    Shading **1**-**6** (bbox / wire+shaded / flat / wire / shaded / textured).
    **P** parent to last selected, **Shift+P** unparent. Ctrl+D duplicate,
    rename, **F** frame, Ctrl+I isolate. File menu saves/loads the scene.
    Ctrl+Z / Ctrl+Y undo and redo.
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
        self._draw_mode_actions: dict[str, QAction] = {}
        self._pivot_action: QAction | None = None
        self._scene_path: Path | None = None
        self._visibility_backup: dict[str, bool] | None = None

        transforms = transform_components()
        meshes = mesh_components()
        lights = light_components()
        self._world = SceneWorld(transforms, meshes, lights)

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
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("New Scene", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._new_scene)
        file_menu.addAction(new_action)

        open_action = QAction("Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_scene)
        file_menu.addAction(open_action)

        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_scene)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._save_scene_as)
        file_menu.addAction(save_as_action)

        create_menu = self.menuBar().addMenu("&Create")
        create_menu.addAction("Empty Transform", self._create_empty)

        poly = create_menu.addMenu("Polygon Primitives")
        poly.addAction("Cube", lambda: self._create_mesh("box"))
        poly.addAction("Plane", lambda: self._create_mesh("plane"))
        poly.addAction("Sphere", lambda: self._create_mesh("sphere"))

        lights = create_menu.addMenu("Lights")
        lights.addAction("Directional Light", lambda: self._create_light("directional"))
        lights.addAction("Point Light", lambda: self._create_light("point"))
        lights.addAction("Spot Light", lambda: self._create_light("spot"))

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

        duplicate_action = QAction("Duplicate", self)
        duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        duplicate_action.triggered.connect(self._duplicate_selection)
        edit_menu.addAction(duplicate_action)

        rename_action = QAction("Rename...", self)
        rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        rename_action.triggered.connect(self._rename_selection)
        edit_menu.addAction(rename_action)
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

        isolate_action = QAction("Isolate Selection", self)
        isolate_action.setShortcut(QKeySequence("Ctrl+I"))
        isolate_action.triggered.connect(self._isolate_selection)
        edit_menu.addAction(isolate_action)

        show_all_action = QAction("Show All", self)
        show_all_action.setShortcut(QKeySequence("Ctrl+Shift+I"))
        show_all_action.triggered.connect(self._show_all)
        edit_menu.addAction(show_all_action)

        frame_action = QAction("Frame Selection", self)
        frame_action.setShortcut(QKeySequence("F"))
        frame_action.triggered.connect(self._frame_selection)
        edit_menu.addAction(frame_action)
        edit_menu.addSeparator()

        delete_action = QAction("Delete", self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._delete_selection)
        edit_menu.addAction(delete_action)

        # Backspace as a Maya-style alias on macOS (Delete is often Fn-Backspace).
        backspace = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        backspace.activated.connect(self._delete_selection)

        shading_menu = self.menuBar().addMenu("S&hading")
        shade_group = QActionGroup(self)
        shade_group.setExclusive(True)
        for key, mode, label in DRAW_MODE_HOTKEYS:
            action = QAction(f"{label}\t{key}", self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.setToolTip(f"{label} ({key})")

            def on_draw_mode(_checked: bool = False, current: DrawMode = mode) -> None:
                self._set_draw_mode(current)

            action.triggered.connect(on_draw_mode)
            shade_group.addAction(action)
            shading_menu.addAction(action)
            self._draw_mode_actions[mode] = action
        self._draw_mode_actions["shaded"].setChecked(True)

        settings_menu = self.menuBar().addMenu("&Settings")
        prefs = QAction("Preferences...", self)
        prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        prefs.triggered.connect(self._open_preferences)
        settings_menu.addAction(prefs)

    def _set_draw_mode(self, mode: DrawMode) -> None:
        """Apply a viewport draw mode and sync the Shading menu checks."""
        action = self._draw_mode_actions.get(mode)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._viewport.set_draw_mode(mode)

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

            def on_tool(
                _checked: bool = False, current: InteractionTool = tool
            ) -> None:
                self._set_tool(current)

            action.triggered.connect(on_tool)
            tool_group.addAction(action)
            toolbar.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions["select"].setChecked(True)

        toolbar.addSeparator()

        space_group = QActionGroup(self)
        space_group.setExclusive(True)
        spaces: tuple[tuple[Space, str], ...] = (("world", "World"), ("local", "Local"))
        for space, label in spaces:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(f"Edit in {label.lower()} space")

            def on_space(_checked: bool = False, current: Space = space) -> None:
                self._set_edit_space(current)

            action.triggered.connect(on_space)
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
        stored = self._world.transforms.get(name)
        if stored is None or stored.space == space:
            return
        self._world.transforms[name] = stored.model_copy(update={"space": space})
        if refresh_panel and not self._gizmo_dragging:
            self._props.set_pages(self._pages_for_transform(name))

    def _used_names(self) -> set[str]:
        return self._world.used_names()

    def _unique_name(self, stem: str) -> str:
        return self._world.unique_name(stem)

    def _selected_parent(self) -> str:
        names = self._viewport.selected_names
        if len(names) == 1 and names[0] in self._world.transforms:
            return names[0]
        return ""

    def _snapshot_transform(self, name: str) -> TransformComponent | None:
        stored = self._world.transforms.get(name)
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
            self._world.iter_transforms(),
            self._world.iter_meshes(),
            self._world.iter_lights(),
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
        self._world.add_transform(component)
        self._viewport.add_transform(component)

    def _register_mesh(self, component: MeshComponent) -> None:
        self._world.add_mesh(component)
        self._viewport.add_mesh(component)

    def _register_light(self, component: LightComponent) -> None:
        self._world.add_light(component)
        self._viewport.add_light(component)

    def _unregister_transform(self, name: str) -> None:
        for mesh_name in self._world.meshes_on(name):
            self._viewport.remove_mesh(mesh_name)
        for light_name in self._world.lights_on(name):
            self._viewport.remove_light(light_name)
        self._viewport.remove_transform(name)
        self._world.remove_transform(name)

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
            position=defaults["position"],
            scale=defaults.get("scale", (1.0, 1.0, 1.0)),
        )
        mesh = MeshComponent(
            name=shape,
            transform=name,
            kind=kind,
            color=defaults["color"],
            cast_shadow=defaults.get("cast_shadow", True),
            receive_shadow=True,
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
        defaults = _LIGHT_DEFAULTS[kind]
        name = self._unique_name(kind)
        shape = f"{name}Shape"
        transform = TransformComponent(
            name=name,
            parent="",
            space=self._edit_space,
            position=defaults["position"],
        )
        light = LightComponent(
            name=shape,
            transform=name,
            kind=kind,
            color=defaults["color"],
            intensity=defaults["intensity"],
            distance=defaults.get("distance", 0.0),
            decay=defaults.get("decay", 2.0),
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
        return self._world.descendant_transforms(roots)

    def _delete_selection(self) -> None:
        selected = self._names_for_viewport(self._tree.selected_names)
        selected = [name for name in selected if name not in FOLDER_NAMES]
        if not selected:
            return
        # Capture deepest-first removal order; store parents-before-children.
        to_remove = list(reversed(self._descendant_transforms(selected)))
        removed_transforms = [
            self._world.transforms[name].model_copy(deep=True)
            for name in reversed(to_remove)
            if name in self._world.transforms
        ]
        removed_meshes: list[MeshComponent] = []
        removed_lights: list[LightComponent] = []
        for name in to_remove:
            for mesh_name in self._world.meshes_on(name):
                mesh = self._world.meshes.get(mesh_name)
                if mesh is not None:
                    removed_meshes.append(mesh.model_copy(deep=True))
            for light_name in self._world.lights_on(name):
                light = self._world.lights.get(light_name)
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
            if name in self._world.transforms and name not in FOLDER_NAMES
        ]
        if len(names) < 2:
            return
        new_parent = names[-1]
        before: dict[str, TransformComponent] = {}
        after: dict[str, TransformComponent] = {}
        for name in names[:-1]:
            if name == new_parent:
                continue
            stored = self._world.transforms.get(name)
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
            self._world.transforms[name] = updated
        if not after:
            return
        self._push_transform_edits("Parent", before, after)
        self._rebuild_tree(names)

    def _unparent_selection(self) -> None:
        """Move selected transforms to the scene root, preserving world pose."""
        names = [
            name
            for name in self._viewport.selected_names
            if name in self._world.transforms and name not in FOLDER_NAMES
        ]
        if not names:
            return
        before: dict[str, TransformComponent] = {}
        after: dict[str, TransformComponent] = {}
        for name in names:
            stored = self._world.transforms.get(name)
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
            self._world.transforms[name] = updated
        if not after:
            return
        self._push_transform_edits("Unparent", before, after)
        self._rebuild_tree(names)

    def _apply_stored_transform(self, component: TransformComponent) -> None:
        """Write hierarchy + local TRS/pivot from a stored component."""
        self._world.transforms[component.name] = component.model_copy(deep=True)
        self._viewport.set_parent(component.name, component.parent)
        self._viewport.apply_transform_local(component)

    def _document_from_store(self) -> SceneDocument:
        """Build a serializable document from the live component store."""
        return self._world.to_document()

    def _sorted_transforms(
        self,
        transforms: list[TransformComponent],
    ) -> list[TransformComponent]:
        """Order transforms so parents appear before children."""
        by_name = {item.name: item for item in transforms}
        ordered: list[TransformComponent] = []
        seen: set[str] = set()

        def visit(name: str) -> None:
            if name in seen or name not in by_name:
                return
            parent = by_name[name].parent
            if parent:
                visit(parent)
            seen.add(name)
            ordered.append(by_name[name])

        for item in transforms:
            visit(item.name)
        return ordered

    def _replace_scene(self, document: SceneDocument) -> None:
        """Replace the live scene with ``document`` and clear history."""
        for name in list(self._world.transforms):
            self._unregister_transform(name)
        self._visibility_backup = None
        self._history.clear()
        for transform in self._sorted_transforms(document.transforms):
            self._register_transform(transform)
        for mesh in document.meshes:
            self._register_mesh(mesh)
        for light in document.lights:
            self._register_light(light)
        self._rebuild_tree([])

    def _set_scene_path(self, path: Path | None) -> None:
        self._scene_path = path
        title = "Scene viewport"
        if path is not None:
            title = f"Scene viewport — {path.name}"
        self.setWindowTitle(title)

    def _new_scene(self) -> None:
        """Reset to the default demo scene."""
        document = SceneDocument(
            transforms=transform_components(),
            meshes=mesh_components(),
            lights=light_components(),
        )
        self._replace_scene(document)
        self._set_scene_path(None)

    def _open_scene(self) -> None:
        path_str, _filter = QFileDialog.getOpenFileName(
            self,
            "Open Scene",
            str(self._scene_path or Path.home()),
            "Scene JSON (*.json)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            document = load_scene(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Open Scene", f"Could not open:\n{exc}")
            return
        self._replace_scene(document)
        self._set_scene_path(path)

    def _save_scene(self) -> None:
        if self._scene_path is None:
            self._save_scene_as()
            return
        try:
            save_scene(self._document_from_store(), self._scene_path)
        except OSError as exc:
            QMessageBox.warning(self, "Save Scene", f"Could not save:\n{exc}")

    def _save_scene_as(self) -> None:
        path_str, _filter = QFileDialog.getSaveFileName(
            self,
            "Save Scene As",
            str(self._scene_path or Path.home() / "scene.json"),
            "Scene JSON (*.json)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            save_scene(self._document_from_store(), path)
        except OSError as exc:
            QMessageBox.warning(self, "Save Scene", f"Could not save:\n{exc}")
            return
        self._set_scene_path(path)

    def _selection_roots(self) -> list[str]:
        """Selected transforms excluding those under another selected transform."""
        selected = [
            name
            for name in self._viewport.selected_names
            if name in self._world.transforms and name not in FOLDER_NAMES
        ]
        selected_set = set(selected)
        roots: list[str] = []
        for name in selected:
            parent = self._world.transforms[name].parent
            under_selected = False
            while parent:
                if parent in selected_set:
                    under_selected = True
                    break
                ancestor = self._world.transforms.get(parent)
                parent = ancestor.parent if ancestor is not None else ""
            if not under_selected:
                roots.append(name)
        return roots

    def _duplicate_selection(self) -> None:
        """Duplicate selected roots (with descendants) and offset them."""
        roots = self._selection_roots()
        if not roots:
            return
        to_copy = self._descendant_transforms(roots)
        name_map: dict[str, str] = {}
        used = self._used_names()
        for name in to_copy:
            stem = name
            candidate = stem
            index = 1
            while candidate in used or candidate in name_map.values():
                candidate = f"{stem}{index}"
                index += 1
            name_map[name] = candidate
            used.add(candidate)

        added_transforms: list[TransformComponent] = []
        added_meshes: list[MeshComponent] = []
        added_lights: list[LightComponent] = []
        for name in to_copy:
            src = self._world.transforms[name]
            new_parent = name_map.get(src.parent, src.parent)
            position = src.position
            if name in roots:
                position = (position[0] + 1.0, position[1], position[2])
            new_transform = src.model_copy(
                update={
                    "name": name_map[name],
                    "parent": new_parent,
                    "position": position,
                }
            )
            self._register_transform(new_transform)
            added_transforms.append(new_transform.model_copy(deep=True))
            for mesh_name in self._world.meshes_on(name):
                mesh = self._world.meshes[mesh_name]
                new_mesh_name = self._unique_name(f"{name_map[name]}Shape")
                new_mesh = mesh.model_copy(
                    update={"name": new_mesh_name, "transform": name_map[name]}
                )
                self._register_mesh(new_mesh)
                added_meshes.append(new_mesh.model_copy(deep=True))
            for light_name in self._world.lights_on(name):
                light = self._world.lights[light_name]
                new_light_name = self._unique_name(f"{name_map[name]}Shape")
                new_light = light.model_copy(
                    update={"name": new_light_name, "transform": name_map[name]}
                )
                self._register_light(new_light)
                added_lights.append(new_light.model_copy(deep=True))

        self._history.push(
            HistoryStep(
                label="Duplicate",
                added=SceneObjects(
                    transforms=added_transforms,
                    meshes=added_meshes,
                    lights=added_lights,
                ),
            )
        )
        self._rebuild_tree([name_map[name] for name in roots])

    def _rename_transform(self, old_name: str, new_name: str) -> None:
        """Rename a transform UID and update references."""
        self._viewport.rename_transform(old_name, new_name)
        self._world.rename_transform(old_name, new_name)

    def _rename_selection(self) -> None:
        names = [
            name
            for name in self._viewport.selected_names
            if name in self._world.transforms and name not in FOLDER_NAMES
        ]
        if len(names) != 1:
            return
        old_name = names[0]
        text, accepted = QInputDialog.getText(
            self,
            "Rename",
            "Transform name:",
            text=old_name,
        )
        new_name = text.strip()
        if not accepted or not new_name or new_name == old_name:
            return
        if new_name in self._used_names():
            QMessageBox.warning(
                self,
                "Rename",
                f"Name {new_name!r} is already used.",
            )
            return
        self._rename_transform(old_name, new_name)
        self._history.push(HistoryStep(label="Rename", renames={old_name: new_name}))
        self._rebuild_tree([new_name])

    def _set_visibility(self, updates: dict[str, bool], *, label: str) -> None:
        before: dict[str, TransformComponent] = {}
        after: dict[str, TransformComponent] = {}
        for name, visible in updates.items():
            stored = self._world.transforms.get(name)
            if stored is None or stored.visible == visible:
                continue
            before[name] = stored.model_copy(deep=True)
            updated = stored.model_copy(update={"visible": visible})
            after[name] = updated
            self._world.transforms[name] = updated
            self._viewport.set_visible(name, visible)
        self._push_transform_edits(label, before, after)
        selected = self._viewport.selected_names
        if len(selected) == 1 and selected[0] in after:
            self._props.set_pages(self._pages_for_transform(selected[0]))

    def _isolate_selection(self) -> None:
        selected = set(self._selection_roots())
        if not selected:
            return
        keep = set(self._descendant_transforms(sorted(selected)))
        if self._visibility_backup is None:
            self._visibility_backup = {
                name: item.visible for name, item in self._world.transforms.items()
            }
        updates = {name: name in keep for name in self._world.transforms}
        self._set_visibility(updates, label="Isolate")

    def _show_all(self) -> None:
        if self._visibility_backup is not None:
            updates = dict(self._visibility_backup)
            self._visibility_backup = None
        else:
            updates = dict.fromkeys(self._world.transforms, True)
        self._set_visibility(updates, label="Show All")

    def _frame_selection(self) -> None:
        names = self._viewport.selected_names
        self._viewport.frame_selection(names or None)

    def _restore_objects(self, objects: SceneObjects) -> None:
        # Parents before children: transforms list is stored that way on delete.
        for transform in objects.transforms:
            if transform.name not in self._world.transforms:
                self._register_transform(transform)
            else:
                self._apply_stored_transform(transform)
        for mesh in objects.meshes:
            if mesh.name not in self._world.meshes:
                self._register_mesh(mesh)
            else:
                self._world.meshes[mesh.name] = mesh
                self._viewport.apply_mesh(mesh)
        for light in objects.lights:
            if light.name not in self._world.lights:
                self._register_light(light)
            else:
                self._world.lights[light.name] = light
                self._viewport.apply_light(light)

    def _remove_objects(self, objects: SceneObjects) -> None:
        names = [item.name for item in objects.transforms]
        # Remove deepest first.
        for name in reversed(self._descendant_transforms(names)):
            if name in self._world.transforms:
                self._unregister_transform(name)

    def _apply_history_step(self, step: HistoryStep, *, undo: bool) -> None:
        if undo:
            for old_name, new_name in step.renames.items():
                if new_name in self._world.transforms:
                    self._rename_transform(new_name, old_name)
            if step.added is not None:
                self._remove_objects(step.added)
            if step.removed is not None:
                self._restore_objects(step.removed)
            for component in step.transforms_before.values():
                if component.name in self._world.transforms:
                    self._apply_stored_transform(component)
        else:
            if step.removed is not None:
                self._remove_objects(step.removed)
            if step.added is not None:
                self._restore_objects(step.added)
            for old_name, new_name in step.renames.items():
                if old_name in self._world.transforms:
                    self._rename_transform(old_name, new_name)
            for component in step.transforms_after.values():
                if component.name in self._world.transforms:
                    self._apply_stored_transform(component)
        select = list(step.transforms_after or step.transforms_before)
        if step.renames:
            select = list(step.renames.keys() if undo else step.renames.values())
        if step.added is not None and not undo:
            select = [item.name for item in step.added.transforms]
        if step.removed is not None and undo:
            select = [item.name for item in step.removed.transforms]
        self._rebuild_tree([name for name in select if name in self._world.transforms])

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
        return self._world.resolve_transform(name)

    def _is_prop_editable(self, field_name: str, model: BaseModel) -> bool:
        if isinstance(model, TransformComponent):
            return field_name in {
                "space",
                "position",
                "rotation",
                "scale",
                "pivot",
                "visible",
            }
        if isinstance(model, MeshComponent):
            return field_name in {
                "color",
                "roughness",
                "metalness",
                "albedo_map",
                "cast_shadow",
                "receive_shadow",
            }
        if isinstance(model, LightComponent):
            shared = {"color", "intensity", "cast_shadow"}
            if model.kind in {"point", "spot"}:
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
        stored = self._world.transforms[name]
        pages: list[tuple[str, BaseModel]] = [
            ("Transform", self._panel_transform(stored)),
        ]
        for mesh_name in self._world.meshes_on(name):
            pages.append(("Mesh", self._world.meshes[mesh_name]))
        for light_name in self._world.lights_on(name):
            pages.append(("Light", self._world.lights[light_name]))
        return pages

    def _on_prop_field(
        self,
        field_name: str,
        value: object,
        model: BaseModel,
    ) -> None:
        if isinstance(model, MeshComponent):
            self._world.meshes[model.name] = model
            self._viewport.apply_mesh(model)
            return
        if isinstance(model, LightComponent):
            self._world.lights[model.name] = model
            self._viewport.apply_light(model)
            return
        if not isinstance(model, TransformComponent):
            return

        stored = self._world.transforms[model.name]
        if field_name == "space":
            self._set_edit_space(model.space, refresh_panel=True)
            return

        before = {model.name: stored.model_copy(deep=True)}
        if field_name == "pivot":
            updated = stored.model_copy(update={"pivot": model.pivot})
            self._world.transforms[model.name] = updated
            self._viewport.set_pivot(model.name, model.pivot)
            self._push_transform_edits(
                "Edit Pivot",
                before,
                {model.name: updated.model_copy(deep=True)},
            )
            self._props.set_pages(self._pages_for_transform(model.name))
            return

        if field_name == "visible":
            self._set_visibility({model.name: model.visible}, label="Visibility")
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
        self._world.transforms[model.name] = updated
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
        if name not in self._world.transforms:
            self._props.clear()
            return
        # Keep panel space aligned with the session edit space (default world).
        stored = self._world.transforms[name]
        if stored.space != self._edit_space:
            self._world.transforms[name] = stored.model_copy(
                update={"space": self._edit_space}
            )
        self._viewport.set_gizmo_space(self._edit_space)
        self._props.set_pages(self._pages_for_transform(name))

    def _store_from_viewport(self, names: list[str]) -> None:
        for name in names:
            if name not in self._world.transforms:
                continue
            position, rotation, scale = self._viewport.read_local_trs(name)
            pivot = self._viewport.read_pivot(name)
            stored = self._world.transforms[name]
            self._world.transforms[name] = stored.model_copy(
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
