"""TimelineBar: setters stay silent, user actions emit, drags are respected."""

from __future__ import annotations

from mtqt import TimelineBar


def _bar(qtbot) -> TimelineBar:
    bar = TimelineBar()
    qtbot.addWidget(bar)
    return bar


def test_empty_bar_is_disabled_and_clips_enable_it(qtbot) -> None:
    bar = _bar(qtbot)
    assert bar.objectName() == "TimelineBar"
    assert bar.clip == ""
    assert bar.clips == []
    assert not bar._play.isEnabled()
    assert not bar._slider.isEnabled()
    emitted: list[str] = []
    bar.clipChanged.connect(emitted.append)
    bar.set_clips(["Walk", "Run"], current="Run")
    assert emitted == []
    assert bar.clips == ["Walk", "Run"]
    assert bar.clip == "Run"
    assert bar._play.isEnabled()
    # The shown clip survives a list refresh that still contains it.
    bar.set_clips(["Idle", "Run", "Walk"])
    assert bar.clip == "Run"
    # A current clip not in the list falls back to the first.
    bar.set_clips(["Idle", "Walk"], current="Gone")
    assert bar.clip == "Idle"
    bar.set_clips([])
    assert bar.clip == ""
    assert not bar._stop.isEnabled()
    assert emitted == []


def test_user_clip_pick_emits(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_clips(["Walk", "Run"])
    with qtbot.waitSignal(bar.clipChanged) as blocker:
        bar._clips.setCurrentIndex(1)
    assert blocker.args == ["Run"]


def test_time_setters_are_silent_and_scrubbing_emits(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_clips(["Walk"])
    scrubbed: list[float] = []
    bar.timeScrubbed.connect(scrubbed.append)
    bar.set_duration(4.0)
    assert bar.duration == 4.0
    assert bar._slider.maximum() == 4000
    bar.set_time(1.25)
    assert bar.time == 1.25
    assert bar._time.text() == "1.25 / 4.00 s"
    assert scrubbed == []
    with qtbot.waitSignal(bar.timeScrubbed) as blocker:
        bar._slider.setValue(2500)
    assert blocker.args == [2.5]
    assert bar._time.text() == "2.50 / 4.00 s"
    # Shrinking the duration clamps the slider without a scrub signal.
    scrubbed.clear()
    bar.set_duration(2.0)
    assert bar.time == 2.0
    assert scrubbed == []
    assert bar._time.text() == "2.00 / 2.00 s"


def test_set_time_leaves_a_held_slider_alone(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_clips(["Walk"])
    bar.set_duration(4.0)
    bar._slider.setSliderDown(True)
    bar._slider.blockSignals(True)
    bar._slider.setValue(1000)
    bar._slider.blockSignals(False)
    bar.set_time(3.0)
    assert bar._slider.value() == 1000
    assert bar._time.text() == "3.00 / 4.00 s"
    bar._slider.setSliderDown(False)
    bar.set_time(3.0)
    assert bar._slider.value() == 3000


def test_transport_buttons_emit_and_setters_do_not(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_clips(["Walk"])
    with qtbot.waitSignal(bar.playToggled) as blocker:
        bar._play.click()
    assert blocker.args == [True]
    assert bar.playing
    assert bar._play.icon().cacheKey() == bar._pause_icon.cacheKey()
    played: list[bool] = []
    bar.playToggled.connect(played.append)
    bar.set_playing(False)
    assert not bar.playing
    assert played == []
    assert bar._play.icon().cacheKey() == bar._play_icon.cacheKey()
    with qtbot.waitSignal(bar.stopRequested):
        bar._stop.click()
    assert bar.loop
    with qtbot.waitSignal(bar.loopToggled) as blocker:
        bar._loop.click()
    assert blocker.args == [False]
    looped: list[bool] = []
    bar.loopToggled.connect(looped.append)
    bar.set_loop(True)
    assert bar.loop
    assert looped == []


def test_speed_box_offers_rates_and_emits(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_clips(["Walk"])
    assert bar.speed == 1.0
    with qtbot.waitSignal(bar.speedChanged) as blocker:
        bar._speed.setCurrentIndex(bar._speed.findData(2.0))
    assert blocker.args == [2.0]
    speeds: list[float] = []
    bar.speedChanged.connect(speeds.append)
    bar.set_speed(0.3)
    assert bar.speed == 0.25
    assert speeds == []
    custom = TimelineBar(speeds=(0.5, 1.5))
    qtbot.addWidget(custom)
    assert custom.speed == 0.5  # no 1.0 offered: the first rate is shown
