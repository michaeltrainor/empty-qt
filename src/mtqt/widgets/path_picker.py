"""Path picker: nested widgets, ``pathlib.Path``, and ``QFileDialog``."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget


class PathPicker(QWidget):
    """Line edit plus browse button that exposes a ``pathlib.Path``.

    Qt file dialogs return strings. This widget converts at that boundary.
    ``pathChanged`` emits a string because ``Path`` is not a Qt metatype;
    read :attr:`path` for the Python ``Path`` value.

    Args:
        parent: Qt parent widget.
        caption: Dialog window title.
        file_filter: ``QFileDialog`` filter, ignored in directory mode.
        directory_mode: When true, pick a directory instead of a file.
    """

    pathChanged = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        caption: str = "Select path",
        file_filter: str = "All files (*)",
        directory_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PathPicker")
        self.setAccessibleName("Path picker")
        self._caption = caption
        self._file_filter = file_filter
        self._directory_mode = directory_mode
        self._path: Path | None = None

        self._edit = QLineEdit(self)
        self._browse = QPushButton("Browse…", self)
        self._browse.setObjectName("PathPickerBrowse")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit, stretch=1)
        layout.addWidget(self._browse)

        self._edit.editingFinished.connect(self._on_edit_finished)
        self._browse.clicked.connect(self._on_browse)

    @property
    def path(self) -> Path | None:
        """Selected path, or ``None`` when the field is empty."""
        return self._path

    @path.setter
    def path(self, value: Path | str | None) -> None:
        new_path = None if value in (None, "") else Path(value)
        self._set_path(new_path, emit=True)

    def _set_path(self, new_path: Path | None, *, emit: bool) -> None:
        if new_path == self._path:
            text = "" if new_path is None else str(new_path)
            if self._edit.text() != text:
                self._edit.setText(text)
            return
        self._path = new_path
        text = "" if new_path is None else str(new_path)
        if self._edit.text() != text:
            self._edit.setText(text)
        if emit:
            self.pathChanged.emit(text)

    def _on_edit_finished(self) -> None:
        raw = self._edit.text().strip()
        self._set_path(Path(raw) if raw else None, emit=True)

    def _on_browse(self) -> None:
        start = str(self._path) if self._path is not None else ""
        if self._directory_mode:
            chosen = QFileDialog.getExistingDirectory(self, self._caption, start)
        else:
            chosen, _ok = QFileDialog.getOpenFileName(
                self,
                self._caption,
                start,
                self._file_filter,
            )
        if chosen:
            self.path = Path(chosen)
