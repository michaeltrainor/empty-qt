"""Main window: Hydra view, outliner dock, timeline, and the File menu."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from mtqt import TimelineBar, apply_stylesheet, ensure_application
from stage_editor.document import USD_SUFFIXES, StageDocument
from stage_editor.hydra_view import HydraView, hydra_available
from stage_editor.outliner import Outliner

FILE_FILTER = "USD files (" + " ".join(f"*{s}" for s in USD_SUFFIXES) + ")"
SAVE_FILTER = "USD ASCII (*.usda);;USD binary (*.usdc);;USD (*.usd)"


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


class StageEditorWindow(QMainWindow):
    """The editor shell. Opens a file from the command line through :meth:`open_path`.

    Args:
        parent: Owning widget, normally none.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StageEditorWindow")
        apply_stylesheet(self, "base", "widgets")
        self.resize(1400, 900)
        self.document = StageDocument(self)
        self.view: HydraView | None = None
        if hydra_available():
            self.view = HydraView(self.document, self)
            self.setCentralWidget(self.view)
            self.view.pickedPath.connect(self._on_picked)
            self.view.renderTimed.connect(self._on_render_timed)
        else:
            missing = QLabel(
                "pxr.UsdImagingGL is not importable.\n"
                "Build OpenUSD with scripts/build_usd_imaging.cmd, then put its\n"
                "inst/Lib/site-packages on PYTHONPATH\n"
                "and inst/lib and inst/bin on PATH.",
                self,
            )
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setCentralWidget(missing)

        self.outliner = Outliner(self.document, self)
        self.outliner.selectedPathsChanged.connect(self._on_outliner_selection)
        dock = QDockWidget("Outliner", self)
        dock.setObjectName("OutlinerDock")
        dock.setWidget(self.outliner)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.timeline = TimelineBar(self)
        timeline_dock = QDockWidget("Timeline", self)
        timeline_dock.setObjectName("TimelineDock")
        timeline_dock.setWidget(self.timeline)
        timeline_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, timeline_dock)
        self.playback = StagePlayback(self.document, self.timeline, self)

        self._status = QLabel(self)
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.addPermanentWidget(self._status)
        self._install_menus()
        self.document.stageReplaced.connect(self._refresh_title)
        self.document.changed.connect(self._on_document_changed)
        self._refresh_title()

    # -- menus ----------------------------------------------------------------

    def _action(self, text: str, shortcut: str, slot, menu) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

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
        file_menu.addSeparator()
        self._action("&Quit", "Ctrl+Q", self.close, file_menu)
        view_menu = bar.addMenu("&View")
        self._action("Frame &Selection", "F", self._frame_selection, view_menu)
        self._action("Frame &All", "A", self._frame_all, view_menu)

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

    def open_path(self, path: Path) -> bool:
        """Open ``path`` as a scene, or reference it into a new scene if foreign.

        Args:
            path: A USD file.

        Returns:
            ``True`` when something opened; a message box reports failures.
        """
        try:
            self.document.open(path)
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
        self.outliner.set_selected_paths([str(prim_path)])
        self._on_outliner_selection([str(prim_path)])

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

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard("Quit"):
            event.ignore()
            return
        if self.view is not None:
            self.view.shutdown()
        event.accept()

    # -- selection and view ---------------------------------------------------

    def _on_outliner_selection(self, paths: list[str]) -> None:
        if self.view is not None:
            self.view.set_selected_paths(paths)

    def _on_picked(self, path: str) -> None:
        paths = [path] if path else []
        self.outliner.set_selected_paths(paths)
        if self.view is not None:
            self.view.set_selected_paths(paths)

    def _frame_selection(self) -> None:
        if self.view is not None:
            self.view.frame_paths(self.outliner.selected_paths())

    def _frame_all(self) -> None:
        if self.view is not None:
            self.view.frame_paths([])

    def _on_render_timed(self, milliseconds: float) -> None:
        count = self.outliner.model.prim_count
        self._status.setText(f"{milliseconds:.1f} ms   {count} prims")

    def _on_document_changed(self, _resynced: list[str], _info: list[str]) -> None:
        self._refresh_title()

    def _refresh_title(self) -> None:
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
