from __future__ import annotations

import importlib
import importlib.metadata

import pytest
from PySide6.QtWidgets import QApplication

import mtqt
from mtqt import ensure_application


def test_import_survives_missing_package_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = mtqt.__version__

    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(importlib.metadata, "version", missing)
            importlib.reload(mtqt)
            assert mtqt.__version__ == "0+unknown"
    finally:
        importlib.reload(mtqt)
    assert mtqt.__version__ == installed


def test_ensure_application_reuses_qtbot_app(qtbot) -> None:
    existing = QApplication.instance()
    assert existing is not None
    assert ensure_application() is existing
