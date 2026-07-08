"""Tests for acceptance_variant.py — verifies AcceptanceWindowStrategy
correctly threads a configurable acceptance_bars through to
check_break_and_acceptance.

Expected values reuse the exact hand-derived trace already proven in
test_swings.py's test_acceptance_bars_3_exits_on_fourth_consecutive_break
(same level, same closes, same expected sequence) — invoked through the
strategy wrapper this time, to prove the WIRING is correct (not the
arithmetic, which is already covered separately).
"""
from __future__ import annotations

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.acceptance_variant import AcceptanceWindowStrategy
from src.rangebars.builder import RangeBar


def _bar(h, l, c):
    return RangeBar(open=c, high=h, low=l, close=c, start_ts=0, end_ts=0)


def test_default_acceptance_bars_is_one():
    replay = Replay(range_size=1.0)
    engine = FillEngine()
    s = AcceptanceWindowStrategy(replay, engine, exit_mode="swing")
    assert s.acceptance_bars == 1


def test_custom_acceptance_bars_is_stored():
    replay = Replay(range_size=1.0)
    engine = FillEngine()
    s = AcceptanceWindowStrategy(replay, engine, exit_mode="swing",
                                  acceptance_bars=3)
    assert s.acceptance_bars == 3


def test_check_reversal_swing_mode_threads_acceptance_bars_through():
    """level=100, long side, closes 95/94/93/92 -> no exit until the 4th
    consecutive broke bar (break + 3 acceptance bars), same trace as the
    pure-function test in test_swings.py."""
    replay = Replay(range_size=1.0)
    engine = FillEngine()
    s = AcceptanceWindowStrategy(replay, engine, exit_mode="swing",
                                  acceptance_bars=3)
    s._side = "long"
    s.swings.last_swing_low = 100.0

    results = [s._check_reversal_swing_mode(_bar(c + 2, c - 2, c))
               for c in [95, 94, 93, 92]]
    assert results == [False, False, False, True]


def test_same_scenario_with_acceptance_bars_1_exits_on_second_bar():
    """Same break sequence, acceptance_bars=1 (the default) — exits on
    the SECOND bar (break + 1 confirm), proving the parameter actually
    changes behavior, not just gets stored inertly."""
    replay = Replay(range_size=1.0)
    engine = FillEngine()
    s = AcceptanceWindowStrategy(replay, engine, exit_mode="swing",
                                  acceptance_bars=1)
    s._side = "long"
    s.swings.last_swing_low = 100.0

    results = [s._check_reversal_swing_mode(_bar(c + 2, c - 2, c))
               for c in [95, 94]]
    assert results == [False, True]
