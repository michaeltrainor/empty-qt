"""Main window: Hydra view, dockable panels, timeline, and usdview-style menus."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import PySide6QtAds as ads
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QWidget,
)

from mtqt import TimelineBar, apply_stylesheet, ensure_application
from stage_editor.display import (
    AOVS,
    BACKGROUNDS,
    COMPLEXITIES,
    DRAW_MODES,
    PICK_MODES,
    ViewSettings,
)
from stage_editor.document import USD_SUFFIXES, StageDocument
from stage_editor.hydra_view import HydraView, hydra_available, stage_cameras
from stage_editor.inspector import Inspector
from stage_editor.interpreter import EditorApi, Interpreter
from stage_editor.layers import LayerPanel
from stage_editor.outliner import Outliner
from stage_editor.validation import ValidationPanel

FILE_FILTER = "USD files (" + " ".join(f"*{s}" for s in USD_SUFFIXES) + ")"
SAVE_FILTER = "USD ASCII (*.usda);;USD binary (*.usdc);;USD (*.usd)"
IMAGE_FILTER = "PNG image (*.png);;JPEG image (*.jpg)"
SETTINGS_ORG = "mtqt"
SETTINGS_APP = "stage-editor"
#: Bump when the dock layout changes so stale saved layouts are ignored.
LAYOUT_VERSION = 1


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


def _ads_stylesheet() -> str:
    return (resources.files("stage_editor") / "ads.qss").read_text(encoding="utf-8")


class StageEditorWindow(QMainWindow):
    """The editor shell. Opens a file from the command line through :meth:`open_path`.

    Panels are Qt Advanced Docking System dock widgets around the viewport;
    the layout and the view settings persist in ``QSettings``.

    Args:
        parent: Owning widget, normally none.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StageEditorWindow")
        apply_stylesheet(self, "base", "widgets")
        self.resize(1600, 960)
        self.document = StageDocument(self)
        # PySide6QtAds ships no stubs, so its types are Any here.
        self.docks: dict[str, Any] = {}

        ads.CDockManager.setConfigFlag(
            ads.CDockManager.eConfigFlag.OpaqueSplitterResize, True
        )
        ads.CDockManager.setConfigFlag(
            ads.CDockManager.eConfigFlag.FocusHighlighting, True
        )
        ads.CDockManager.setConfigFlag(
            ads.CDockManager.eConfigFlag.DockAreaHasUndockButton, False
        )
        self.dock_manager = ads.CDockManager(self)
        self.dock_manager.setStyleSheet(_ads_stylesheet())

        self.view: HydraView | None = None
        if hydra_available():
            self.view = HydraView(self.document, self)
            self.view.pickedPath.connect(self._on_picked)
            self.view.renderTimed.connect(self._on_render_timed)
            central_widget: QWidget = self.view
        else:
            missing = QLabel(
                "pxr.UsdImagingGL is not importable.\n"
                "Build OpenUSD with scripts/build_usd_imaging.cmd, then put its\n"
                "inst/Lib/site-packages on PYTHONPATH\n"
                "and inst/lib and inst/bin on PATH.",
                self,
            )
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            central_widget = missing
        central = ads.CDockWidget("Viewport", self)
        central.setObjectName("ViewportDock")
        central.setWidget(central_widget)
        central.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetClosable, False)
        central.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetMovable, False)
        central.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetFloatable, False)
        self.dock_manager.setCentralWidget(central)

        self.outliner = Outliner(self.document, self)
        self.outliner.selectedPathsChanged.connect(self._on_outliner_selection)
        self.outliner.frameRequested.connect(self._frame_paths)
        self.outliner.cameraRequested.connect(self._look_through)
        outliner = self._add_dock(
            "Outliner", self.outliner, ads.DockWidgetArea.LeftDockWidgetArea
        )
        self.inspector = Inspector(self.document, self)
        inspector = self._add_dock(
            "Inspector", self.inspector, ads.DockWidgetArea.RightDockWidgetArea
        )
        self.layers = LayerPanel(self.document, self)
        self._add_dock("Layers", self.layers, None, beside=inspector)
        inspector.setAsCurrentTab()
        self.timeline = TimelineBar(self)
        timeline = self._add_dock(
            "Timeline",
            self.timeline,
            ads.DockWidgetArea.BottomDockWidgetArea,
            no_tab=True,
        )
        timeline.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetClosable, False)
        timeline.dockAreaWidget().setDockAreaFlag(
            ads.CDockAreaWidget.eDockAreaFlag.HideSingleWidgetTitleBar, True
        )
        container = outliner.dockAreaWidget()
        self.dock_manager.setSplitterSizes(container, [380, 800, 420])
        self.playback = StagePlayback(self.document, self.timeline, self)

        self.validation = ValidationPanel(self.document, self)
        self.validation.pathsSelected.connect(self._select)
        validation = self._add_dock(
            "Validation",
            self.validation,
            ads.DockWidgetArea.BottomDockWidgetArea,
            target=central.dockAreaWidget(),
        )
        api = EditorApi(self.document, self.outliner.selected_paths, self._select)
        self.interpreter = Interpreter(api, self)
        self._add_dock("Interpreter", self.interpreter, None, beside=validation)
        for name in ("Validation", "Interpreter"):
            self.docks[name].toggleView(False)

        self._status = QLabel(self)
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.addPermanentWidget(self._status)
        self._settings_actions: dict[str, QAction] = {}
        self._install_menus()
        self.document.stageReplaced.connect(self._refresh_title)
        self.document.dirtyChanged.connect(self._refresh_title)
        self._refresh_title()
        self._restore_settings()

    # -- docks ----------------------------------------------------------------

    def _add_dock(
        self,
        title: str,
        widget: QWidget,
        area: Any,
        beside: Any = None,
        *,
        no_tab: bool = False,
        target: Any = None,
    ) -> Any:
        """Add ``widget`` as a dock in ``area``, or as a tab beside dock ``beside``.

        Returns:
            The new ``CDockWidget``.
        """
        dock = ads.CDockWidget(title, self)
        dock.setObjectName(f"{title.replace(' ', '')}Dock")
        dock.setWidget(widget)
        dock.setFeature(ads.CDockWidget.DockWidgetFeature.NoTab, no_tab)
        self.docks[title] = dock
        if beside is not None:
            self.dock_manager.addDockWidgetTabToArea(dock, beside.dockAreaWidget())
        else:
            self.dock_manager.addDockWidget(area, dock, target)
        return dock

    # -- menus ----------------------------------------------------------------

    def _action(
        self,
        text: str,
        shortcut: str,
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
        self._action("&New", "Ctrl+N", self._new, file_menu)
        self._action("&Open...", "Ctrl+O", self._open_dialog, file_menu)
        self._action("Add &Reference...", "Ctrl+I", self._reference_dialog, file_menu)
        file_menu.addSeparator()
        self._action("&Save", "Ctrl+S", self._save, file_menu)
        self._action("Save &As...", "Ctrl+Shift+S", self._save_as_dialog, file_menu)
        self._action(
            "Save &Flattened As...", "", self._save_flattened_dialog, file_menu
        )
        self._action(
            "Save &Viewer Image...", "Ctrl+Shift+P", self._save_image_dialog, file_menu
        )
        file_menu.addSeparator()
        self._action("&Quit", "Ctrl+Q", self.close, file_menu)

        edit_menu = bar.addMenu("&Edit")
        self._edit_menu = edit_menu
        self._action("&Find Prim", "Ctrl+F", self.outliner.focus_search, edit_menu)
        self._action(
            "&Copy Prim Path", "Ctrl+Shift+C", self.outliner.copy_paths, edit_menu
        )

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

        play = bar.addMenu("&Playback")
        self._action("Next Frame", "Right", lambda: self.playback.step(1), play)
        self._action("Previous Frame", "Left", lambda: self.playback.step(-1), play)
        self._action("Stop", "", self.playback.stop, play)

        self._window_menu = bar.addMenu("&Window")
        self._window_menu.aboutToShow.connect(self._populate_window_menu)
        self._sync_settings_actions()

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

    def _populate_window_menu(self) -> None:
        menu = self._window_menu
        menu.clear()
        for dock in self.docks.values():
            menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        reset = menu.addAction("Reset Layout")
        reset.triggered.connect(lambda _c=False: self._reset_layout())

    # -- view settings --------------------------------------------------------

    def _set_view_field(self, field: str, value: object) -> None:
        if self.view is None:
            return
        settings = self.view.settings
        if getattr(settings, field) == value:
            return
        setattr(settings, field, value)
        self.view.set_settings(settings)
        self._sync_settings_actions()

    def _step_complexity(self, step: int) -> None:
        if self.view is None:
            return
        complexity = self.view.settings.complexity
        values = [v for _l, _k, v in COMPLEXITIES]
        current = min(range(len(values)), key=lambda i: abs(values[i] - complexity))
        target = values[max(0, min(len(values) - 1, current + step))]
        self._set_view_field("complexity", target)

    def apply_view_settings(self, settings: ViewSettings) -> None:
        """Draw with ``settings`` and check the matching menu entries.

        Args:
            settings: New view settings; ignored without Hydra.
        """
        if self.view is not None:
            self.view.set_settings(settings)
        self._sync_settings_actions()

    def _sync_settings_actions(self) -> None:
        settings = self.view.settings if self.view is not None else ViewSettings()
        for key, action in self._settings_actions.items():
            action.blockSignals(True)
            if "=" in key:
                field, _sep, _value = key.partition("=")
                action.setChecked(
                    action.data() == getattr(settings, field)
                    or (
                        isinstance(action.data(), tuple | list)
                        and tuple(action.data()) == tuple(getattr(settings, field))
                    )
                )
            else:
                action.setChecked(bool(getattr(settings, key)))
            action.blockSignals(False)
            action.setEnabled(self.view is not None)

    # -- settings persistence ---------------------------------------------------

    def _qsettings(self) -> QSettings:
        return QSettings(SETTINGS_ORG, SETTINGS_APP)

    def _restore_settings(self) -> None:
        store = self._qsettings()
        geometry = store.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = store.value("dockState")
        if state is not None and store.value("layoutVersion", 0, int) == LAYOUT_VERSION:
            self._default_state = self.dock_manager.saveState()
            self.dock_manager.restoreState(state)
        else:
            self._default_state = self.dock_manager.saveState()
        text = store.value("viewSettings")
        if isinstance(text, str):
            self.apply_view_settings(ViewSettings.from_json(text))

    def _save_settings(self) -> None:
        store = self._qsettings()
        store.setValue("geometry", self.saveGeometry())
        store.setValue("dockState", self.dock_manager.saveState())
        store.setValue("layoutVersion", LAYOUT_VERSION)
        if self.view is not None:
            store.setValue("viewSettings", self.view.settings.to_json())

    def _reset_layout(self) -> None:
        self.dock_manager.restoreState(self._default_state)
        for dock in self.docks.values():
            dock.toggleView(True)

    # -- file -----------------------------------------------------------------

    def _confirm_discard(self, title: str) -> bool:
        if not self.document.has_unsaved_changes():
            return True
        answer = QMessageBox.question(
            self,
            title,
            f"{self.document.title} has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _new(self) -> None:
        if self._confirm_discard("New"):
            self.document.new()

    def _open_dialog(self) -> None:
        if not self._confirm_discard("Open"):
            return
        name, _ = QFileDialog.getOpenFileName(self, "Open", "", FILE_FILTER)
        if name:
            self.open_path(Path(name))

    def open_path(
        self, path: Path, *, mask: Sequence[str] = (), load: bool = True
    ) -> bool:
        """Open ``path`` as a scene, or reference it into a new scene if foreign.

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
        self._select([str(prim_path)])

    def _save(self) -> None:
        if self.document.path is None:
            self._save_as_dialog()
            return
        self.document.save()
        self._refresh_title()

    def _save_as_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, "Save As", "", SAVE_FILTER)
        if not name:
            return
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(".usda")
        try:
            self.document.save_as(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Save As", str(exc))

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
        if not self._confirm_discard("Quit"):
            event.ignore()
            return
        self._save_settings()
        if self.view is not None:
            self.view.shutdown()
        self.dock_manager.deleteLater()
        event.accept()

    # -- selection and view ---------------------------------------------------

    def _validate(self) -> None:
        dock = self.docks["Validation"]
        dock.toggleView(True)
        dock.setAsCurrentTab()
        self.validation.run()

    def _show_interpreter(self) -> None:
        dock = self.docks["Interpreter"]
        dock.toggleView(True)
        dock.setAsCurrentTab()

    def _select(self, paths: list[str]) -> None:
        """Select ``paths`` everywhere, as if the user picked them."""
        self.outliner.set_selected_paths(paths)
        self._on_outliner_selection(paths)

    def _on_outliner_selection(self, paths: list[str]) -> None:
        if self.view is not None:
            self.view.set_selected_paths(paths)
        self.inspector.set_prim_path(paths[0] if paths else "")

    def _on_picked(self, path: str) -> None:
        modifiers = QApplication.keyboardModifiers()
        paths = [path] if path else []
        if modifiers & (
            Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
        ):
            current = self.outliner.selected_paths()
            if path in current:
                current.remove(path)
                paths = current
            else:
                paths = current + paths
        self._select(paths)

    def _look_through(self, path: str) -> None:
        if self.view is not None:
            self.view.set_camera_path(path)

    def _frame_selection(self) -> None:
        self._frame_paths(self.outliner.selected_paths())

    def _frame_paths(self, paths: list[str]) -> None:
        if self.view is not None:
            self.view.frame_paths(paths)

    def _on_render_timed(self, milliseconds: float) -> None:
        count = self.outliner.model.prim_count
        target = self.document.edit_target_layer.GetDisplayName()
        self._status.setText(
            f"{milliseconds:.1f} ms   {count} prims   edit target: {target}"
        )

    def _refresh_title(self, *_args: object) -> None:
        star = "*" if self.document.has_unsaved_changes() else ""
        self.setWindowTitle(f"{self.document.title}{star} - Stage Editor")


def main(argv: Sequence[str] | None = None) -> int:
    """Show the stage editor and run the Qt event loop.

    Args:
        argv: Arguments after the program name; the first is opened.
    """
    app = ensure_application()
    window = StageEditorWindow()
    window.show()
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        window.open_path(Path(args[0]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
