from __future__ import annotations

from PySide6.QtWidgets import QApplication

from mtqt import ensure_application


def test_ensure_application_reuses_qtbot_app(qtbot) -> None:
    existing = QApplication.instance()
    assert existing is not None
    assert ensure_application() is existing
