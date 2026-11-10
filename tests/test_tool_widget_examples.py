"""Construct the tool-widget example windows without starting an event loop."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from mtqt import ensure_application

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _load(module_name: str) -> ModuleType:
    path = _EXAMPLES / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"mtqt_examples.{module_name}", path)
    if spec is None or spec.loader is None:
        msg = f"Cannot load {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tool_example_windows_construct(qtbot) -> None:
    ensure_application()
    windows = [
        _load("command_palette").CommandPaletteWindow(),
        _load("toast_stack").ToastStackWindow(),
        _load("range_slider").RangeSliderWindow(),
        _load("breadcrumb_bar").BreadcrumbBarWindow(),
        _load("tag_input").TagInputWindow(),
        _load("gallery").GalleryWindow(),
    ]
    for window in windows:
        qtbot.addWidget(window)
