"""Standalone window for :class:`~mtqt.PropertiesPanel`.

Shows a small pydantic model driven by the panel. The panel only knows the
model schema — not meshes or the scene viewport example.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from PySide6.QtWidgets import QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from mtqt import PropertiesPanel, apply_stylesheet, ensure_application


class DemoProps(BaseModel):
    """Toy profile used only by this example."""

    name: str = Field(frozen=True, title="Name")
    visible: bool = Field(default=True, title="Visible")
    size: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        title="Size",
        description="Uniform scale.",
    )
    mode: Literal["preview", "final"] = Field(default="preview", title="Mode")
    offset: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        title="Offset",
    )
    color: str = Field(
        default="#4a8fd4",
        title="Color",
        json_schema_extra={"mtqt_editor": "color"},
    )
    output: Path = Field(default=Path("/tmp/demo.out"), title="Output")

    @field_validator("color")
    @classmethod
    def _color_must_be_hex(cls, value: str) -> str:
        if not value.startswith("#") or len(value) not in {4, 7}:
            msg = "color must be #rgb or #rrggbb"
            raise ValueError(msg)
        return value


class PropertiesPanelWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PropertiesPanel")
        self.resize(480, 560)

        central = QWidget(self)
        panel = PropertiesPanel(central, empty_message="No object")
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        panel.set_field_editable("name", False)
        panel.set_editable_check(lambda field, _model: field != "output")
        panel.register_handler(
            lambda field, value, model: log.appendPlainText(
                f"handler {field}={value!r}\n{model.model_dump_json(indent=2)}"
            )
        )
        panel.register_field_handler(
            "color",
            lambda field, value, _model: log.appendPlainText(
                f"color handler: {value!r}"
            ),
        )
        panel.set_model(DemoProps(name="demo_item"))
        panel.fieldChanged.connect(
            lambda field, value, _model: log.appendPlainText(
                f"fieldChanged: {field}={value!r}"
            )
        )
        panel.validationFailed.connect(
            lambda field, message: log.appendPlainText(
                f"validationFailed: {field}: {message}"
            )
        )

        layout = QVBoxLayout(central)
        layout.addWidget(panel, stretch=1)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = PropertiesPanelWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
