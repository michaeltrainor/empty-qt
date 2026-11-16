"""Main window: document tabs around Hydra views, laid out like scene_viewport.

Each open stage is a tab. The prim tree and layers sit on the left, the
inspector on the right, the timeline below, and Validation and the Python
console in a panel under the view that opens on demand; all of them follow
the active tab. Ejecting a prim writes it to a sidecar layer and opens that
layer in its own tab; both tabs share the layer, so edits show in both.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pxr import Sdf
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QKeySequence,
    QUndoGroup,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from mtqt import TimelineBar, apply_stylesheet, ensure_application
from stage_editor.cli import build_parser, resolve_camera, split_paths
from stage_editor.display import (
    AOVS,
    BACKGROUNDS,
    COMPLEXITIES,
    DRAW_MODES,
    PICK_MODES,
    ViewSettings,
    complexity_by_name,
)
from stage_editor.document import USD_SUFFIXES, StageDocument
from stage_editor.edits import (
    PRIM_TYPES,
    bind_material,
    create_material,
    create_prim,
    default_parent,
    delete_prims,
    duplicate_prims,
    rename_prim,
    reparent_prims,
    stage_materials,
)
from stage_editor.eject import (
    check_ejectable,
    eject,
    ejected_info,
    find_sidecar,
    merge_back,
    sidecar_file,
)
from stage_editor.hydra_view import HydraView, hydra_available, stage_cameras
from stage_editor.inspector import Inspector
from stage_editor.interpreter import EditorApi, Interpreter
from stage_editor.layers import LayerPanel
from stage_editor.outliner import Outliner
from stage_editor.undo import LayerSnapshotCommand
from stage_editor.validation import ValidationPanel

FILE_FILTER = "USD files (" + " ".join(f"*{s}" for s in USD_SUFFIXES) + ")"
SAVE_FILTER = "USD ASCII (*.usda);;USD binary (*.usdc);;USD (*.usd)"
IMAGE_FILTER = "PNG image (*.png);;JPEG image (*.jpg)"
SETTINGS_ORG = "mtqt"
SETTINGS_APP = "stage-editor"
TOOLS = (("select", "Select", "Q"), ("translate", "Move", "W"))
TOOLS += (("rotate", "Rotate", "E"), ("scale", "Scale", "R"))


class StagePlayback:
    """Drive the document's time from a :class:`TimelineBar`.

    Args:
        document: The document whose time advances.
        bar: The transport widget.
        parent: Owner of the tick timer.
    """

    def __init__(
        self, document: StageDocument, bar: TimelineBar, parent: QWidget
    ) -> None:
        self._document = document
        self._bar = bar
        self._loop = True
        self._speed = 1.0
        self._timer = QTimer(parent)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        bar.playToggled.connect(self._on_play)
        bar.stopRequested.connect(self.stop)
        bar.loopToggled.connect(self._on_loop)
        bar.speedChanged.connect(self._on_speed)
        bar.timeScrubbed.connect(self._on_scrub)
        document.stageReplaced.connect(self.refresh)
        document.timeChanged.connect(self._on_time)
        self.refresh()

    def refresh(self) -> None:
        """Reload the range from the stage and stop."""
        self._timer.stop()
        start, end = self._document.time_range()
        seconds = max(end - start, 0.0) / self._document.time_codes_per_second()
        self._bar.set_clips(["Stage time"] if seconds > 0 else [])
        self._bar.set_duration(seconds)
        self._bar.set_playing(False)
        self._bar.set_loop(self._loop)
        self._on_time(self._document.time)

    def stop(self) -> None:
        """Pause and rewind to the start time code."""
        self._timer.stop()
        self._bar.set_playing(False)
        self._document.time = self._document.time_range()[0]

    def step(self, frames: float) -> None:
        """Move the current time by ``frames`` time codes, clamped to the range.

        Args:
            frames: Time codes to move; negative steps back.
        """
        start, end = self._document.time_range()
        self._document.time = min(max(self._document.time + frames, start), end)

    def _on_play(self, playing: bool) -> None:
        if playing:
            self._timer.start(int(1000 / self._document.time_codes_per_second()))
        else:
            self._timer.stop()

    def _on_loop(self, loop: bool) -> None:
        self._loop = loop

    def _on_speed(self, speed: float) -> None:
        self._speed = speed

    def _on_scrub(self, seconds: float) -> None:
        start, _end = self._document.time_range()
        self._document.time = start + seconds * self._document.time_codes_per_second()

    def _on_time(self, code: float) -> None:
        start, _end = self._document.time_range()
        self._bar.set_time((code - start) / self._document.time_codes_per_second())

    def _tick(self) -> None:
        start, end = self._document.time_range()
        code = self._document.time + self._speed
        if code > end:
            if not self._loop:
                self._timer.stop()
                self._bar.set_playing(False)
                code = end
            else:
                code = start + (code - end - 1)
        self._document.time = code


class DocumentTab:
    """One open stage and the panels that show it.

    Args:
        window: The window that owns the widgets.
        document: The stage document; the tab keeps it.
    """

    def __init__(self, window: StageEditorWindow, document: StageDocument) -> None:
        self.document = document
        self.view: HydraView | None = None
        if hydra_available():
            self.view = HydraView(document, window)
            self.page: QWidget = self.view
        else:
            missing = QLabel(
                "pxr.UsdImagingGL is not importable.\n"
                "Build OpenUSD with scripts/build_usd_imaging.cmd, then put its\n"
                "inst/Lib/site-packages on PYTHONPATH\n"
                "and inst/lib and inst/bin on PATH.",
                window,
            )
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.page = missing
        self.outliner = Outliner(document, window)
        self.layers = LayerPanel(document, window)
        self.left = QTabWidget(window)
        self.left.addTab(self.outliner, "Outliner")
        self.left.addTab(self.layers, "Layers")
        self.inspector = Inspector(document, window)
        self.validation = ValidationPanel(document, window)
        api = EditorApi(document, self.outliner.selected_paths, window.select_paths)
        self.interpreter = Interpreter(api, window)
        self.bottom = QTabWidget(window)
        self.bottom.addTab(self.validation, "Validation")
        self.bottom.addTab(self.interpreter, "Interpreter")
        self.timeline = TimelineBar(window)
        self.playback = StagePlayback(document, self.timeline, window)

    @property
    def title(self) -> str:
        """Tab text: the file name, with ``*`` when unsaved."""
        star = "*" if self.document.has_unsaved_changes() else ""
        return f"{self.document.title}{star}"

    def widgets(self) -> list[QWidget]:
        """Every top-level widget this tab added to the window."""
        return [self.page, self.left, self.inspector, self.bottom, self.timeline]


class StageEditorWindow(QMainWindow):
    """The editor shell. Opens a file from the command line through :meth:`open_path`.

    Args:
        parent: Owning widget, normally none.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StageEditorWindow")
        apply_stylesheet(self, "base", "widgets")
        self.resize(1600, 960)
        self.tabs: list[DocumentTab] = []
        self._settings = ViewSettings()
        self._tool = "select"
        self._space = "world"
        self._undo = QUndoGroup(self)

        self._documents = QTabWidget(self)
        self._documents.setObjectName("StageEditorDocuments")
        self._documents.setTabsClosable(True)
        self._documents.setMovable(True)
        self._documents.setDocumentMode(True)
        self._documents.currentChanged.connect(self._on_current_changed)
        self._documents.tabCloseRequested.connect(self._close_tab_at)
        self._left = QStackedWidget(self)
        self._right = QStackedWidget(self)
        self._bottom = QStackedWidget(self)
        self._timelines = QStackedWidget(self)
        self._bottom.hide()

        center = QSplitter(Qt.Orientation.Vertical, self)
        center.addWidget(self._documents)
        center.addWidget(self._bottom)
        center.setStretchFactor(0, 1)
        self._splitter = QSplitter(self)
        self._splitter.setObjectName("StageEditorSplitter")
        self._splitter.addWidget(self._left)
        self._splitter.addWidget(center)
        self._splitter.addWidget(self._right)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([320, 900, 380])
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._splitter, 1)
        layout.addWidget(self._timelines)
        self.setCentralWidget(central)

        self._status = QLabel(self)
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.addPermanentWidget(self._status)
        self._settings_actions: dict[str, QAction] = {}
        self._tool_actions: dict[str, QAction] = {}
        self._space_actions: dict[str, QAction] = {}
        self._add_tab(StageDocument(self))
        self._install_menus()
        self._install_toolbar()
        self._restore_settings()

    # -- tabs -----------------------------------------------------------------

    @property
    def current(self) -> DocumentTab:
        """The active tab."""
        return self.tabs[self._documents.currentIndex()]

    @property
    def document(self) -> StageDocument:
        """The active tab's document."""
        return self.current.document

    @property
    def view(self) -> HydraView | None:
        """The active tab's Hydra view, or ``None`` without the imaging build."""
        return self.current.view

    @property
    def outliner(self) -> Outliner:
        """The active tab's outliner."""
        return self.current.outliner

    @property
    def inspector(self) -> Inspector:
        """The active tab's inspector."""
        return self.current.inspector

    @property
    def validation(self) -> ValidationPanel:
        """The active tab's validation panel."""
        return self.current.validation

    @property
    def interpreter(self) -> Interpreter:
        """The active tab's Python console."""
        return self.current.interpreter

    def _add_tab(self, document: StageDocument) -> DocumentTab:
        tab = DocumentTab(self, document)
        self.tabs.append(tab)
        self._left.addWidget(tab.left)
        self._right.addWidget(tab.inspector)
        self._bottom.addWidget(tab.bottom)
        self._timelines.addWidget(tab.timeline)
        self._undo.addStack(document.undo_stack)
        if tab.view is not None:
            tab.view.set_settings(self._settings)
            tab.view.set_tool(self._tool)
            tab.view.set_space(self._space)
            tab.view.pickedPath.connect(lambda path, t=tab: self._on_picked(t, path))
            tab.view.renderTimed.connect(lambda ms, t=tab: self._on_render_timed(t, ms))
        outliner = tab.outliner
        outliner.selectedPathsChanged.connect(lambda p, t=tab: self._on_selection(t, p))
        outliner.frameRequested.connect(self._frame_paths)
        outliner.cameraRequested.connect(self._look_through)
        outliner.renameRequested.connect(self._rename)
        outliner.reparentRequested.connect(self._reparent)
        outliner.menu_hook = self._fill_edit_menu
        tab.inspector.message.connect(self._show_message)
        tab.validation.pathsSelected.connect(self.select_paths)
        document.stageReplaced.connect(self._refresh_titles)
        document.dirtyChanged.connect(self._refresh_titles)
        document.layersChanged.connect(self._refresh_titles)
        self._documents.addTab(tab.page, tab.title)
        self._documents.setCurrentIndex(len(self.tabs) - 1)
        return tab

    def _on_current_changed(self, index: int) -> None:
        if not 0 <= index < len(self.tabs):
            return
        # Tabs can be dragged into a new order; follow the page, not the index.
        page = self._documents.widget(index)
        tab = next(t for t in self.tabs if t.page is page)
        self.tabs.remove(tab)
        self.tabs.insert(index, tab)
        self._left.setCurrentWidget(tab.left)
        self._right.setCurrentWidget(tab.inspector)
        self._bottom.setCurrentWidget(tab.bottom)
        self._timelines.setCurrentWidget(tab.timeline)
        self._undo.setActiveStack(tab.document.undo_stack)
        self._refresh_titles()

    def _tab_for(self, path: Path) -> DocumentTab | None:
        for tab in self.tabs:
            if (
                tab.document.path is not None
                and tab.document.path.resolve() == path.resolve()
            ):
                return tab
        return None

    def _close_tab_at(self, index: int, *, ask: bool = True) -> bool:
        tab = self.tabs[index]
        if ask and not self._confirm_discard([tab], "Close Tab"):
            return False
        if len(self.tabs) == 1:
            tab.document.new()
            return True
        if tab.view is not None:
            tab.view.shutdown()
        self._undo.removeStack(tab.document.undo_stack)
        self._documents.removeTab(index)
        self.tabs.remove(tab)
        for widget in tab.widgets():
            widget.setParent(None)
            widget.deleteLater()
        tab.document.deleteLater()
        return True

    def open_tab(self, path: Path) -> DocumentTab | None:
        """Open ``path`` in its own tab, or switch to the tab that has it.

        Args:
            path: A USD file.

        Returns:
            The tab, or ``None`` if the file could not be opened.
        """
        existing = self._tab_for(path)
        if existing is not None:
            self._documents.setCurrentWidget(existing.page)
            return existing
        document = StageDocument(self)
        try:
            document.open(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Open", str(exc))
            document.deleteLater()
            return None
        return self._add_tab(document)

    def _refresh_titles(self, *_args: object) -> None:
        for index, tab in enumerate(self.tabs):
            self._documents.setTabText(index, tab.title)
            info = ejected_info(tab.document.stage.GetRootLayer())
            tip = str(tab.document.path or "Untitled")
            if info:
                tip += f"\nEjected from {info.get('path')}"
            self._documents.setTabToolTip(index, tip)
        if self.tabs:
            self.setWindowTitle(f"{self.current.title} - Stage Editor")

    # -- menus ----------------------------------------------------------------

    def _action(
        self,
        text: str,
        shortcut: str | QKeySequence.StandardKey,
        slot: Callable[[], object],
        menu: QMenu,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    def _toggle(self, text: str, field: str, menu: QMenu, shortcut: str = "") -> None:
        action = QAction(text, self)
        action.setCheckable(True)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.toggled.connect(lambda on, f=field: self._set_view_field(f, on))
        menu.addAction(action)
        self._settings_actions[field] = action

    def _choice(
        self,
        menu: QMenu,
        field: str,
        options: Sequence[tuple[str, object]],
    ) -> None:
        group = QActionGroup(self)
        group.setExclusive(True)
        for label, value in options:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(value)
            action.triggered.connect(
                lambda _c=False, f=field, v=value: self._set_view_field(f, v)
            )
            group.addAction(action)
            menu.addAction(action)
            self._settings_actions[f"{field}={value!r}"] = action

    def _install_menus(self) -> None:
        bar = self.menuBar()
        assert bar is not None

        file_menu = bar.addMenu("&File")
        self._action("&New", QKeySequence.StandardKey.New, self._new, file_menu)
        self._action(
            "&Open...", QKeySequence.StandardKey.Open, self._open_dialog, file_menu
        )
        self._action(
            "Open in New &Tab...", "Ctrl+Shift+O", self._open_tab_dialog, file_menu
        )
        self._action("Add &Reference...", "Ctrl+I", self._reference_dialog, file_menu)
        file_menu.addSeparator()
        self._action("&Save", QKeySequence.StandardKey.Save, self._save, file_menu)
        self._action(
            "Save &As...",
            QKeySequence.StandardKey.SaveAs,
            self._save_as_dialog,
            file_menu,
        )
        self._action("Save A&ll", "Ctrl+Alt+S", self._save_all, file_menu)
        self._action(
            "Save &Flattened As...", "", self._save_flattened_dialog, file_menu
        )
        self._action(
            "Save &Viewer Image...", "Ctrl+Shift+P", self._save_image_dialog, file_menu
        )
        file_menu.addSeparator()
        self._action(
            "&Close Tab",
            QKeySequence.StandardKey.Close,
            lambda: self._close_tab_at(self._documents.currentIndex()),
            file_menu,
        )
        self._action("&Quit", QKeySequence.StandardKey.Quit, self.close, file_menu)

        edit_menu = bar.addMenu("&Edit")
        undo = self._undo.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence(QKeySequence.StandardKey.Undo))
        redo = self._undo.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence(QKeySequence.StandardKey.Redo))
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        edit_menu.addSeparator()
        self._action(
            "&Delete", QKeySequence.StandardKey.Delete, self._delete, edit_menu
        )
        self._action("D&uplicate", "Ctrl+D", self._duplicate, edit_menu)
        self._action(
            "Re&name", "F2", lambda: self.outliner.edit_current_name(), edit_menu
        )
        self._action("&Group", "Ctrl+G", self._group, edit_menu)
        edit_menu.addSeparator()
        self._action(
            "&Find Prim", "Ctrl+F", lambda: self.outliner.focus_search(), edit_menu
        )
        self._action(
            "&Copy Prim Path",
            "Ctrl+Shift+C",
            lambda: self.outliner.copy_paths(),
            edit_menu,
        )

        create = bar.addMenu("&Create")
        self._fill_create_menu(create)

        layer_menu = bar.addMenu("&Layer")
        self._action("&Eject to Layer", "Ctrl+E", self._eject, layer_menu)
        self._action(
            "&Open Ejected Layer", "Ctrl+Shift+E", self._open_ejected, layer_menu
        )
        self._action("&Merge Back", "", self._merge_back, layer_menu)

        display = bar.addMenu("&Display")
        draw = display.addMenu("&Draw Mode")
        self._choice(draw, "draw_mode", DRAW_MODES)
        complexity = display.addMenu("&Complexity")
        self._choice(
            complexity, "complexity", [(label, v) for label, _k, v in COMPLEXITIES]
        )
        complexity.addSeparator()
        self._action("Increase", "Ctrl+=", lambda: self._step_complexity(1), complexity)
        self._action(
            "Decrease", "Ctrl+-", lambda: self._step_complexity(-1), complexity
        )
        purposes = display.addMenu("&Purposes")
        self._toggle("&Guide", "show_guides", purposes)
        self._toggle("&Proxy", "show_proxy", purposes)
        self._toggle("&Render", "show_render", purposes)
        lights = display.addMenu("&Lights")
        self._toggle("&Camera Light", "camera_light", lights)
        self._toggle("&Dome Light", "dome_light", lights)
        self._toggle("&Scene Lights", "scene_lights", lights)
        display.addSeparator()
        self._toggle("Scene &Materials", "scene_materials", display)
        self._toggle("Cull &Backfaces", "cull_backfaces", display)
        self._toggle("Selection &Bounds", "show_bounds", display, "Ctrl+B")
        self._toggle("&HUD", "show_hud", display, "Ctrl+H")
        aov = display.addMenu("&Render Output")
        self._choice(aov, "aov", [(name, name) for name in AOVS])
        background = display.addMenu("Back&ground")
        self._choice(background, "background", BACKGROUNDS)

        camera = bar.addMenu("&Camera")
        self._camera_menu = camera
        camera.aboutToShow.connect(self._populate_camera_menu)
        self._populate_camera_menu()

        select = bar.addMenu("&Select")
        pick = select.addMenu("&Pick Mode")
        self._choice(pick, "pick_mode", PICK_MODES)

        tools = bar.addMenu("&Tools")
        self._action("&Validate Stage", "Ctrl+Shift+V", self._validate, tools)
        self._action(
            "Python &Interpreter", "Ctrl+Shift+I", self._show_interpreter, tools
        )
        self._action("&Hide Bottom Panel", "", self._bottom.hide, tools)

        play = bar.addMenu("&Playback")
        self._action("Next Frame", "Right", lambda: self.current.playback.step(1), play)
        self._action(
            "Previous Frame", "Left", lambda: self.current.playback.step(-1), play
        )
        self._action("Stop", "", lambda: self.current.playback.stop(), play)
        self._sync_settings_actions()

    def _install_toolbar(self) -> None:
        toolbar = QToolBar("Tools", self)
        toolbar.setObjectName("StageEditorToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)
        tools = QActionGroup(self)
        for tool, label, shortcut in TOOLS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            action.setToolTip(f"{label} ({shortcut})")
            action.triggered.connect(lambda _c=False, t=tool: self.set_tool(t))
            tools.addAction(action)
            toolbar.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions[self._tool].setChecked(True)
        toolbar.addSeparator()
        spaces = QActionGroup(self)
        for space, label in (("world", "World"), ("local", "Local")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(f"Move and rotate in {label.lower()} space")
            action.triggered.connect(lambda _c=False, s=space: self.set_space(s))
            spaces.addAction(action)
            toolbar.addAction(action)
            self._space_actions[space] = action
        self._space_actions[self._space].setChecked(True)
        toolbar.addSeparator()
        for label, slot, tip in (
            (
                "Eject",
                self._eject,
                "Move the selected prim to its own layer and open it",
            ),
            (
                "Open Ejected",
                self._open_ejected,
                "Open the selected prim's ejected layer",
            ),
            (
                "Merge Back",
                self._merge_back,
                "Fold the ejected layer back into the scene",
            ),
        ):
            action = QAction(label, self)
            action.setToolTip(tip)
            action.triggered.connect(lambda _c=False, f=slot: f())
            toolbar.addAction(action)

    def _fill_create_menu(self, menu: QMenu, paths: Sequence[str] = ()) -> None:
        submenus: dict[str, QMenu] = {}
        for group, label, type_name in PRIM_TYPES:
            target = menu
            if group:
                target = submenus.get(group) or menu.addMenu(group)
                submenus[group] = target
            action = target.addAction(label)
            action.triggered.connect(
                lambda _c=False, t=type_name, p=list(paths): self._create(t, p)
            )
        menu.addSeparator()
        action = menu.addAction("MaterialX Material")
        action.triggered.connect(lambda _c=False: self._create_material())

    def _fill_edit_menu(self, menu: QMenu, paths: list[str]) -> None:
        """Outliner context-menu entries for editing."""
        create = menu.addMenu("Create")
        self._fill_create_menu(create, paths[:1])
        if not paths:
            return
        stage = self.document.stage
        menu.addAction("Rename", lambda: self.outliner.edit_current_name())
        menu.addAction("Duplicate", self._duplicate)
        menu.addAction("Group", self._group)
        menu.addAction("Delete", self._delete)
        materials = stage_materials(stage)
        bind = menu.addMenu("Bind Material")
        for material in materials:
            bind.addAction(material, lambda m=material: self._bind(paths, m))
        if materials:
            bind.addSeparator()
        bind.addAction("Unbind", lambda: self._bind(paths, ""))
        menu.addSeparator()
        if self._sidecar_for(paths[0]) is not None:
            menu.addAction("Open Ejected Layer", self._open_ejected)
            menu.addAction("Merge Back", self._merge_back)
        else:
            menu.addAction("Eject to Layer", self._eject)

    def _populate_camera_menu(self) -> None:
        menu = self._camera_menu
        menu.clear()
        current = self.view.camera_path if self.view is not None else ""
        group = QActionGroup(menu)
        free = QAction("&Free Camera", menu)
        free.setCheckable(True)
        free.setChecked(not current)
        free.triggered.connect(lambda _c=False: self._look_through(""))
        group.addAction(free)
        menu.addAction(free)
        cameras = stage_cameras(self.document.stage)
        if cameras:
            menu.addSeparator()
        for path in cameras:
            action = QAction(path, menu)
            action.setCheckable(True)
            action.setChecked(path == current)
            action.triggered.connect(lambda _c=False, p=path: self._look_through(p))
            group.addAction(action)
            menu.addAction(action)
        menu.addSeparator()
        frame = menu.addAction("Frame &Selection")
        frame.setShortcut(QKeySequence("F"))
        frame.triggered.connect(lambda _c=False: self._frame_selection())
        frame_all = menu.addAction("Frame &All")
        frame_all.setShortcut(QKeySequence("A"))
        frame_all.triggered.connect(lambda _c=False: self._frame_paths([]))

    # -- tools ----------------------------------------------------------------

    @property
    def view_settings(self) -> ViewSettings:
        """A copy of the display settings every tab draws with."""
        return ViewSettings(**vars(self._settings))

    def set_tool(self, tool: str) -> None:
        """Switch every view to a tool: select, translate, rotate, or scale."""
        self._tool = tool
        for tab in self.tabs:
            if tab.view is not None:
                tab.view.set_tool(tool)
        action = self._tool_actions.get(tool)
        if action is not None:
            action.setChecked(True)

    def set_space(self, space: str) -> None:
        """Use ``world`` or ``local`` axes for move and rotate in every view."""
        self._space = space
        for tab in self.tabs:
            if tab.view is not None:
                tab.view.set_space(space)

    # -- view settings --------------------------------------------------------

    def _set_view_field(self, field: str, value: object) -> None:
        settings = ViewSettings(**vars(self._settings))
        if getattr(settings, field) == value:
            return
        setattr(settings, field, value)
        self.apply_view_settings(settings)

    def _step_complexity(self, step: int) -> None:
        complexity = self._settings.complexity
        values = [v for _l, _k, v in COMPLEXITIES]
        current = min(range(len(values)), key=lambda i: abs(values[i] - complexity))
        target = values[max(0, min(len(values) - 1, current + step))]
        self._set_view_field("complexity", target)

    def apply_view_settings(self, settings: ViewSettings) -> None:
        """Draw every tab with ``settings`` and check the matching menu entries.

        Args:
            settings: New view settings.
        """
        self._settings = ViewSettings(**vars(settings))
        for tab in self.tabs:
            if tab.view is not None:
                tab.view.set_settings(settings)
        self._sync_settings_actions()

    def _sync_settings_actions(self) -> None:
        settings = self._settings
        for key, action in self._settings_actions.items():
            action.blockSignals(True)
            if "=" in key:
                field, _sep, _value = key.partition("=")
                data = action.data()
                current = getattr(settings, field)
                if isinstance(data, tuple | list):
                    action.setChecked(tuple(data) == tuple(current))
                else:
                    action.setChecked(data == current)
            else:
                action.setChecked(bool(getattr(settings, key)))
            action.blockSignals(False)
            action.setEnabled(hydra_available())

    # -- settings persistence ---------------------------------------------------

    def _qsettings(self) -> QSettings:
        return QSettings(SETTINGS_ORG, SETTINGS_APP)

    def _restore_settings(self) -> None:
        store = self._qsettings()
        geometry = store.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter = store.value("splitter")
        if splitter is not None:
            self._splitter.restoreState(splitter)
        text = store.value("viewSettings")
        if isinstance(text, str):
            self.apply_view_settings(ViewSettings.from_json(text))

    def _save_settings(self) -> None:
        store = self._qsettings()
        store.setValue("geometry", self.saveGeometry())
        store.setValue("splitter", self._splitter.saveState())
        store.setValue("viewSettings", self._settings.to_json())

    # -- file -----------------------------------------------------------------

    def _confirm_discard(self, tabs: Sequence[DocumentTab], title: str) -> bool:
        dirty = [t for t in tabs if t.document.has_unsaved_changes()]
        if not dirty:
            return True
        names = "\n".join(f"  {t.document.title}" for t in dirty)
        answer = QMessageBox.question(
            self,
            title,
            f"Unsaved changes in:\n{names}\n\nSave them first?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return all(self._save_tab(t) for t in dirty)
        return answer == QMessageBox.StandardButton.Discard

    def _new(self) -> None:
        if self._confirm_discard([self.current], "New"):
            self.document.new()

    def _open_dialog(self) -> None:
        if not self._confirm_discard([self.current], "Open"):
            return
        name, _ = QFileDialog.getOpenFileName(self, "Open", "", FILE_FILTER)
        if name:
            self.open_path(Path(name))

    def _open_tab_dialog(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Open in New Tab", "", FILE_FILTER)
        if name:
            self.open_tab(Path(name))

    def open_path(
        self, path: Path, *, mask: Sequence[str] = (), load: bool = True
    ) -> bool:
        """Open ``path`` in the current tab, or reference it if foreign.

        Args:
            path: A USD file.
            mask: Prim paths to populate, in the file's namespace.
            load: Load payloads.

        Returns:
            ``True`` when something opened; a message box reports failures.
        """
        try:
            self.document.open(path, mask=mask, load=load)
        except ValueError as exc:
            QMessageBox.warning(self, "Open", str(exc))
            return False
        return True

    def _reference_dialog(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Add Reference", "", FILE_FILTER)
        if not name:
            return
        try:
            prim_path = self.document.add_reference(Path(name))
        except ValueError as exc:
            QMessageBox.warning(self, "Add Reference", str(exc))
            return
        self.select_paths([str(prim_path)])

    def _save_tab(self, tab: DocumentTab) -> bool:
        if tab.document.path is None:
            self._documents.setCurrentWidget(tab.page)
            return self._save_as_dialog()
        tab.document.save()
        self._refresh_titles()
        return True

    def _save(self) -> None:
        self._save_tab(self.current)

    def _save_all(self) -> None:
        for tab in list(self.tabs):
            if tab.document.has_unsaved_changes() and not self._save_tab(tab):
                return

    def _save_as_dialog(self) -> bool:
        name, _ = QFileDialog.getSaveFileName(self, "Save As", "", SAVE_FILTER)
        if not name:
            return False
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(".usda")
        try:
            self.document.save_as(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Save As", str(exc))
            return False
        return True

    def _save_flattened_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Flattened As", "", SAVE_FILTER
        )
        if not name:
            return
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(".usda")
        if not self.document.stage.Export(str(path), addSourceFileComment=False):
            QMessageBox.warning(self, "Save Flattened As", f"cannot write {path}")

    def _save_image_dialog(self) -> None:
        if self.view is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Viewer Image", "", IMAGE_FILTER
        )
        if not name:
            return
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(".png")
        if not self.view.grab_image().save(str(path)):
            QMessageBox.warning(self, "Save Viewer Image", f"cannot write {path}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard(self.tabs, "Quit"):
            event.ignore()
            return
        self._save_settings()
        for tab in self.tabs:
            if tab.view is not None:
                tab.view.shutdown()
        event.accept()

    # -- editing ----------------------------------------------------------------

    def _run[T](self, title: str, action: Callable[[], T]) -> T | None:
        """Run an edit, turning a refusal into a message box."""
        try:
            return action()
        except ValueError as exc:
            QMessageBox.warning(self, title, str(exc))
            return None

    def _create(self, type_name: str, paths: Sequence[str] = ()) -> None:
        document = self.document
        parent = default_parent(document, paths or self.outliner.selected_paths())
        path = self._run("Create", lambda: create_prim(document, parent, type_name))
        if path is not None:
            self.select_paths([str(path)])

    def _create_material(self) -> None:
        path = self._run("Create Material", lambda: create_material(self.document))
        if path is not None:
            self.select_paths([str(path)])

    def _bind(self, paths: list[str], material: str) -> None:
        self._run(
            "Bind Material", lambda: bind_material(self.document, paths, material)
        )
        self.inspector.set_prim_path(paths[0])

    def _delete(self) -> None:
        paths = self.outliner.selected_paths()
        if not paths:
            return
        hidden = self._run("Delete", lambda: delete_prims(self.document, paths))
        if hidden:
            self._show_message(
                f"Deactivated {len(hidden)} prim(s) that come from referenced files"
            )
        self.select_paths([])

    def _duplicate(self) -> None:
        paths = self.outliner.selected_paths()
        copies = self._run("Duplicate", lambda: duplicate_prims(self.document, paths))
        if copies:
            self.select_paths(list(copies))

    def _group(self) -> None:
        paths = self.outliner.selected_paths()
        if not paths:
            return
        document = self.document
        parent = Sdf.Path(paths[0]).GetParentPath()
        group = self._run("Group", lambda: create_prim(document, parent, "Xform"))
        if group is None:
            return
        moved = self._run("Group", lambda: reparent_prims(document, paths, str(group)))
        if moved is not None:
            self.select_paths([str(group)])

    def _rename(self, path: str, name: str) -> None:
        new = self._run("Rename", lambda: rename_prim(self.document, path, name))
        if new is not None:
            self.select_paths([str(new)])

    def _reparent(self, paths: list[str], parent: str) -> None:
        moved = self._run("Move", lambda: reparent_prims(self.document, paths, parent))
        if moved:
            self.select_paths(list(moved))

    # -- eject ------------------------------------------------------------------

    def _sidecar_for(self, path: str) -> Sdf.Layer | None:
        stage = self.document.stage
        for layer in stage.GetLayerStack(includeSessionLayers=False):
            sidecar = find_sidecar(stage, path, layer)
            if sidecar is not None:
                return sidecar
        return None

    def _eject(self) -> None:
        paths = self.outliner.selected_paths()
        if not paths:
            self._show_message("Select a prim to eject")
            return
        document = self.document
        try:
            prim = check_ejectable(document.stage, paths[0])
        except ValueError as exc:
            QMessageBox.warning(self, "Eject", str(exc))
            return
        if self._sidecar_for(paths[0]) is not None:
            self._open_ejected()
            return
        if document.path is None:
            QMessageBox.information(
                self,
                "Eject",
                "Save the scene first: ejected layers go beside it.",
                QMessageBox.StandardButton.Ok,
            )
            if not self._save_as_dialog():
                return
            document = self.document
        layer = document.edit_target_layer
        if layer == document.stage.GetSessionLayer():
            QMessageBox.warning(
                self,
                "Eject",
                "The edit target is the session layer; pick a scene layer.",
            )
            return
        assert document.path is not None
        file = sidecar_file(document.path, prim.GetName())
        stage = document.stage
        outcome: dict[str, object] = {}

        def edit() -> None:
            outcome["result"] = eject(stage, paths[0], file, layer)

        command = LayerSnapshotCommand(
            f"Eject {prim.GetName()}",
            stage.GetLayerStack(includeSessionLayers=False),
            edit,
        )
        try:
            document.undo_stack.push(command)
        except ValueError as exc:
            QMessageBox.warning(self, "Eject", str(exc))
            return
        result = outcome.get("result")
        if result is None:
            return
        warnings = getattr(result, "warnings", [])
        if warnings:
            QMessageBox.information(
                self, "Eject", "\n".join(warnings), QMessageBox.StandardButton.Ok
            )
        self._show_message(f"Ejected {paths[0]} to {file}")
        self.open_tab(file)

    def _open_ejected(self) -> None:
        paths = self.outliner.selected_paths()
        sidecar = self._sidecar_for(paths[0]) if paths else None
        if sidecar is None:
            self._show_message("The selected prim has no ejected layer")
            return
        self.open_tab(Path(sidecar.realPath))

    def _merge_back(self) -> None:
        paths = self.outliner.selected_paths()
        if not paths:
            return
        document = self.document
        stage = document.stage
        holder = None
        for layer in stage.GetLayerStack(includeSessionLayers=False):
            if find_sidecar(stage, paths[0], layer) is not None:
                holder = layer
                break
        if holder is None:
            self._show_message("The selected prim has no ejected layer")
            return
        merged: dict[str, Sdf.Layer] = {}

        def edit() -> None:
            merged["layer"] = merge_back(stage, paths[0], holder)

        document.undo_stack.push(
            LayerSnapshotCommand(
                "Merge Back", stage.GetLayerStack(includeSessionLayers=False), edit
            )
        )
        sidecar = merged.get("layer")
        if sidecar is None:
            return
        tab = self._tab_for(Path(sidecar.realPath))
        if tab is not None:
            # Its content now lives in the scene; the tab has nothing left to save.
            tab.document.mark_saved()
            self._close_tab_at(self.tabs.index(tab), ask=False)
        self._show_message(
            f"Merged {Path(sidecar.realPath).name} back into "
            f"{holder.GetDisplayName()}; the file stays on disk"
        )

    # -- selection and view ---------------------------------------------------

    def _show_message(self, text: str) -> None:
        status_bar = self.statusBar()
        if status_bar is not None:
            status_bar.showMessage(text, 8000)

    def _show_bottom(self, index: int) -> None:
        self._bottom.show()
        self.current.bottom.setCurrentIndex(index)

    def _validate(self) -> None:
        self._show_bottom(0)
        self.validation.run()

    def _show_interpreter(self) -> None:
        self._show_bottom(1)

    def select_paths(self, paths: list[str]) -> None:
        """Select ``paths`` in the active tab's outliner, view, and inspector.

        Args:
            paths: Prim paths as strings.
        """
        tab = self.current
        tab.outliner.set_selected_paths(paths)
        self._on_selection(tab, paths)

    def _on_selection(self, tab: DocumentTab, paths: list[str]) -> None:
        if tab.view is not None:
            tab.view.set_selected_paths(paths)
        tab.inspector.set_prim_path(paths[0] if paths else "")

    def _on_picked(self, tab: DocumentTab, path: str) -> None:
        modifiers = QApplication.keyboardModifiers()
        paths = [path] if path else []
        if modifiers & (
            Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
        ):
            current = tab.outliner.selected_paths()
            if path in current:
                current.remove(path)
                paths = current
            else:
                paths = current + paths
        tab.outliner.set_selected_paths(paths)
        self._on_selection(tab, paths)

    def _look_through(self, path: str) -> None:
        if self.view is not None:
            self.view.set_camera_path(path)

    def _frame_selection(self) -> None:
        self._frame_paths(self.outliner.selected_paths())

    def _frame_paths(self, paths: list[str]) -> None:
        if self.view is not None:
            self.view.frame_paths(paths)

    def _on_render_timed(self, tab: DocumentTab, milliseconds: float) -> None:
        if tab is not self.current:
            return
        count = tab.outliner.model.prim_count
        target = tab.document.edit_target_layer.GetDisplayName()
        self._status.setText(
            f"{milliseconds:.1f} ms   {count} prims   edit target: {target}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Show the stage editor and run the Qt event loop.

    Args:
        argv: Arguments after the program name; see ``stage-editor --help``.
    """
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    app = ensure_application()
    window = StageEditorWindow()
    window.show()
    if args.complexity or args.draw_mode:
        settings = window.view_settings
        if args.complexity:
            settings.complexity = complexity_by_name(args.complexity)
        if args.draw_mode:
            settings.draw_mode = f"DRAW_{args.draw_mode.upper()}"
        window.apply_view_settings(settings)
    if args.file is not None:
        opened = window.open_path(
            args.file, mask=split_paths(args.mask), load=not args.unloaded
        )
        document = window.document
        if opened and args.frame is not None:
            document.time = args.frame
        if opened and args.select:
            window.select_paths(
                [document.map_source_path(p) for p in split_paths(args.select)]
            )
        if opened and args.camera and window.view is not None:
            cameras = stage_cameras(document.stage)
            camera = resolve_camera(cameras, document.map_source_path(args.camera))
            camera = camera or resolve_camera(cameras, args.camera)
            window.view.set_camera_path(camera)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
