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


# ---------------------------------------------------------------------------
# Generalized acceptance_bars (2026-07-07) — real-data testing found the
# original 1-bar acceptance window resolves almost as fast as the rule it
# replaced. These hand-derive the multi-bar case by hand, tracing the
# pending_break/new_count arithmetic on paper before writing the assertion,
# not by running the code first.
# ---------------------------------------------------------------------------

def test_acceptance_bars_3_exits_on_fourth_consecutive_break():
    # level=100, long side. acceptance_bars=3 requires 3 bars AFTER the
    # break bar (4 consecutive closes below 100 total) before exit fires.
    # Hand trace: bar1 close=95 -> new_count=1 (1>3? no) -> (False, True)
    #             bar2 close=94 -> new_count=2 (2>3? no) -> (False, 2)
    #             bar3 close=93 -> new_count=3 (3>3? no) -> (False, 3)
    #             bar4 close=92 -> new_count=4 (4>3? YES) -> (True, False)
    pending = False
    for close, expected in [(95, (False, True)), (94, (False, 2)),
                             (93, (False, 3)), (92, (True, False))]:
        exit_now, pending = check_break_and_acceptance(
            "long", last_swing_low=100.0, last_swing_high=None,
            pending_break=pending, bar=_bar(close + 2, close - 2, close),
            acceptance_bars=3)
        assert (exit_now, pending) == expected


def test_acceptance_bars_3_reclaim_resets_then_fresh_break_restarts_at_one():
    # Same level=100, acceptance_bars=3. Break, then reclaim on bar 2
    # (before the 4-bar window completes) -> must reset to (False, False),
    # not carry any partial count forward. A fresh break afterward must
    # start counting from 1 again, not from wherever it left off.
    exit1, pending1 = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=False, bar=_bar(97, 93, 95), acceptance_bars=3)
    assert (exit1, pending1) == (False, True)          # bar1: break, count=1

    exit2, pending2 = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=pending1, bar=_bar(103, 99, 101), acceptance_bars=3)
    assert (exit2, pending2) == (False, False)          # bar2: reclaim, reset

    exit3, pending3 = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=pending2, bar=_bar(98, 94, 96), acceptance_bars=3)
    assert (exit3, pending3) == (False, True)           # bar3: FRESH break,
    # count restarts at 1 (True), not 2 — proves no leftover state from
    # the failed break earlier survived the reclaim.


def test_acceptance_bars_0_exits_immediately_on_break_bar():
    # acceptance_bars=0: new_count(1) > acceptance_bars(0) is true on the
    # very first broke bar, so this should fire immediately — no
    # confirmation bar at all, equivalent to the pre-swing-fix bar-mode
    # rule's speed but still gated on real swing structure.
    exit_now, pending = check_break_and_acceptance(
        "long", last_swing_low=100.0, last_swing_high=None,
        pending_break=False, bar=_bar(97, 93, 95), acceptance_bars=0)
    assert (exit_now, pending) == (True, False)
