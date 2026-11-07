"""Main window with a scene graph beside a rendercanvas viewport."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict

from pydantic import BaseModel
from PySide6.QtCore import QEvent, QKeyCombination, QMimeData, QObject, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from rendercanvas.qt import QRenderWidget

from mtqt import (
    PropertiesBook,
    SceneGraphTree,
    TimelineBar,
    apply_stylesheet,
    ensure_application,
)
from scene_viewport.animation import AnimationClip
from scene_viewport.components import (
    CameraComponent,
    CameraKind,
    LightComponent,
    LightKind,
    MeshComponent,
    Space,
    TransformComponent,
)
from scene_viewport.export_gltf import export_scene
from scene_viewport.gizmo import InteractionTool
from scene_viewport.history import HistoryStep, SceneHistory, SceneObjects
from scene_viewport.mesh.importer import (
    IMPORT_SUFFIXES,
    ModelImportError,
    importable_paths,
    is_importable,
    load_model,
)
from scene_viewport.mesh.types import PrimitiveKind
from scene_viewport.model_import import (
    ImportResult,
    import_conversion,
    import_objects,
)
from scene_viewport.navigation import NAVIGATION_HINTS
from scene_viewport.playback import PlaybackController
from scene_viewport.recent_files import RecentFiles
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
    ViewPreset,
    attach_viewport,
)
from scene_viewport.world import SceneWorld, prune_clip
from scene_viewport.xform_math import look_at_trs

_log = logging.getLogger(__name__)

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
    **P** parent to last selected, **Shift+P** unparent, or drag rows in the
    tree to reparent and reorder (world pose kept). Ctrl+D duplicate,
    rename, **F** frame selected, **A** frame all, Ctrl+I isolate, Ctrl+H
    hide. Numpad **7** / **1** / **3** / **5** switch to top / front / side /
    perspective. **X** toggles snapping (hold Ctrl mid-drag to invert).
    Left-drag box-selects (Shift / Ctrl / Ctrl+Shift as for clicks).
    Camera bindings follow the Maya, Blender, or pygfx preset in
    Preferences. File → Open takes a scene or a model file (a model opens as
    a new, untitled scene), Open Recent lists both, and New / Open / Quit
    ask before dropping unsaved changes. Ctrl+Z / Ctrl+Y undo and redo.
    A scene with animation clips shows a timeline bar under the viewport;
    **Space** plays and pauses. Playback poses the viewport only: the
    stored transforms stay the rest pose until Stop restores it.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        recent_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scene viewport")
        self.resize(1200, 640)
        self._syncing = False
        self._gizmo_dragging = False
        self._history = SceneHistory()
        self._recent_path = recent_path
        self._recent = RecentFiles.load(recent_path)
        self._recent_menu: QMenu | None = None
        # Snapshot of the scene as last opened or saved, for the discard prompt.
        self._saved_document: SceneDocument | None = None
        self._import_problems: list[str] = []
        self._gizmo_before: dict[str, TransformComponent] = {}
        self._settings: AppSettings = load_settings()
        self._edit_space: Space = self._settings.default_edit_space
        self._tool_actions: dict[InteractionTool, QAction] = {}
        self._space_actions: dict[Space, QAction] = {}
        self._draw_mode_actions: dict[str, QAction] = {}
        self._pivot_action: QAction | None = None
        self._grid_action: QAction | None = None
        self._look_through_action: QAction | None = None
        self._play_action: QAction | None = None
        self._nav_label: QLabel | None = None
        self._scene_path: Path | None = None
        self._import_dir: Path | None = None
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
            drag_drop=True,
        )
        self._tree.setMinimumWidth(200)
        self._tree.set_move_filter(self._accept_tree_move)
        self._tree.nodesMoved.connect(self._on_tree_nodes_moved)
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
        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(splitter, stretch=1)
        self._timeline = TimelineBar(central)
        self._timeline.hide()
        central_layout.addWidget(self._timeline)
        self.setCentralWidget(central)
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
        self._playback = PlaybackController(
            self._world, self._viewport, self._timeline, parent=self
        )
        self._playback.stateChanged.connect(self._update_status)
        self._playback.poseSettled.connect(self._on_pose_settled)
        self._tree.selectedNamesChanged.connect(self._on_tree_selection)

        self._props.set_editable_check(self._is_prop_editable)
        self._props.register_handler(self._on_prop_field)
        self._props.fieldPreviewed.connect(self._on_prop_preview)
        self._install_menus()
        # Model files dropped anywhere on the window are imported. The tree
        # accepts drops for its own rows, so it would swallow file drags;
        # catch those first and treat them like a drop on the window.
        self.setAcceptDrops(True)
        tree_viewport = self._tree.view.viewport()
        if tree_viewport is not None:
            tree_viewport.installEventFilter(self)
        self._install_toolbar()
        self._install_status_bar()
        # Edits to the startup scene deserve the discard prompt too.
        self._saved_document = self._world.to_document()

    def _install_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("New Scene", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._new_scene)
        file_menu.addAction(new_action)

        open_action = QAction("Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_dialog)
        file_menu.addAction(open_action)

        self._recent_menu = file_menu.addMenu("Open Recent")
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        self._rebuild_recent_menu()

        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_scene)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._save_scene_as)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()

        import_action = QAction("Import...", self)
        import_action.triggered.connect(self._import_dialog)
        file_menu.addAction(import_action)

        export_action = QAction("Export Scene...", self)
        export_action.triggered.connect(lambda: self._export_dialog(selection=False))
        file_menu.addAction(export_action)

        export_selection_action = QAction("Export Selection...", self)
        export_selection_action.triggered.connect(
            lambda: self._export_dialog(selection=True)
        )
        file_menu.addAction(export_selection_action)

        reload_action = QAction("Reload Changed Files", self)
        reload_action.triggered.connect(self._reload_changed_files)
        file_menu.addAction(reload_action)

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

        cameras = create_menu.addMenu("Cameras")
        cameras.addAction(
            "Perspective Camera", lambda: self._create_camera("perspective")
        )
        cameras.addAction(
            "Orthographic Camera", lambda: self._create_camera("orthographic")
        )

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

        select_all_action = QAction("Select All", self)
        select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_action.triggered.connect(self._select_all)
        edit_menu.addAction(select_all_action)

        deselect_action = QAction("Deselect All", self)
        # Blender's Alt+A. Not Esc: a window Esc shortcut would steal the
        # key from PropertiesPanel, where Esc cancels a label scrub.
        deselect_action.setShortcut(QKeySequence("Alt+A"))
        deselect_action.triggered.connect(lambda: self._select_names([]))
        edit_menu.addAction(deselect_action)
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

        hide_action = QAction("Hide Selection", self)
        hide_action.setShortcut(QKeySequence("Ctrl+H"))
        hide_action.triggered.connect(self._hide_selection)
        edit_menu.addAction(hide_action)

        isolate_action = QAction("Isolate Selection", self)
        isolate_action.setShortcut(QKeySequence("Ctrl+I"))
        isolate_action.triggered.connect(self._isolate_selection)
        edit_menu.addAction(isolate_action)

        show_all_action = QAction("Show All", self)
        show_all_action.setShortcuts(
            [QKeySequence("Ctrl+Shift+I"), QKeySequence("Ctrl+Shift+H")]
        )
        show_all_action.triggered.connect(self._show_all)
        edit_menu.addAction(show_all_action)
        edit_menu.addSeparator()

        delete_action = QAction("Delete", self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._delete_selection)
        edit_menu.addAction(delete_action)

        # Backspace as a Maya-style alias on macOS (Delete is often Fn-Backspace).
        backspace = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        backspace.activated.connect(self._delete_selection)

        view_menu = self.menuBar().addMenu("&View")
        frame_action = QAction("Frame Selection", self)
        frame_action.setShortcut(QKeySequence("F"))
        frame_action.triggered.connect(self._frame_selection)
        view_menu.addAction(frame_action)

        frame_all_action = QAction("Frame All", self)
        frame_all_action.setShortcuts([QKeySequence("A"), QKeySequence("Home")])
        frame_all_action.triggered.connect(lambda: self._viewport.frame_selection([]))
        view_menu.addAction(frame_all_action)
        view_menu.addSeparator()

        self._look_through_action = QAction("Look Through Camera", self)
        self._look_through_action.setCheckable(True)
        self._look_through_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self._look_through_action.setStatusTip(
            "Render from the selected camera; toggle again for the free camera"
        )
        self._look_through_action.triggered.connect(self._toggle_look_through)
        view_menu.addAction(self._look_through_action)
        view_menu.addSeparator()

        # Blender numpad layout; menu entries cover keyboards without one.
        view_specs: list[tuple[ViewPreset, str, Qt.Key]] = [
            ("perspective", "Perspective", Qt.Key.Key_5),
            ("top", "Top", Qt.Key.Key_7),
            ("front", "Front", Qt.Key.Key_1),
            ("side", "Side", Qt.Key.Key_3),
        ]
        for view, label, key in view_specs:
            action = QAction(label, self)
            action.setShortcut(
                QKeySequence(QKeyCombination(Qt.KeyboardModifier.KeypadModifier, key))
            )

            def on_view(_checked: bool = False, current: ViewPreset = view) -> None:
                self._viewport.set_view(current)

            action.triggered.connect(on_view)
            view_menu.addAction(action)
        view_menu.addSeparator()

        grid_action = QAction("Show Grid", self)
        grid_action.setCheckable(True)
        grid_action.setChecked(self._settings.show_grid)
        grid_action.toggled.connect(self._set_show_grid)
        view_menu.addAction(grid_action)
        self._grid_action = grid_action

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

        animation_menu = self.menuBar().addMenu("&Animation")
        play_action = QAction("Play / Pause", self)
        play_action.setShortcut(QKeySequence(Qt.Key.Key_Space))
        play_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        play_action.setStatusTip("Play or pause the current animation clip")
        play_action.triggered.connect(self._playback.toggle_play)
        play_action.setEnabled(False)
        animation_menu.addAction(play_action)
        self._play_action = play_action
        stop_action = QAction("Stop", self)
        stop_action.setStatusTip("Rewind and restore the rest pose")
        stop_action.triggered.connect(self._playback.stop)
        animation_menu.addAction(stop_action)

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
        if self._grid_action is not None:
            self._grid_action.blockSignals(True)
            self._grid_action.setChecked(self._settings.show_grid)
            self._grid_action.blockSignals(False)
        self._update_status()

    def _set_show_grid(self, visible: bool) -> None:
        """Toggle the ground grid and persist it like Preferences does."""
        if visible == self._settings.show_grid:
            return
        self._settings = self._settings.model_copy(update={"show_grid": visible})
        save_settings(self._settings)
        self._viewport.apply_settings(self._settings)

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

        snap_action = QAction("Snap", self)
        snap_action.setCheckable(True)
        snap_action.setShortcut(QKeySequence("X"))
        snap_action.setToolTip(
            "Snap move/rotate to the Preferences increments (X). "
            "Hold Ctrl during a drag to invert."
        )
        snap_action.toggled.connect(self._set_snap)
        toolbar.addAction(snap_action)

    def _install_status_bar(self) -> None:
        status = self.statusBar()
        self._nav_label = QLabel(status)
        status.addPermanentWidget(self._nav_label)
        self._update_status()

    def _update_status(self) -> None:
        """Show selection, tool, snap state, and the camera bindings."""
        if self._nav_label is not None:
            self._nav_label.setText(NAVIGATION_HINTS[self._settings.navigation_style])
        names = self._viewport.selected_names
        if not names:
            selection = "Nothing selected"
        elif len(names) == 1:
            selection = names[0]
        else:
            selection = f"{len(names)} selected (primary {names[-1]})"
        parts = [selection, self._viewport.interaction_tool.capitalize()]
        if self._viewport.snap_enabled:
            parts.append("Snap on")
        looking = self._viewport.looking_through
        if looking is not None:
            parts.append(f"Looking through {looking} (Ctrl+Shift+L exits)")
        playback = self._playback.status_text()
        if playback is not None:
            parts.append(playback)
        self._sync_look_through_action()
        self.statusBar().showMessage(" · ".join(parts))

    def _set_snap(self, enabled: bool) -> None:
        self._viewport.set_snap_enabled(enabled)
        self._update_status()

    def _set_tool(self, tool: InteractionTool) -> None:
        action = self._tool_actions.get(tool)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._viewport.set_interaction_tool(tool)
        self._update_status()

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
        self._refresh_playback()
        forest = build_forest(
            self._world.iter_transforms(),
            self._world.iter_meshes(),
            self._world.iter_lights(),
            self._world.iter_cameras(),
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

    def _refresh_playback(self) -> None:
        """Re-read clips after a scene change and enable the Play action."""
        self._playback.refresh()
        if self._play_action is not None:
            self._play_action.setEnabled(bool(self._world.animations))

    def _on_pose_settled(self) -> None:
        """Refresh the panel once a pose stops moving, so it shows the viewport."""
        self._sync_properties(self._viewport.selected_names)

    def _stop_playback_for_edit(self, names: Iterable[str]) -> bool:
        """Stop playback before an edit that reads animated transforms.

        Edits read the viewport, so a displayed pose would become the new
        rest pose. Returns ``True`` when playback was stopped.
        """
        if not self._playback.stop_for_edit(names):
            return False
        self.statusBar().showMessage(
            "Playback stopped and the rest pose restored before the edit", 4000
        )
        return True

    def _register_animation(self, clip: AnimationClip) -> None:
        self._world.add_animation(clip)

    def _register_transform(self, component: TransformComponent) -> None:
        self._world.add_transform(component)
        self._viewport.add_transform(component)

    def _register_mesh(self, component: MeshComponent) -> None:
        self._world.add_mesh(component)
        self._viewport.add_mesh(component)

    def _register_light(self, component: LightComponent) -> None:
        self._world.add_light(component)
        self._viewport.add_light(component)

    def _register_camera(self, component: CameraComponent) -> None:
        self._world.add_camera(component)
        self._viewport.add_camera(component)

    def _unregister_transform(self, name: str) -> None:
        for mesh_name in self._world.meshes_on(name):
            self._viewport.remove_mesh(mesh_name)
        for light_name in self._world.lights_on(name):
            self._viewport.remove_light(light_name)
        for camera_name in self._world.cameras_on(name):
            self._viewport.remove_camera(camera_name)
        self._viewport.remove_transform(name)
        self._world.remove_transform(name)

    def _create_camera(self, kind: CameraKind) -> None:
        """Add a camera on a new transform, aimed at the scene from the front."""
        name = self._unique_name("camera")
        shape = f"{name}Shape"
        position, rotation, _scale = look_at_trs((5.5, 4.0, 6.5), (0.0, 0.4, 0.0))
        transform = TransformComponent(
            name=name,
            parent=self._selected_parent(),
            space=self._edit_space,
            position=position,
            rotation=rotation,
        )
        camera = CameraComponent(name=shape, transform=name, kind=kind)
        self._register_transform(transform)
        self._register_camera(camera)
        self._history.push(
            HistoryStep(
                label=f"Create {kind} camera",
                added=SceneObjects(
                    transforms=[transform.model_copy(deep=True)],
                    cameras=[camera.model_copy(deep=True)],
                ),
            )
        )
        self._rebuild_tree([name])

    def _selected_camera(self) -> str | None:
        """Return the camera on the primary selected transform, if any."""
        for name in reversed(self._viewport.selected_names):
            cameras = self._world.cameras_on(name)
            if cameras:
                return cameras[0]
        return None

    def _toggle_look_through(self, checked: bool) -> None:
        """Look through the selected camera, or return to the free camera."""
        if not checked:
            self._viewport.look_through(None)
        else:
            camera = self._selected_camera()
            if camera is None:
                action = self._look_through_action
                if action is not None:
                    action.setChecked(False)
                self.statusBar().showMessage("Select a camera to look through", 4000)
                return
            self._viewport.look_through(camera)
        self._update_status()

    def _sync_look_through_action(self) -> None:
        """Keep the View menu toggle in step with the viewport."""
        if self._look_through_action is None:
            return
        looking = self._viewport.looking_through is not None
        if self._look_through_action.isChecked() != looking:
            self._look_through_action.blockSignals(True)
            self._look_through_action.setChecked(looking)
            self._look_through_action.blockSignals(False)

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
        removed_cameras: list[CameraComponent] = []
        clip_names = dict.fromkeys(
            clip
            for name in to_remove
            for clip in self._world.animations_targeting(name)
        )
        removed_animations = [
            self._world.animations[clip].model_copy(deep=True) for clip in clip_names
        ]
        for name in to_remove:
            for mesh_name in self._world.meshes_on(name):
                mesh = self._world.meshes.get(mesh_name)
                if mesh is not None:
                    removed_meshes.append(mesh.model_copy(deep=True))
            for light_name in self._world.lights_on(name):
                light = self._world.lights.get(light_name)
                if light is not None:
                    removed_lights.append(light.model_copy(deep=True))
            for camera_name in self._world.cameras_on(name):
                camera = self._world.cameras.get(camera_name)
                if camera is not None:
                    removed_cameras.append(camera.model_copy(deep=True))
            self._unregister_transform(name)
        self._history.push(
            HistoryStep(
                label="Delete",
                removed=SceneObjects(
                    transforms=removed_transforms,
                    meshes=removed_meshes,
                    lights=removed_lights,
                    cameras=removed_cameras,
                    animations=removed_animations,
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
            self._reparent_keep_world(name, new_parent, before, after)
        if not after:
            return
        self._push_transform_edits("Parent", before, after)
        self._rebuild_tree(names)

    def _reparent_keep_world(
        self,
        name: str,
        new_parent: str,
        before: dict[str, TransformComponent],
        after: dict[str, TransformComponent],
    ) -> bool:
        """Reparent ``name`` in the store and viewport, keeping its world pose.

        Records the old and new components in ``before`` / ``after`` for an
        undo step. Returns ``False`` when nothing changed: the transform is
        missing, already under ``new_parent``, or the move is invalid.
        """
        stored = self._world.transforms.get(name)
        if stored is None or stored.parent == new_parent:
            return False
        self._stop_playback_for_edit([name])
        snap = self._snapshot_transform(name)
        if snap is None:
            return False
        local = self._viewport.reparent_transform(name, new_parent)
        if local is None:
            return False
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
        return True

    def _accept_tree_move(self, names: Sequence[str], parent: str, _row: int) -> bool:
        """Veto outliner drags that don't map onto the transform hierarchy.

        Only transforms move: folders and shape leaves stay put. They can
        land on another transform, or on the ``geo`` / ``lgt`` folder for the
        scene root, where lights go under ``lgt`` and everything else under
        ``geo``, as :func:`build_forest` would place them.
        """
        transforms = self._world.transforms
        if not names or any(name not in transforms for name in names):
            return False
        if parent == "lgt":
            return all(self._world.lights_on(name) for name in names)
        if parent == "cam":
            return all(
                self._world.cameras_on(name) and not self._world.lights_on(name)
                for name in names
            )
        if parent == "geo":
            return not any(
                self._world.lights_on(name) or self._world.cameras_on(name)
                for name in names
            )
        return parent in transforms

    def _tree_transform_order(self) -> list[str]:
        """Return every transform UID in the outliner's top-down order."""
        order: list[str] = []
        transforms = self._world.transforms

        def walk(node: object) -> None:
            if not isinstance(node, list) or not node:
                return
            name = node[0]
            if isinstance(name, str) and name in transforms:
                order.append(name)
            for child in node[1:]:
                walk(child)

        for root in self._tree.model.to_lists():
            walk(root)
        return order

    def _on_tree_nodes_moved(self, names: list[str], parent: str, _row: int) -> None:
        """Mirror an outliner drag into the store, viewport, and history."""
        new_parent = "" if parent in FOLDER_NAMES else parent
        moved = [name for name in names if name in self._world.transforms]
        if not moved:
            QTimer.singleShot(0, lambda: self._rebuild_tree([]))
            return
        order_before = list(self._world.transforms)
        before: dict[str, TransformComponent] = {}
        after: dict[str, TransformComponent] = {}
        for name in moved:
            if not self._reparent_keep_world(name, new_parent, before, after):
                snap = self._snapshot_transform(name)
                if snap is not None:
                    before[name] = snap
                    after[name] = snap.model_copy(deep=True)
        self._world.reorder_transforms(self._tree_transform_order())
        order_after = list(self._world.transforms)
        if before != after or order_before != order_after:
            self._history.push(
                HistoryStep(
                    label="Move",
                    transforms_before=before,
                    transforms_after=after,
                    order_before=order_before,
                    order_after=order_after,
                )
            )
        # The tree is mid-drop; rebuild it from the store once that finishes.
        QTimer.singleShot(0, lambda: self._rebuild_tree(moved))

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
            self._reparent_keep_world(name, "", before, after)
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
        self._playback.stop()
        for name in list(self._world.transforms):
            self._unregister_transform(name)
        # Report only the files the new scene fails to load.
        self._viewport.take_missing_sources()
        self._visibility_backup = None
        self._history.clear()
        for transform in self._sorted_transforms(document.transforms):
            self._register_transform(transform)
        for mesh in document.meshes:
            self._register_mesh(mesh)
        for light in document.lights:
            self._register_light(light)
        for camera in document.cameras:
            self._register_camera(camera)
        for clip in document.animations:
            pruned = prune_clip(clip, self._world.transforms)
            if pruned is not None and pruned.name not in self._world.animations:
                self._register_animation(pruned)
        self._rebuild_tree([])
        self._saved_document = self._world.to_document()

    def _set_scene_path(self, path: Path | None, *, display: str = "") -> None:
        """Record the scene file and show its name (or ``display``) in the title."""
        self._scene_path = path
        name = path.name if path is not None else display
        self.setWindowTitle(f"Scene viewport — {name}" if name else "Scene viewport")

    def _default_document(self) -> SceneDocument:
        return SceneDocument(
            transforms=transform_components(),
            meshes=mesh_components(),
            lights=light_components(),
        )

    def _new_scene(self) -> None:
        """Reset to the default demo scene."""
        if not self._confirm_discard("New Scene"):
            return
        self._replace_scene(self._default_document())
        self._set_scene_path(None)

    def _has_unsaved_changes(self) -> bool:
        """Whether the scene differs from what was last opened or saved."""
        if self._saved_document is None:
            return False
        return self._world.to_document() != self._saved_document

    def _confirm_discard(self, title: str) -> bool:
        """Ask to save, discard, or cancel when the scene has unsaved changes.

        Returns:
            ``True`` when the caller may replace the scene.
        """
        if not self._has_unsaved_changes():
            return True
        name = self._scene_path.name if self._scene_path is not None else "this scene"
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Save changes to {name}?")
        box.setInformativeText("Unsaved changes will be lost.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Discard:
            return True
        if answer == QMessageBox.StandardButton.Save:
            self._save_scene()
            return not self._has_unsaved_changes()
        return False

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard("Quit"):
            self._playback.stop()
            event.accept()
        else:
            event.ignore()

    def _open_dialog(self) -> None:
        """Pick a scene or model file to open as the current scene."""
        models = " ".join(f"*{suffix}" for suffix in IMPORT_SUFFIXES)
        start = self._scene_path or self._import_dir or Path.home()
        path_str, _filter = QFileDialog.getOpenFileName(
            self,
            "Open",
            str(start),
            f"Scenes and models (*.json {models});;Scene JSON (*.json);;"
            f"3D models ({models})",
        )
        if not path_str:
            return
        self.open_path(Path(path_str))

    def open_path(self, path: Path) -> bool:
        """Open a scene file, or a model file as a new scene.

        A ``.json`` scene replaces the current scene. A model file (see
        :data:`IMPORT_SUFFIXES`) starts from the default scene, imports the
        model, and leaves the scene untitled, so Save asks where to put it.
        Unsaved changes prompt first.

        Args:
            path: Scene or model file.

        Returns:
            ``True`` when the file was opened.
        """
        if not self._confirm_discard("Open"):
            return False
        if path.suffix.lower() == ".json":
            try:
                document = load_scene(path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Open Scene", f"Could not open:\n{exc}")
                self._forget_recent(path)
                return False
            self._replace_scene(document)
            self._set_scene_path(path)
            self._remember_recent(path)
            self._report_open_problems("Open Scene", [])
            return True
        if not is_importable(path):
            QMessageBox.warning(self, "Open", f"Unsupported file type:\n{path.name}")
            return False
        if not path.is_file():
            QMessageBox.warning(self, "Open", f"File not found:\n{path}")
            self._forget_recent(path)
            return False
        try:
            # Read the file before touching the current scene, so a broken
            # model leaves the user's work in place. The import below hits
            # the parse cache.
            load_model(path)
        except ModelImportError as exc:
            QMessageBox.warning(self, "Open", f"Could not open:\n{exc}")
            self._forget_recent(path)
            return False
        self._replace_scene(self._default_document())
        self._set_scene_path(None, display=path.name)
        self._import_dir = path.parent
        roots = self._import_files([path], report=False)
        problems, self._import_problems = self._import_problems, []
        if not roots:
            self._set_scene_path(None)
            self._report_open_problems(
                "Open", problems or [f"Could not open:\n{path.name}"]
            )
            self._forget_recent(path)
            return False
        # The imported model is the document, not an edit to undo.
        self._history.clear()
        self._saved_document = self._world.to_document()
        self._report_open_problems("Open", problems)
        return True

    def _report_open_problems(self, title: str, problems: list[str]) -> None:
        """Warn once about model files or textures that failed to load."""
        problems = list(problems)
        missing = self._viewport.take_missing_sources()
        if missing:
            listed = "\n".join(str(source) for source in missing)
            problems.append(
                "These model files could not be loaded and are shown as boxes:\n"
                + listed
            )
        if title == "Open Scene":
            warnings = self._source_warnings()
            if warnings:
                problems.append(
                    "Some files these models use could not be loaded; those parts "
                    "show the material color:\n" + "\n".join(warnings)
                )
        if problems:
            QMessageBox.warning(self, title, "\n\n".join(problems))

    def _remember_recent(self, path: Path) -> None:
        self._recent.add(path)
        self._save_recent()

    def _forget_recent(self, path: Path) -> None:
        self._recent.remove(path)
        self._save_recent()

    def _save_recent(self) -> None:
        try:
            self._recent.save(self._recent_path)
        except OSError as exc:
            _log.warning("could not save recent files: %s", exc)

    def _rebuild_recent_menu(self) -> None:
        menu = self._recent_menu
        if menu is None:
            return
        menu.clear()
        paths = self._recent.paths
        for index, path in enumerate(paths, start=1):
            prefix = f"&{index} " if index < 10 else ""
            action = menu.addAction(f"{prefix}{path.name}")
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.triggered.connect(lambda _checked=False, p=path: self.open_path(p))
        if paths:
            menu.addSeparator()
        clear_action = menu.addAction("Clear Recent Files")
        clear_action.setEnabled(bool(paths))
        clear_action.triggered.connect(self._clear_recent)

    def _clear_recent(self) -> None:
        self._recent.clear()
        self._save_recent()

    def _import_dialog(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in IMPORT_SUFFIXES)
        start = self._import_dir or (
            self._scene_path.parent if self._scene_path is not None else Path.home()
        )
        path_strs, _filter = QFileDialog.getOpenFileNames(
            self,
            "Import",
            str(start),
            f"3D models ({patterns})",
        )
        if not path_strs:
            return
        paths = [Path(path_str) for path_str in path_strs]
        self._import_dir = paths[0].parent
        self._import_files(paths)

    def _export_dialog(self, *, selection: bool) -> None:
        """Export the scene, or the selected transforms, as glTF."""
        roots: list[str] | None = None
        if selection:
            roots = self._selection_roots()
            if not roots:
                QMessageBox.information(
                    self,
                    "Export Selection",
                    "Select one or more transforms first.",
                    QMessageBox.StandardButton.Ok,
                )
                return
        stem = self._scene_path.stem if self._scene_path is not None else "scene"
        start = (
            self._scene_path.parent if self._scene_path is not None else Path.home()
        ) / f"{stem}.glb"
        path_str, chosen = QFileDialog.getSaveFileName(
            self,
            "Export Selection" if selection else "Export Scene",
            str(start),
            "Binary glTF (*.glb);;glTF JSON (*.gltf)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() not in (".glb", ".gltf"):
            path = path.with_suffix(".gltf" if "gltf" in chosen.lower() else ".glb")
        self.export_path(path, roots=roots)

    def export_path(self, path: Path, *, roots: Sequence[str] | None = None) -> bool:
        """Write the scene (or ``roots`` with their descendants) as glTF.

        Args:
            path: ``.glb`` or ``.gltf`` destination.
            roots: Transform UIDs to export, or ``None`` for the whole scene.

        Returns:
            ``True`` when the file was written.
        """
        try:
            result = export_scene(self._world, path, roots=roots)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export", f"Could not export:\n{exc}")
            return False
        self._remember_recent(path)
        noun = "mesh" if result.meshes == 1 else "meshes"
        self.statusBar().showMessage(
            f"Exported {result.transforms} nodes, {result.meshes} {noun}, "
            f"{result.lights} lights to {path.name}",
            6000,
        )
        if result.warnings:
            QMessageBox.warning(
                self,
                "Export",
                "Exported with these omissions:\n" + "\n".join(result.warnings),
            )
        return True

    def _import_files(self, paths: Sequence[Path], *, report: bool = True) -> list[str]:
        """Import model files as one undo step; return the new root names.

        Args:
            paths: Model files.
            report: Show problems in a message box. When ``False`` they are
                left in ``_import_problems`` for the caller to report.
        """
        used = self._used_names()
        clip_names = set(self._world.animations)
        results: list[ImportResult] = []
        failures: list[str] = []
        warnings: list[str] = []
        for path in paths:
            try:
                model = load_model(path)
            except ModelImportError as exc:
                failures.append(str(exc))
                continue
            self._remember_recent(path)
            warnings.extend(model.warnings)
            conversion = import_conversion(
                path,
                self._settings.import_up_axis,
                self._settings.import_unit,
            )
            results.append(
                import_objects(
                    model,
                    used,
                    space=self._edit_space,
                    conversion=conversion,
                    clip_names=clip_names,
                )
            )
            warnings.extend(results[-1].warnings)
        roots = [result.root for result in results]
        if results:
            transforms = [t for result in results for t in result.objects.transforms]
            meshes = [m for result in results for m in result.objects.meshes]
            lights = [light for result in results for light in result.objects.lights]
            cameras = [cam for result in results for cam in result.objects.cameras]
            clips = [clip for result in results for clip in result.objects.animations]
            for transform in transforms:
                self._register_transform(transform)
            for mesh in meshes:
                self._register_mesh(mesh)
            for light in lights:
                self._register_light(light)
            for camera in cameras:
                self._register_camera(camera)
            for clip in clips:
                self._register_animation(clip)
            self._history.push(
                HistoryStep(
                    label="Import",
                    added=SceneObjects(
                        transforms=[t.model_copy(deep=True) for t in transforms],
                        meshes=[m.model_copy(deep=True) for m in meshes],
                        lights=[light.model_copy(deep=True) for light in lights],
                        cameras=[cam.model_copy(deep=True) for cam in cameras],
                        animations=[clip.model_copy(deep=True) for clip in clips],
                    ),
                )
            )
            self._rebuild_tree(roots)
            # Models come in any size (often millimetres), so show them.
            self._viewport.frame_selection(roots)
        problems: list[str] = []
        if failures:
            problems.append("Could not import:\n" + "\n".join(failures))
        if warnings:
            problems.append(
                "Some parts of these models were approximated or skipped:\n"
                + "\n".join(warnings)
            )
        if not report:
            self._import_problems = problems
        elif problems:
            QMessageBox.warning(self, "Import", "\n\n".join(problems))
        return roots

    def import_files(self, paths: Sequence[Path]) -> list[str]:
        """Import model files into the current scene as one undo step.

        Args:
            paths: Model files (see :data:`IMPORT_SUFFIXES`).

        Returns:
            Names of the new root transforms.
        """
        return self._import_files(paths)

    def _source_warnings(self) -> list[str]:
        """Warnings from every model file the scene uses, each file once."""
        warnings: list[str] = []
        sources = {
            mesh.source for mesh in self._world.iter_meshes() if mesh.kind == "file"
        }
        for source in sorted(sources):
            try:
                warnings.extend(load_model(source).warnings)
            except ModelImportError:
                # Reported as a missing source instead.
                continue
        return warnings

    def _reload_changed_files(self) -> None:
        """Pick up model and texture files edited outside the app."""
        reloaded = self._viewport.reload_changed_sources()
        if reloaded:
            count = len(reloaded)
            noun = "mesh" if count == 1 else "meshes"
            self.statusBar().showMessage(
                f"Reloaded {count} {noun} from changed files", 5000
            )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # Coming back from an image editor or DCC: reload what was saved.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._reload_changed_files()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # File drags over the outliner import like drops anywhere else.
        if (
            event.type()
            in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop)
            and isinstance(event, QDropEvent)
            and dropped_models(event.mimeData())
        ):
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
            else:
                event.acceptProposedAction()
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if dropped_models(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if dropped_models(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = dropped_models(event.mimeData())
        if not paths:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        self._import_files(paths)

    def _save_scene(self) -> None:
        if self._scene_path is None:
            self._save_scene_as()
            return
        try:
            save_scene(self._document_from_store(), self._scene_path)
        except OSError as exc:
            QMessageBox.warning(self, "Save Scene", f"Could not save:\n{exc}")
            return
        self._saved_document = self._world.to_document()
        self._remember_recent(self._scene_path)

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
        self._saved_document = self._world.to_document()
        self._remember_recent(path)

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
        added_cameras: list[CameraComponent] = []
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
            for camera_name in self._world.cameras_on(name):
                camera = self._world.cameras[camera_name]
                new_camera_name = self._unique_name(f"{name_map[name]}Shape")
                new_camera = camera.model_copy(
                    update={"name": new_camera_name, "transform": name_map[name]}
                )
                self._register_camera(new_camera)
                added_cameras.append(new_camera.model_copy(deep=True))

        self._history.push(
            HistoryStep(
                label="Duplicate",
                added=SceneObjects(
                    transforms=added_transforms,
                    meshes=added_meshes,
                    lights=added_lights,
                    cameras=added_cameras,
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

    def _hide_selection(self) -> None:
        roots = self._selection_roots()
        if roots:
            self._set_visibility(dict.fromkeys(roots, False), label="Hide")

    def _select_all(self) -> None:
        names = [name for name in self._world.transforms if name not in FOLDER_NAMES]
        self._select_names(names)

    def _select_names(self, names: list[str]) -> None:
        """Select transforms in both the tree and viewport."""
        self._syncing = True
        try:
            self._tree.set_selected_names(names)
            self._viewport.set_selected_names(names)
        finally:
            self._syncing = False
        self._sync_properties(names)

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
        for camera in objects.cameras:
            if camera.name not in self._world.cameras:
                self._register_camera(camera)
            else:
                self._world.cameras[camera.name] = camera
                self._viewport.apply_camera(camera)
        for clip in objects.animations:
            pruned = prune_clip(clip, self._world.transforms)
            if pruned is None:
                continue
            if pruned.name in self._world.animations:
                self._world.set_animation(pruned)
            else:
                self._world.add_animation(pruned)

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
        order = step.order_before if undo else step.order_after
        if order is not None:
            self._world.reorder_transforms(order)
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
            if (
                field_name in {"position", "rotation", "scale", "pivot"}
                and self._playback.pose_displayed
                and self._playback.affects(model.name)
            ):
                # The panel reads the viewport, which shows a pose. Stop first.
                return False
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
        for camera_name in self._world.cameras_on(name):
            pages.append(("Camera", self._world.cameras[camera_name]))
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
        if isinstance(model, CameraComponent):
            self._world.cameras[model.name] = model
            self._viewport.apply_camera(model)
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

        updated = self._apply_panel_trs(model, stored)
        if updated is None:
            return
        self._world.transforms[model.name] = updated
        self._push_transform_edits(
            "Edit Transform",
            before,
            {model.name: updated.model_copy(deep=True)},
        )
        self._props.set_pages(self._pages_for_transform(model.name))

    def _apply_panel_trs(
        self,
        model: TransformComponent,
        stored: TransformComponent,
    ) -> TransformComponent | None:
        """Push the panel's TRS to the viewport and return the local result.

        Args:
            model: Panel model; its TRS is local or world per ``model.space``.
            stored: Current stored transform the result is based on.

        Returns:
            ``stored`` with the new local TRS, or ``None`` if the transform
            is missing from the viewport. The store is not changed.
        """
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
            return updated
        local = self._viewport.apply_transform_world(
            model.name,
            model.position,
            model.rotation,
            model.scale,
        )
        if local is None:
            return None
        return stored.model_copy(
            update={
                "space": "world",
                "position": local.position,
                "rotation": local.rotation,
                "scale": local.scale,
            }
        )

    def _on_prop_preview(
        self,
        field_name: str,
        _value: object,
        model: BaseModel,
    ) -> None:
        """Show a properties drag live in the viewport, without store or undo.

        The drag's release arrives as a normal field commit through
        :meth:`_on_prop_field`, which records one undo step against the
        untouched store. Esc previews the start value to put it back.
        """
        if isinstance(model, MeshComponent):
            self._viewport.apply_mesh(model)
            return
        if isinstance(model, LightComponent):
            self._viewport.apply_light(model)
            return
        if not isinstance(model, TransformComponent):
            return
        stored = self._world.transforms.get(model.name)
        if stored is None:
            return
        if field_name == "pivot":
            self._viewport.set_pivot(model.name, model.pivot)
        elif field_name in {"position", "rotation", "scale"}:
            self._apply_panel_trs(model, stored)

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
        self._update_status()
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
            if self._stop_playback_for_edit(names):
                self._viewport.rebase_drag()
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


def dropped_models(mime: QMimeData | None) -> list[Path]:
    """Return the importable local model files in drag-and-drop data.

    Args:
        mime: Data of a drag or drop event.

    Returns:
        Existing files with a supported suffix, in drop order.
    """
    if mime is None or not mime.hasUrls():
        return []
    local: Iterable[Path] = (
        Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()
    )
    return importable_paths(local)


def startup_paths(argv: Sequence[str]) -> list[Path]:
    """Return the files named on the command line, in order.

    Args:
        argv: Arguments after the program name.

    Returns:
        Arguments with a scene (``.json``) or model suffix, whether or not
        the file exists; :meth:`SceneViewportWindow.open_path` reports a
        missing one. Other arguments are ignored with a warning.
    """
    paths: list[Path] = []
    for argument in argv:
        path = Path(argument)
        if path.suffix.lower() == ".json" or is_importable(path):
            paths.append(path)
        else:
            _log.warning("ignoring argument %s: not a scene or model file", argument)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    """Show the scene viewport window and run the Qt event loop.

    Args:
        argv: Command-line arguments after the program name. Defaults to
            ``sys.argv[1:]``. The first scene or model file is opened; any
            further model files are imported into it.
    """
    app = ensure_application()
    window = SceneViewportWindow()
    window.show()
    paths = startup_paths(sys.argv[1:] if argv is None else argv)
    if paths:
        first, *rest = paths
        if window.open_path(first):
            models = [path for path in rest if is_importable(path)]
            if models:
                window.import_files(models)
    return app.exec()
