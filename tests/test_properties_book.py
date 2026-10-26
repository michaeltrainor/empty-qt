"""Tests for :class:`~mtqt.PropertiesBook`."""

from __future__ import annotations

from pydantic import BaseModel, Field
from PySide6.QtWidgets import QDoubleSpinBox

from mtqt import PropertiesBook


class _Transform(BaseModel):
    name: str = Field(frozen=True, title="Name")
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)


class _Mesh(BaseModel):
    name: str = Field(frozen=True, title="Name")
    color: str = Field(default="#ffffff", json_schema_extra={"mtqt_editor": "color"})


def test_properties_book_empty(qtbot) -> None:
    book = PropertiesBook(empty_message="Pick something")
    qtbot.addWidget(book)
    assert book.model is None
    assert book.models == []
    assert not book._empty.isHidden()
    assert book._empty.text() == "Pick something"


def test_properties_book_single_page_hides_tab_bar(qtbot) -> None:
    book = PropertiesBook()
    qtbot.addWidget(book)
    book.set_pages([("Transform", _Transform(name="root"))])
    assert book._tabs.tabBar().isHidden()
    assert book.model is not None
    assert book.model.name == "root"


def test_properties_book_tabs_and_commits(qtbot) -> None:
    book = PropertiesBook()
    qtbot.addWidget(book)
    events: list[tuple[str, object, type]] = []

    def on_field(field: str, value: object, model: BaseModel) -> None:
        events.append((field, value, type(model)))

    book.register_handler(on_field)
    book.set_pages(
        [
            ("Transform", _Transform(name="crate", position=(1.0, 0.0, 0.0))),
            ("Mesh", _Mesh(name="crateShape", color="#c47a3a")),
        ]
    )
    assert book._tabs.count() == 2
    assert not book._tabs.tabBar().isHidden()
    assert len(book.models) == 2

    book._tabs.setCurrentIndex(0)
    vec_spin = book._panels[0]._form_host.findChildren(QDoubleSpinBox)[0]
    with qtbot.waitSignal(book.fieldChanged) as blocker:
        vec_spin.setValue(2.5)
    assert blocker.args[0] == "position"
    assert isinstance(blocker.args[2], _Transform)
    assert ("position", blocker.args[1], _Transform) in [
        (field, value, cls) for field, value, cls in events
    ]

    book.clear()
    assert book.model is None
    assert book._tabs.count() == 0
