"""Tests for rolling (trailing-day) range-bar sizing — AUDIT item A4.

Every expected value is derived by hand from the definitions, never read
back from the implementation. Timestamps are built from explicit day and
minute indices so the arithmetic is checkable on paper.
"""
import pytest

from src.rangebars.rolling import (
    DAY_MS, utc_day, mean_1m_range_by_day, rolling_range_sizes,
    schedule_from_ticks,
)

MIN_MS = 60_000


def ts(day: int, minute: int = 0, ms: int = 0) -> int:
    return day * DAY_MS + minute * MIN_MS + ms


# --- utc_day ------------------------------------------------------------------

def test_utc_day_boundaries():
    assert utc_day(0) == 0
    assert utc_day(DAY_MS - 1) == 0
    assert utc_day(DAY_MS) == 1


# --- mean_1m_range_by_day -----------------------------------------------------

def test_mean_range_single_day_two_minutes():
    """Day 10, minute 0: prices 100, 103 -> range 3. Minute 1: 200, 201 ->
    range 1. Mean = (3 + 1) / 2 = 2.0."""
    ticks = [
        (100.0, ts(10, 0)), (103.0, ts(10, 0, 500)),
        (200.0, ts(10, 1)), (201.0, ts(10, 1, 500)),
    ]
    assert mean_1m_range_by_day(ticks) == {10: 2.0}


def test_zero_range_minute_is_included():
    """Minute 0 range 4, minute 1 all one price -> range 0. Mean = 2.0,
    not 4.0. (Documented choice, mirrors calibrate.py's headline.)"""
    ticks = [
        (100.0, ts(5, 0)), (104.0, ts(5, 0, 1)),
        (50.0, ts(5, 1)), (50.0, ts(5, 1, 1)),
    ]
    assert mean_1m_range_by_day(ticks) == {5: 2.0}


def test_days_are_separated():
    """Day 1 mean = 10, day 2 mean = 2. Grouping must not pool them."""
    ticks = [
        (100.0, ts(1, 0)), (110.0, ts(1, 0, 1)),
        (100.0, ts(2, 0)), (102.0, ts(2, 0, 1)),
    ]
    assert mean_1m_range_by_day(ticks) == {1: 10.0, 2: 2.0}


def test_day_split_across_the_stream_is_grouped_by_ts_not_order():
    """A file straddling midnight: the day comes from the timestamp."""
    ticks = [
        (100.0, ts(1, 1439)), (106.0, ts(1, 1439, 1)),   # day 1, last minute
        (200.0, ts(2, 0)), (200.0, ts(2, 0, 1)),          # day 2, range 0
    ]
    assert mean_1m_range_by_day(ticks) == {1: 6.0, 2: 0.0}


def test_empty_stream():
    assert mean_1m_range_by_day([]) == {}


# --- rolling_range_sizes: the lookahead guarantee -----------------------------

def test_day_is_sized_by_the_previous_day():
    """means: day 1 -> 10, day 2 -> 20, day 3 -> 30. pct = 0.3.
    Day 1: nothing precedes it -> absent.
    Day 2: 0.3 * mean(day 1) = 3.0.
    Day 3: 0.3 * mean(day 2) = 6.0.   <- NOT 9.0, which would be day 3 itself."""
    means = {1: 10.0, 2: 20.0, 3: 30.0}
    assert rolling_range_sizes(means, pct=0.3) == {2: 3.0, 3: 6.0}


def test_earliest_day_never_gets_a_size():
    assert rolling_range_sizes({7: 100.0}, pct=0.3) == {}


def test_gap_is_not_silently_bridged():
    """Day 5 present, day 6 missing, day 7 present. With the default
    max_staleness_days=1, day 7's predecessor (day 6) has no data, so day 7
    gets NO size — it must not silently inherit day 5's."""
    means = {5: 10.0, 7: 40.0}
    assert rolling_range_sizes(means, pct=0.3) == {}


def test_gap_bridged_only_when_explicitly_allowed():
    """Same data, staleness 2: day 7 may reach back to day 5. 0.3*10 = 3.0."""
    means = {5: 10.0, 7: 40.0}
    assert rolling_range_sizes(means, pct=0.3, max_staleness_days=2) == {7: 3.0}


def test_nearest_prior_day_wins_when_several_are_allowed():
    """days 4 (mean 10) and 5 (mean 100) both precede day 6 within staleness
    2. Day 6 must use day 5, the most recent: 0.3 * 100 = 30.0."""
    means = {4: 10.0, 5: 100.0, 6: 999.0}
    got = rolling_range_sizes(means, pct=0.3, max_staleness_days=2)
    assert got[6] == 30.0


def test_staleness_zero_is_rejected_as_lookahead():
    with pytest.raises(ValueError, match="lookahead"):
        rolling_range_sizes({1: 10.0}, max_staleness_days=0)


def test_bad_pct_rejected():
    with pytest.raises(ValueError):
        rolling_range_sizes({1: 10.0, 2: 10.0}, pct=0.0)


def test_flat_prior_day_yields_no_size_rather_than_zero():
    """mean 0 -> range_size 0 would close a bar on every tick. Skip the day."""
    means = {1: 0.0, 2: 10.0}
    assert rolling_range_sizes(means, pct=0.3) == {}


# --- tick rounding ------------------------------------------------------------

def test_tick_rounding():
    """0.3 * 42.83 = 12.849 -> at tick 0.1 that is 128 ticks -> 12.8."""
    means = {1: 42.83, 2: 1.0}
    got = rolling_range_sizes(means, pct=0.3, tick=0.1)
    assert got[2] == pytest.approx(12.8)


def test_tick_rounding_never_yields_zero_ticks():
    """0.3 * 0.1 = 0.03, below one tick of 0.1 -> clamped to 1 tick."""
    means = {1: 0.1, 2: 1.0}
    got = rolling_range_sizes(means, pct=0.3, tick=0.1)
    assert got[2] == pytest.approx(0.1)


def test_bad_tick_rejected():
    with pytest.raises(ValueError):
        rolling_range_sizes({1: 10.0, 2: 10.0}, tick=0.0)


# --- schedule_from_ticks ------------------------------------------------------

def test_schedule_from_ticks_end_to_end():
    """Day 1: one minute, range 10 -> mean 10. Day 2: one minute, range 40.
    Schedule: day 2 = 0.3 * 10 = 3.0. Day 3 has no ticks, so no entry.
    Day 1 has no predecessor, so no entry."""
    ticks = [
        (100.0, ts(1, 0)), (110.0, ts(1, 0, 1)),
        (100.0, ts(2, 0)), (140.0, ts(2, 0, 1)),
    ]
    assert schedule_from_ticks(ticks, pct=0.3) == {2: 3.0}
