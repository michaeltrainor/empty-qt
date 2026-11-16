"""Python interpreter dock, like usdview's: a console bound to the open stage.

The console's namespace holds the USD modules and an ``api`` object
(:class:`EditorApi`) for the stage, the selection, and the time, so a
script can inspect or edit the scene the way usdview's ``usdviewApi`` does.
"""

from __future__ import annotations

import code
import contextlib
import io
import sys
import traceback
from collections.abc import Callable, Sequence

from pxr import Gf, Kind, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdShade, UsdSkel, Vt
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFontDatabase, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from stage_editor.document import StageDocument

BANNER = (
    "Stage editor Python console. `api.stage`, `api.prim`, `api.selection`, "
    "`api.time`, `api.select([...])`; Usd, Sdf, UsdGeom, Gf, Vt, ... are imported."
)


class EditorApi:
    """What console scripts reach: the document, stage, selection, and time.

    Args:
        document: The open document.
        get_selection: Returns the selected prim paths.
        set_selection: Selects prim paths in the editor.
    """

    def __init__(
        self,
        document: StageDocument,
        get_selection: Callable[[], list[str]],
        set_selection: Callable[[list[str]], None],
    ) -> None:
        self.document = document
        self._get_selection = get_selection
        self._set_selection = set_selection

    @property
    def stage(self) -> Usd.Stage:
        """The open stage."""
        return self.document.stage

    @property
    def selection(self) -> list[str]:
        """The selected prim paths."""
        return self._get_selection()

    @property
    def prim(self) -> Usd.Prim | None:
        """The first selected prim, or ``None``."""
        paths = self._get_selection()
        return self.stage.GetPrimAtPath(paths[0]) if paths else None

    @property
    def time(self) -> float:
        """The current time code; assign to move it."""
        return self.document.time

    @time.setter
    def time(self, value: float) -> None:
        self.document.time = value

    def select(self, paths: Sequence[str]) -> None:
        """Select prims by path.

        Args:
            paths: Prim paths as strings.
        """
        self._set_selection([str(p) for p in paths])


class _Console(code.InteractiveConsole):
    """An ``InteractiveConsole`` whose output goes to a callback."""

    def __init__(
        self, namespace: dict[str, object], write: Callable[[str], None]
    ) -> None:
        super().__init__(namespace)
        self._write = write

    def write(self, data: str) -> None:
        self._write(data)

    # The base class hands tracebacks to sys.excepthook when a host (Qt, a
    # test runner) replaced it; print them in the console instead.
    def showtraceback(self) -> None:
        kind, value, tb = sys.exc_info()
        if kind is None or value is None:
            return
        skipped = tb.tb_next if tb is not None else None
        self.write("".join(traceback.format_exception(kind, value, skipped)))

    def showsyntaxerror(self, filename: str | None = None, **_kwargs: object) -> None:
        kind, value, _tb = sys.exc_info()
        if kind is None or value is None:
            return
        self.write("".join(traceback.format_exception_only(kind, value)))


class Interpreter(QWidget):
    """A Python console: an output pane and an input line with history.

    Up and Down walk the history. A statement that needs more lines (a
    ``for`` or ``def``) prompts with ``...`` until an empty line ends it.

    Args:
        api: The object bound as ``api`` in the namespace.
        parent: Owning widget.
    """

    def __init__(self, api: EditorApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api = api
        namespace: dict[str, object] = {
            "api": api,
            "Usd": Usd,
            "Sdf": Sdf,
            "UsdGeom": UsdGeom,
            "UsdShade": UsdShade,
            "UsdLux": UsdLux,
            "UsdSkel": UsdSkel,
            "Gf": Gf,
            "Vt": Vt,
            "Tf": Tf,
            "Kind": Kind,
        }
        self.namespace = namespace
        self._console = _Console(namespace, self._append)
        self._history: list[str] = []
        self._history_pos = 0
        self._more = False

        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._output = QPlainTextEdit(self)
        self._output.setReadOnly(True)
        self._output.setFont(font)
        self._output.setMaximumBlockCount(5000)
        self._input = QLineEdit(self)
        self._input.setFont(font)
        self._input.setPlaceholderText(">>> ")
        self._input.returnPressed.connect(self._on_return)
        self._input.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._output, 1)
        layout.addWidget(self._input)
        self._append(BANNER + "\n")

    @property
    def output(self) -> str:
        """Everything the console printed so far."""
        return self._output.toPlainText()

    def execute(self, line: str) -> bool:
        """Run one line as if typed, echoing it and capturing output.

        Args:
            line: Source text for one line.

        Returns:
            Whether the console waits for more lines.
        """
        prompt = "... " if self._more else ">>> "
        self._append(f"{prompt}{line}\n")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            self._more = self._console.push(line)
        if buffer.getvalue():
            self._append(buffer.getvalue())
        self._input.setPlaceholderText("... " if self._more else ">>> ")
        return self._more

    def _append(self, text: str) -> None:
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _on_return(self) -> None:
        line = self._input.text()
        self._input.clear()
        if line.strip():
            self._history.append(line)
        self._history_pos = len(self._history)
        self.execute(line)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._input and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            if event.key() == Qt.Key.Key_Up and self._history:
                self._history_pos = max(0, self._history_pos - 1)
                self._input.setText(self._history[self._history_pos])
                return True
            if event.key() == Qt.Key.Key_Down and self._history:
                self._history_pos = min(len(self._history), self._history_pos + 1)
                text = (
                    self._history[self._history_pos]
                    if self._history_pos < len(self._history)
                    else ""
                )
                self._input.setText(text)
                return True
        return super().eventFilter(watched, event)
