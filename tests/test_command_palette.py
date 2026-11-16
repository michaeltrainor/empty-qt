"""CommandPalette: silent updates, subsequence rank, activation emits an id."""

from __future__ import annotations

from PySide6.QtCore import Qt

from mtqt import Command, CommandPalette

COMMANDS = (
    Command("open-scene", "Open Scene", "Load a scene file"),
    Command("save-scene", "Save Scene", "Write the current scene"),
    Command("preferences", "Preferences", "Edit tool settings"),
    Command("frame-all", "Frame All", "Fit the view"),
)


def _palette(qtbot) -> CommandPalette:
    palette = CommandPalette()
    qtbot.addWidget(palette)
    return palette


def test_set_commands_is_silent_and_keeps_the_query(qtbot) -> None:
    palette = _palette(qtbot)
    assert palette.objectName() == "CommandPalette"
    emitted: list[str] = []
    palette.commandTriggered.connect(emitted.append)
    palette.set_query("sc")
    palette.set_commands(COMMANDS)
    assert palette.query == "sc"
    assert [command.id for command in palette.matches] == ["open-scene", "save-scene"]
    assert emitted == []


def test_empty_query_returns_commands_in_original_order(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    palette.set_query("sc")
    palette.set_query("")
    assert palette.matches == COMMANDS


def test_tighter_subsequence_ranks_the_compact_title_first(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(
        (
            Command("spread", "Search Catalog"),
            Command("tight", "Scene"),
        )
    )
    palette.set_query("sc")
    assert [command.id for command in palette.matches] == ["tight", "spread"]


def test_activating_the_current_row_emits_its_id(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    palette.set_query("pref")
    palette.show()
    item = palette._list.currentItem()
    assert item is not None
    with qtbot.waitSignal(palette.commandTriggered) as blocker:
        palette._list.itemActivated.emit(item)
    assert blocker.args == ["preferences"]


def test_enter_uses_the_first_match_when_none_is_current(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    palette.set_query("sc")
    palette.show()
    palette._list.setCurrentRow(-1)
    with qtbot.waitSignal(palette.commandTriggered) as blocker:
        qtbot.keyClick(palette._edit, Qt.Key.Key_Return)
    assert blocker.args == ["open-scene"]


def test_down_then_enter_emits_the_next_id(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    palette.set_query("sc")
    palette.show()
    qtbot.keyClick(palette._edit, Qt.Key.Key_Down)
    with qtbot.waitSignal(palette.commandTriggered) as blocker:
        qtbot.keyClick(palette._edit, Qt.Key.Key_Return)
    assert blocker.args == ["save-scene"]


def test_escape_clears_the_query_without_emitting(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    palette.set_query("sc")
    palette.show()
    emitted: list[str] = []
    palette.commandTriggered.connect(emitted.append)
    qtbot.keyClick(palette._edit, Qt.Key.Key_Escape)
    assert palette.query == ""
    assert palette.matches == COMMANDS
    assert emitted == []


def test_enter_on_no_matches_does_not_emit(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    palette.set_query("zzz")
    palette.show()
    emitted: list[str] = []
    palette.commandTriggered.connect(emitted.append)
    qtbot.keyClick(palette._edit, Qt.Key.Key_Return)
    qtbot.keyClick(palette._edit, Qt.Key.Key_Down)
    assert palette.matches == ()
    assert emitted == []


def test_set_commands_during_activation_rebuilds_after_the_signal(qtbot) -> None:
    palette = _palette(qtbot)
    palette.set_commands(COMMANDS)
    seen: list[str] = []

    def on_trigger(command_id: str) -> None:
        seen.append(command_id)
        palette.set_commands((Command("next", "Next"),))

    palette.commandTriggered.connect(on_trigger)
    item = palette._list.item(0)
    assert item is not None
    palette._list.itemActivated.emit(item)
    assert seen == ["open-scene"]
    assert palette.matches == COMMANDS
    qtbot.waitUntil(lambda: palette.matches == (Command("next", "Next"),))
