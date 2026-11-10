"""BreadcrumbBar: silent crumbs, click emits a key, rebuild waits out the click."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from mtqt import BreadcrumbBar, Crumb

PATH = (
    Crumb("root", "Assets"),
    Crumb("hero", "Hero"),
    Crumb("body", "Body"),
)


def _bar(qtbot) -> BreadcrumbBar:
    bar = BreadcrumbBar()
    qtbot.addWidget(bar)
    return bar


def _button(bar: BreadcrumbBar, label: str) -> QPushButton:
    return next(
        button for button in bar.findChildren(QPushButton) if button.text() == label
    )


def test_set_crumbs_is_silent_and_round_trips(qtbot) -> None:
    bar = _bar(qtbot)
    assert bar.objectName() == "BreadcrumbBar"
    assert bar.accessibleName() == "Breadcrumb"
    emitted: list[str] = []
    bar.crumbActivated.connect(emitted.append)
    bar.set_crumbs(PATH)
    assert emitted == []
    assert bar.crumbs == PATH
    buttons = bar.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["Assets", "Hero", "Body"]
    assert buttons[-1].property("current") is True
    assert buttons[0].property("current") is False


def test_clicking_the_second_segment_emits_its_key(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_crumbs(PATH)
    with qtbot.waitSignal(bar.crumbActivated) as blocker:
        _button(bar, "Hero").click()
    assert blocker.args == ["hero"]
    assert bar.crumbs == PATH


def test_set_crumbs_during_click_rebuilds_after_the_click(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_crumbs((Crumb("a", "A"), Crumb("b", "B")))

    def on_activated(_key: str) -> None:
        bar.set_crumbs((Crumb("a", "A"), Crumb("b", "B"), Crumb("c", "C")))

    bar.crumbActivated.connect(on_activated)
    _button(bar, "B").click()
    assert bar.crumbs[-1].key == "c"
    assert all(button.text() != "C" for button in bar.findChildren(QPushButton))
    qtbot.waitUntil(
        lambda: any(button.text() == "C" for button in bar.findChildren(QPushButton))
    )
