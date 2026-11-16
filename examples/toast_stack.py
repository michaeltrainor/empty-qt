"""Standalone window for :class:`~mtqt.ToastStack`.

Shows info, warning, and error buttons and a read-only log of
``toastDismissed``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mtqt import ToastLevel, ToastStack, apply_stylesheet, ensure_application


class ToastStackWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ToastStack")
        self.resize(640, 420)

        central = QWidget(self)
        log = QPlainTextEdit(central)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        stack = ToastStack(central)
        info = QPushButton("Info", central)
        warning = QPushButton("Warning", central)
        error = QPushButton("Error", central)
        info.clicked.connect(lambda: stack.show_toast("Saved the scene"))
        warning.clicked.connect(
            lambda: stack.show_toast(
                "This scene has unsaved changes",
                level=ToastLevel.WARNING,
            )
        )
        error.clicked.connect(
            lambda: stack.show_toast(
                "Could not save the scene",
                level=ToastLevel.ERROR,
            )
        )
        stack.toastDismissed.connect(
            lambda toast_id: log.appendPlainText(f"toastDismissed: {toast_id}")
        )

        buttons = QHBoxLayout()
        buttons.addWidget(info)
        buttons.addWidget(warning)
        buttons.addWidget(error)
        buttons.addStretch(1)

        layout = QVBoxLayout(central)
        layout.addLayout(buttons)
        layout.addWidget(stack, stretch=1)
        layout.addWidget(log, stretch=1)
        self.setCentralWidget(central)
        apply_stylesheet(self, "base", "widgets")


def main() -> int:
    app = ensure_application()
    window = ToastStackWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
