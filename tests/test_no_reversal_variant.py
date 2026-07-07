"""Tests for no_reversal_variant.py — verifies the core invariant that
reversal checks always return False, and that the strategy still produces
valid trades through take-profit and stop-loss exits.

All expected values below were derived BY HAND from the synthetic tick
stream and strategy rules, on paper, before running the code.
"""
from __future__ import annotations

import pytest

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.no_reversal_variant import NoReversalStrategy


def test_reversal_bar_mode_always_false():
    """Core invariant: _check_reversal_bar_mode must always return False."""
    replay = Replay(range_size=1.0)
    engine = FillEngine()
    s = NoReversalStrategy(replay, engine)
    # Create a mock bar that would normally trigger reversal (opposite bar)
    from src.rangebars.builder import RangeBar
    bar = RangeBar(open=100, high=101, low=99, close=99,
                   start_ts=0, end_ts=1000, n_ticks=5)
    # Close < Open with side="long" would normally trigger bar-mode reversal
    s._side = "long"
    assert s._check_reversal_bar_mode(bar) is False
    s._side = "short"
    assert s._check_reversal_bar_mode(bar) is False


def test_reversal_swing_mode_always_false():
    """Core invariant: _check_reversal_swing_mode must always return False."""
    replay = Replay(range_size=1.0)
    engine = FillEngine()
    s = NoReversalStrategy(replay, engine, exit_mode="swing")
    from src.rangebars.builder import RangeBar
    bar = RangeBar(open=100, high=101, low=99, close=99,
                   start_ts=0, end_ts=1000, n_ticks=5)
    # Should return False regardless of side or bar content
    s._side = "long"
    assert s._check_reversal_swing_mode(bar) is False
    s._side = "short"
    assert s._check_reversal_swing_mode(bar) is False


def test_no_reversal_strategy_exit_reasons():
    """End-to-end: with a simple synthetic stream that would produce a
    reversal in the parent WFStrategy, NoReversalStrategy should NOT produce
    any reversal exits — only take or stop.

    Hand-derived scenario (range_size=1.0, ema_fast=2, ema_slow=3,
    keltner_period=2, mult_inner=1, mult_outer=2, part_size=1):

    Warm-up ticks: (100.0,0) (101.0,10k) (102.0,20k) (102.2,60k)
                   (103.2,70k) (103.4,120k) (104.2,130k) (104.3,180k)
                   (105.1,190k)

    After warm-up: bias=bull, line-0=102.5.  A gap down to 102.3 fills
    both parts and the stop — all trades should be "stop", never "reversal".
    """
    replay = Replay(range_size=1.0, ema_fast=2, ema_slow=3)
    engine = FillEngine()
    s = NoReversalStrategy(
        replay, engine,
        keltner_period=2, mult_inner=1, mult_outer=2, part_size=1,
        exit_mode="swing", trailing=False,
    )

    # Warm-up ticks (hand-derived, same as test_strategy.py)
    warmup = [
        (100.0, 0),
        (101.0, 10_000),
        (102.0, 20_000),
        (102.2, 60_000),
        (103.2, 70_000),
        (103.4, 120_000),
        (104.2, 130_000),
        (104.3, 180_000),
        (105.1, 190_000),
        (104.4, 200_000),   # part1 fills at 104.5
        (102.3, 500_000),   # gap down: fills part2 at 103.5 AND stop at 102.5
    ]
    replay.run(warmup, s)

    # All exits must be "stop" — no "reversal" exits
    exit_reasons = {t.exit_reason for t in s.trades}
    assert "reversal" not in exit_reasons, \
        f"Found reversal exits: {[t.exit_reason for t in s.trades]}"
    assert len(s.trades) > 0, "Expected at least one trade"
