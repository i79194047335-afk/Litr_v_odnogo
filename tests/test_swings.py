"""Swing structure tests. Expected values hand-computed independently
from Beggs' definition, not read back from the implementation."""
import pytest

from src.rangebars.builder import RangeBar
from src.backtest.swings import SwingTracker, check_break_and_acceptance


def _bar(h, l, c):
    return RangeBar(open=c, high=h, low=l, close=c, start_ts=0, end_ts=0)


def test_swing_low_confirms_after_five_bars():
    # lows = [100, 98, 95, 97, 99] -> 95 (index 2) is lower than all 4
    # neighbors -> confirms as swing low once the 5th bar arrives.
    t = SwingTracker(confirm_bars=2)
    lows = [100, 98, 95, 97, 99]
    for i, lo in enumerate(lows):
        t.update(_bar(h=lo + 5, l=lo, c=lo + 2))
        if i < 4:
            assert t.last_swing_low is None, f"confirmed too early at bar {i}"
    assert t.last_swing_low == 95


def test_swing_high_confirms_after_five_bars():
    # highs = [100, 105, 110, 103, 101] -> 110 (index 2) is higher than all
    # 4 neighbors -> confirms once the 5th bar arrives.
    t = SwingTracker(confirm_bars=2)
    highs = [100, 105, 110, 103, 101]
    for hi in highs:
        t.update(_bar(h=hi, l=hi - 5, c=hi - 2))
    assert t.last_swing_high == 110


def test_non_extremal_candidate_does_not_confirm():
    # lows = [100, 98, 95, 94, 99] -> 95 is NOT < 94, so index 2 fails.
    t = SwingTracker(confirm_bars=2)
    lows = [100, 98, 95, 94, 99]
    for lo in lows:
        t.update(_bar(h=lo + 5, l=lo, c=lo + 2))
    assert t.last_swing_low is None


def test_swing_low_updates_to_latest_confirmed_not_most_extreme():
    # A second, LESS extreme but more recent swing low must still replace
    # the older one — "last confirmed", not "most extreme ever".
    t = SwingTracker(confirm_bars=2)
    for lo in [100, 98, 95, 97, 99]:      # confirms 95 at bar index 2
        t.update(_bar(h=lo + 5, l=lo, c=lo + 2))
    assert t.last_swing_low == 95
    for lo in [102, 99, 96, 98, 100]:     # confirms 96 (less extreme than 95)
        t.update(_bar(h=lo + 5, l=lo, c=lo + 2))
    assert t.last_swing_low == 96


def test_invalid_confirm_bars_raises():
    with pytest.raises(ValueError):
        SwingTracker(confirm_bars=0)


def test_no_break_when_price_stays_above_swing_low():
    exit_now, pending = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=False, bar=_bar(106, 104, 105))
    assert (exit_now, pending) == (False, False)


def test_objective_break_sets_pending_not_exit():
    exit_now, pending = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=False, bar=_bar(96, 94, 95))
    assert (exit_now, pending) == (False, True)


def test_acceptance_confirms_exit():
    exit_now, pending = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=True, bar=_bar(95, 93, 94))
    assert (exit_now, pending) == (True, False)


def test_reclaim_clears_pending_without_exit():
    exit_now, pending = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=True, bar=_bar(103, 101, 102))
    assert (exit_now, pending) == (False, False)


def test_short_side_mirrors_long():
    exit_now, pending = check_break_and_acceptance(
        "short", last_swing_low=None, last_swing_high=100.0,
        pending_break=False, bar=_bar(106, 104, 105))
    assert (exit_now, pending) == (False, True)
    exit_now2, pending2 = check_break_and_acceptance(
        "short", last_swing_low=None, last_swing_high=100.0,
        pending_break=True, bar=_bar(108, 106, 107))
    assert (exit_now2, pending2) == (True, False)


def test_no_swing_level_yet_never_triggers():
    exit_now, pending = check_break_and_acceptance(
        "long", last_swing_low=None, last_swing_high=None,
        pending_break=False, bar=_bar(50, 40, 45))
    assert (exit_now, pending) == (False, False)
