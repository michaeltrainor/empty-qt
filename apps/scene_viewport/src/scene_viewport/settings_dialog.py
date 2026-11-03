"""Preferences dialog for scene viewport application settings."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QWidget,
)

from mtqt.widgets.properties_panel import PropertiesPanel
from scene_viewport.settings import AppSettings


class SettingsDialog(QDialog):
    """Modal dialog that edits an :class:`AppSettings` via PropertiesPanel.

    On Accept, ``settings`` holds the committed model. Cancel discards edits.
    """

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog around a copy of ``settings``.

        Args:
            settings: Initial values shown in the panel.
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(360)
        self._draft = settings.model_copy(deep=True)
        self.settings = settings

        self._panel = PropertiesPanel(parent=self)
        self._panel.set_model(self._draft)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._panel)
        layout.addWidget(buttons)

    def accept(self) -> None:
        """Commit the draft model and close."""
        self.settings = self._draft
        super().accept()
