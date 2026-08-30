"""Standalone gallery that imports mtqt like a production tool."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListView,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mtqt import (
    LabeledLineEdit,
    ObjectListModel,
    ObjectTableModel,
    PathPicker,
    apply_stylesheet,
    ensure_application,
)


@dataclass
class Person:
    name: str
    role: str

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


class GalleryWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mtqt gallery")
        self.resize(720, 520)

        self._use_qss = QCheckBox("Use mtqt stylesheet", self)
        self._use_qss.setChecked(True)
        self._use_qss.toggled.connect(self._on_qss_toggled)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_widgets_tab(), "Widgets")
        tabs.addTab(self._build_list_tab(), "List")
        tabs.addTab(self._build_table_tab(), "Table")

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.addWidget(self._use_qss)
        layout.addWidget(tabs)
        self.setCentralWidget(central)

        self._on_qss_toggled(True)

    def _on_qss_toggled(self, checked: bool) -> None:
        if checked:
            apply_stylesheet(self, "base", "widgets")
        else:
            self.setStyleSheet("")

    def _build_widgets_tab(self) -> QWidget:
        page = QWidget(self)
        log = QPlainTextEdit(page)
        log.setReadOnly(True)
        log.setPlaceholderText("Signal log")

        name = LabeledLineEdit("Name", page, placeholder="Required")
        path = PathPicker(page, caption="Pick a file")

        def log_line(message: str) -> None:
            log.appendPlainText(message)

        name.textChanged.connect(lambda text: log_line(f"textChanged: {text!r}"))
        name.validityChanged.connect(lambda ok: log_line(f"validityChanged: {ok}"))
        path.pathChanged.connect(lambda value: log_line(f"pathChanged: {value!r}"))

        layout = QVBoxLayout(page)
        layout.addWidget(name)
        layout.addWidget(path)
        layout.addWidget(log, stretch=1)
        return page

    def _build_list_tab(self) -> QWidget:
        page = QWidget(self)
        people = [
            Person("Ada", "TD"),
            Person("Lin", "Pipeline"),
        ]
        model = ObjectListModel(people, parent=page, display_attr="name")
        view = QListView(page)
        view.setModel(model)

        add = QPushButton("Add row", page)
        remove = QPushButton("Remove selected", page)

        def on_add() -> None:
            model.append(Person(f"User {model.rowCount() + 1}", "Artist"))

        def on_remove() -> None:
            indexes = view.selectedIndexes()
            if indexes:
                model.remove_row(indexes[0].row())

        add.clicked.connect(on_add)
        remove.clicked.connect(on_remove)

        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()

        layout = QVBoxLayout(page)
        layout.addWidget(view)
        layout.addLayout(buttons)
        return page

    def _build_table_tab(self) -> QWidget:
        page = QWidget(self)
        people = [
            Person("Ada", "TD"),
            Person("Lin", "Pipeline"),
        ]
        model = ObjectTableModel(people, columns=("name", "role"), parent=page)
        view = QTableView(page)
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.horizontalHeader().setStretchLastSection(True)

        snapshot = QPlainTextEdit(page)
        snapshot.setReadOnly(True)

        def refresh_snapshot() -> None:
            lines = [
                f"{model.row_at(row).name}: {model.row_at(row).role}"  # type: ignore[union-attr]
                for row in range(model.rowCount())
            ]
            snapshot.setPlainText("\n".join(lines))

        model.dataChanged.connect(lambda *_args: refresh_snapshot())
        refresh_snapshot()

        layout = QVBoxLayout(page)
        layout.addWidget(view, stretch=2)
        layout.addWidget(snapshot, stretch=1)
        return page


def main() -> int:
    app = ensure_application()
    window = GalleryWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
