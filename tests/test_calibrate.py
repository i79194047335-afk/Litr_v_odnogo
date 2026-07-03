"""Calibration unit tests. Expected values hand-computed independently.

Minute bucketing is by t_ms // 60000 (60000 ms = 1 minute)."""
import math

import pytest

from src.rangebars.calibrate import (
    one_minute_ranges,
    calibrate,
    _round_to_tick,
)


# ---------------------------------------------------------------------------
# one_minute_ranges
# ---------------------------------------------------------------------------

def test_bucketing_by_minute():
    # Minute 0: 100,102,101 → range 2 ; Minute 1: 103,100 → range 3 ;
    # Minute 2: single 105 → range 0.
    ticks = [
        (100.0, 0), (102.0, 30_000), (101.0, 59_999),   # minute 0
        (103.0, 60_000), (100.0, 119_999),              # minute 1
        (105.0, 120_000),                               # minute 2
    ]
    ranges = sorted(one_minute_ranges(ticks))
    assert ranges == [0.0, 2.0, 3.0]


def test_minute_boundary_is_exclusive_at_60000():
    # t=59_999 is minute 0; t=60_000 is minute 1. A tick at each boundary
    # must land in different minutes.
    ticks = [(10.0, 59_999), (20.0, 60_000)]
    # two separate minutes, each single-tick → both range 0
    assert sorted(one_minute_ranges(ticks)) == [0.0, 0.0]


def test_single_tick_minute_has_zero_range():
    assert one_minute_ranges([(42.0, 12345)]) == [0.0]


def test_empty_input_gives_empty_list():
    assert one_minute_ranges([]) == []


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------

def test_calibrate_hand_computed():
    ranges = [2.0, 3.0, 0.0]
    s = calibrate(ranges, pct=0.30)
    assert s["n_minutes"] == 3
    assert s["n_zero_minutes"] == 1
    assert math.isclose(s["zero_fraction"], 1 / 3, rel_tol=1e-12)
    assert math.isclose(s["mean_all"], 5 / 3, rel_tol=1e-12)
    assert math.isclose(s["mean_nonzero"], 2.5, rel_tol=1e-12)
    assert math.isclose(s["median"], 2.0, rel_tol=1e-12)
    assert math.isclose(s["suggested_from_mean_all"], 0.5, rel_tol=1e-12)
    assert math.isclose(s["suggested_from_mean_nonzero"], 0.75, rel_tol=1e-12)


def test_median_even_length():
    # sorted [0,2,3,3] → median = (2+3)/2 = 2.5
    s = calibrate([2.0, 3.0, 0.0, 3.0], pct=0.30)
    assert math.isclose(s["median"], 2.5, rel_tol=1e-12)


def test_all_zero_ranges_nonzero_mean_is_zero():
    s = calibrate([0.0, 0.0, 0.0], pct=0.30)
    assert s["mean_all"] == 0.0
    assert s["mean_nonzero"] == 0.0
    assert s["suggested_from_mean_all"] == 0.0


def test_empty_raises():
    with pytest.raises(ValueError):
        calibrate([], pct=0.30)


# ---------------------------------------------------------------------------
# _round_to_tick
# ---------------------------------------------------------------------------

def test_round_to_tick_exact_multiple():
    val, n = _round_to_tick(0.5, 0.1)
    assert n == 5
    assert math.isclose(val, 0.5, rel_tol=1e-9)


def test_round_to_tick_floors_to_at_least_one_tick():
    # 0.03 / 0.1 = 0.3 → round → 0, but we clamp to a minimum of 1 tick.
    val, n = _round_to_tick(0.03, 0.1)
    assert n == 1
    assert math.isclose(val, 0.1, rel_tol=1e-9)
