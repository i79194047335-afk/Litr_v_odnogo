"""Replay harness tests. Expected values hand-computed independently.

The most important tests here are not arithmetic — they pin the LOOKAHEAD
DISCIPLINE and EVENT ORDER, which are the whole point of Slice 1."""
import math

import pytest

from src.backtest.replay import MinuteCandle, MinuteCandleBuilder, Replay


# ---------------------------------------------------------------------------
# MinuteCandleBuilder
# ---------------------------------------------------------------------------

def test_candle_closes_only_when_later_minute_tick_arrives():
    b = MinuteCandleBuilder()
    assert b.update(100.0, 0) is None
    assert b.update(102.0, 30_000) is None
    assert b.update(101.0, 59_999) is None          # still minute 0
    closed = b.update(103.0, 60_000)                # first tick of minute 1
    assert closed is not None
    assert closed.minute == 0
    assert closed.open == 100.0
    assert closed.high == 102.0
    assert closed.low == 100.0
    assert closed.close == 101.0
    assert closed.n_ticks == 3


def test_gap_minutes_produce_no_empty_candles():
    # tick in minute 0, then next tick in minute 5: exactly ONE candle closes
    # (minute 0) — minutes 1-4 had no trades and produce nothing.
    b = MinuteCandleBuilder()
    b.update(100.0, 10_000)
    closed = b.update(110.0, 5 * 60_000)
    assert closed.minute == 0
    # and the new open candle is minute 5
    still_open = b.flush()
    assert still_open.minute == 5
    assert still_open.open == 110.0


def test_backwards_time_raises():
    b = MinuteCandleBuilder()
    b.update(100.0, 120_000)     # minute 2
    with pytest.raises(ValueError):
        b.update(99.0, 30_000)   # minute 0 — corrupted input


def test_flush_returns_open_candle_without_closing():
    b = MinuteCandleBuilder()
    b.update(100.0, 0)
    b.update(105.0, 1_000)
    c = b.flush()
    assert c.minute == 0 and c.high == 105.0 and c.n_ticks == 2
    # flush() does not reset: a later-minute tick still closes minute 0
    closed = b.update(50.0, 60_000)
    assert closed.minute == 0


def test_candle_ts_properties():
    c = MinuteCandle(minute=2, open=1, high=1, low=1, close=1, n_ticks=1)
    assert c.start_ts == 120_000
    assert c.end_ts == 179_999


# ---------------------------------------------------------------------------
# Replay: EMA updates only on candle close (hand-computed)
# ---------------------------------------------------------------------------

def test_emas_update_only_on_minute_close_hand_computed():
    # ema_fast period=2 → k=2/3 ; ema_slow period=3 → k=0.5
    # minute 0 closes at 101, minute 1 closes at 103.
    r = Replay(range_size=1e9, ema_fast=2, ema_slow=3)  # huge range: no bars
    ticks = [
        (100.0, 0), (101.0, 30_000),          # minute 0 (close 101)
        (102.0, 60_000), (103.0, 90_000),     # minute 1 (close 103)
        (104.0, 120_000),                     # minute 2 begins → closes m1
    ]
    r.run(ticks)
    # After the stream: minute 0 and minute 1 have closed. Minute 2 is open.
    # EMA_fast: seed 101 → then 103*(2/3) + 101*(1/3) = 307/3
    assert math.isclose(r.ema_fast.value, 307 / 3, rel_tol=1e-12)
    # EMA_slow: seed 101 → then 103*0.5 + 101*0.5 = 102
    assert math.isclose(r.ema_slow.value, 102.0, rel_tol=1e-12)
    assert r.last_closed_minute.minute == 1


def test_ema_is_none_while_first_minute_still_open():
    r = Replay(range_size=1e9, ema_fast=2, ema_slow=3)
    r.run([(100.0, 0), (105.0, 59_999)])   # minute 0 never closes
    assert r.ema_fast.value is None
    assert r.ema_slow.value is None
    assert r.last_closed_minute is None


# ---------------------------------------------------------------------------
# Replay: lookahead discipline & event order
# ---------------------------------------------------------------------------

class Recorder:
    """Records every event with the harness state visible at that moment."""

    def __init__(self, replay: Replay):
        self.r = replay
        self.events: list[tuple] = []

    def on_tick(self, price, ts):
        self.events.append(("tick", ts))

    def on_minute_close(self, candle):
        self.events.append(("minute", candle.minute))

    def on_range_bar(self, bar):
        # snapshot what the strategy could see at this instant
        lc = self.r.last_closed_minute
        self.events.append(("bar", bar.end_ts, lc.minute if lc else None))


def test_bar_closing_inside_first_minute_sees_no_closed_candle():
    # range_size=1: ticks 100 → 101 close a bar inside minute 0.
    # At that instant NO 1m candle has closed — the strategy must see None.
    r = Replay(range_size=1.0, ema_fast=2, ema_slow=3)
    rec = Recorder(r)
    r.run([(100.0, 0), (101.0, 1_000)], rec)
    bar_events = [e for e in rec.events if e[0] == "bar"]
    assert len(bar_events) == 1
    assert bar_events[0][2] is None      # last_closed_minute was None


def test_minute_close_fires_before_bar_close_on_same_tick():
    # One tick that BOTH starts minute 1 (closing minute 0) AND closes a
    # range bar. The minute-close event must come first: that candle ended
    # before this tick's minute began, so the bar handler may see it.
    r = Replay(range_size=1.0, ema_fast=2, ema_slow=3)
    rec = Recorder(r)
    ticks = [
        (100.0, 0),          # minute 0, opens bar
        (100.2, 30_000),     # minute 0, bar still open (range 0.2 < 1.0)
        (101.5, 60_000),     # minute 1: closes candle 0 AND closes the bar
    ]
    r.run(ticks, rec)
    kinds = [e[0] for e in rec.events]
    # for the last tick the order must be: minute, tick, bar
    assert kinds[-3:] == ["minute", "tick", "bar"]
    # and the bar handler saw minute 0 as closed
    bar_ev = rec.events[-1]
    assert bar_ev[2] == 0


def test_bar_never_sees_a_minute_that_closed_after_it():
    # Longer random-ish walk: for every bar event, the visible last-closed
    # minute must have ended strictly before the bar's end timestamp.
    r = Replay(range_size=0.5, ema_fast=2, ema_slow=3)
    rec = Recorder(r)
    ticks = [
        (100.0, 0), (100.3, 20_000), (100.6, 40_000),
        (100.1, 70_000), (99.8, 100_000),
        (100.9, 130_000), (101.4, 150_000),
        (100.2, 190_000), (99.5, 210_000),
    ]
    r.run(ticks, rec)
    bar_events = [e for e in rec.events if e[0] == "bar"]
    assert bar_events, "walk should have closed at least one bar"
    for _, bar_end_ts, lc_minute in bar_events:
        if lc_minute is not None:
            candle_end = lc_minute * 60_000 + 59_999
            assert candle_end < bar_end_ts, (
                f"lookahead: bar ending {bar_end_ts} saw minute {lc_minute} "
                f"which ends {candle_end}"
            )


def test_strategy_hooks_are_optional():
    # A strategy object with no hooks at all must not crash the harness.
    class Empty:
        pass
    r = Replay(range_size=1.0)
    r.run([(100.0, 0), (101.5, 60_000)], Empty())
    assert r.n_ticks == 2
    assert len(r.bars) == 1


def test_run_without_strategy():
    r = Replay(range_size=1.0)
    r.run([(100.0, 0), (101.5, 1_000)])
    assert len(r.bars) == 1
    assert r.n_ticks == 2


# --- rolling range-size schedule (AUDIT item A4) -------------------------------

from src.rangebars.rolling import DAY_MS


def test_no_schedule_leaves_size_constant():
    """Default path must be unchanged: no switches recorded, size untouched."""
    r = Replay(range_size=1.0)
    r.run([(100.0, 0), (100.5, DAY_MS), (101.5, DAY_MS + 1)])
    assert r.range_size_changes == []
    assert r.range_builder.range_size == 1.0


def test_schedule_switches_size_at_utc_midnight():
    """Seed size 1.0; schedule sizes day 1 at 5.0. Ticks on day 0 must build
    bars at 1.0; the first tick of day 1 swaps the size before it is used."""
    r = Replay(range_size=1.0, range_size_schedule={1: 5.0})
    # day 0: 100 -> 101.0 spans 1.0 -> exactly one bar closes at size 1.0,
    #        the next bar opens at 101.0.
    # day 1: 101 -> 104.0 spans 3.0, below the new 5.0 -> no further bar.
    #        (Prices stay continuous on purpose: a jump would close bars via
    #        the builder's while-loop and hide what this test is checking.)
    r.run([(100.0, 0), (101.0, 1), (103.0, DAY_MS), (104.0, DAY_MS + 1)])
    assert r.range_size_changes == [(1, 5.0)]
    assert r.range_builder.range_size == 5.0
    assert len(r.bars) == 1


def test_day_absent_from_schedule_keeps_previous_size():
    """Day 1 sized, day 2 absent -> day 2 keeps 5.0, no second switch."""
    r = Replay(range_size=1.0, range_size_schedule={1: 5.0})
    r.run([(100.0, 0), (100.0, DAY_MS), (100.0, 2 * DAY_MS)])
    assert r.range_size_changes == [(1, 5.0)]
    assert r.range_builder.range_size == 5.0


def test_switch_happens_before_the_ticks_bar_update():
    """The first tick of the new day must already be measured against the new
    size. Seed 10.0, day 1 -> 1.0. Day 0 opens a bar at 100. The day-1 tick at
    101.0 spans 1.0: it closes a bar ONLY if the new size is already active."""
    r = Replay(range_size=10.0, range_size_schedule={1: 1.0})
    r.run([(100.0, 0), (101.0, DAY_MS)])
    assert len(r.bars) == 1


def test_redundant_schedule_entry_records_no_switch():
    """A schedule that names the size already active is not a change."""
    r = Replay(range_size=2.0, range_size_schedule={1: 2.0})
    r.run([(100.0, 0), (100.0, DAY_MS)])
    assert r.range_size_changes == []
