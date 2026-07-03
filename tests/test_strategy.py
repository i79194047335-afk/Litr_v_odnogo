"""WF strategy tests.

The three integration scenarios are FULLY hand-scripted: every bar close,
line level, fill and R below was derived on paper from the builder / engine /
strategy rules before running the code. Test params are deliberately tiny so
this is checkable by hand:

    range_size=1.0, ema_fast=2 (k=2/3), ema_slow=3 (k=1/2),
    keltner period=2, mult_inner=1, mult_outer=2, part_size=1.

Shared warm-up script (hand-derived):
    (100.0,      0)  m0; opens first bar
    (101.0, 10_000)  bar1 closes o100 h101 l100 c101 (up)
    (102.0, 20_000)  bar2 closes o101 h102 l101 c102; Keltner ready
    (102.2, 60_000)  m1 tick -> closes m0 candle (close 102);
                     ema_fast seed 102, ema_slow seed 102 (neither ready)
    (103.2, 70_000)  bar3 closes at 103 (o102 h103 l102 c103)
    (103.4,120_000)  m2 tick -> closes m1 candle (close 103.2);
                     fast=102.8 (ready), slow=102.6 (not ready yet)
    (104.2,130_000)  bar4 closes at 104; bias still None (slow not ready)
                     -> no entry orders yet (warm-up gate)
    (104.3,180_000)  m3 tick -> closes m2 candle (close 104.2);
                     fast=311.2/3~103.7333, slow=103.4, both ready;
                     BULL available (fast>slow and 104.2>fast)
    (105.1,190_000)  bar5 closes at 105; Keltner closes[104,105] c=104.5 r=1
                     -> refresh: lines 0=102.5 q=103.5 h=104.5 tq=105.5;
                     last_price 105.1 -> BOTH entries placed:
                     part1 buy limit @104.5, part2 buy limit @103.5
    (104.4,200_000)  104.4 < 104.5 -> part1 FILLS at 104.5;
                     stop: sell stop @102.5 (line 0) size 1;
                     take: sell limit @105.5 (line 3/4) size 1
"""
import math

import pytest

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy, compute_bias, zone_lines


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------

def test_bias_bull_requires_both_conditions():
    assert compute_bias(101.0, 100.0, 102.0) == "bull"
    assert compute_bias(101.0, 100.0, 100.5) is None    # close below fast
    assert compute_bias(100.0, 101.0, 102.0) is None    # fast below slow


def test_bias_bear_mirrors():
    assert compute_bias(99.0, 100.0, 98.0) == "bear"
    assert compute_bias(99.0, 100.0, 99.5) is None      # close above fast


def test_bias_equal_emas_is_neutral():
    assert compute_bias(100.0, 100.0, 101.0) is None


def test_bias_none_inputs():
    assert compute_bias(None, 100.0, 100.0) is None
    assert compute_bias(100.0, None, 100.0) is None
    assert compute_bias(100.0, 100.0, None) is None


def test_zone_lines_bull_hand_computed():
    # c=100, r=2, mi=4, mo=8: inner=8, outer=16
    ln = zone_lines(100.0, 2.0, 4.0, 8.0, "bull")
    assert ln == {"0": 84.0, "q": 92.0, "h": 100.0, "tq": 108.0, "1": 116.0}


def test_zone_lines_bear_mirrors():
    ln = zone_lines(100.0, 2.0, 4.0, 8.0, "bear")
    assert ln == {"0": 116.0, "q": 108.0, "h": 100.0, "tq": 92.0, "1": 84.0}


def test_zone_lines_are_evenly_spaced_when_inner_is_half_outer():
    ln = zone_lines(100.0, 1.0, 4.0, 8.0, "bull")
    seq = [ln["0"], ln["q"], ln["h"], ln["tq"], ln["1"]]
    gaps = [b - a for a, b in zip(seq, seq[1:])]
    assert all(math.isclose(g, gaps[0]) for g in gaps)


# ---------------------------------------------------------------------------
# integration scenarios (hand-scripted; see module docstring for warm-up)
# ---------------------------------------------------------------------------

WARMUP = [
    (100.0, 0), (101.0, 10_000), (102.0, 20_000),
    (102.2, 60_000), (103.2, 70_000),
    (103.4, 120_000), (104.2, 130_000),
    (104.3, 180_000), (105.1, 190_000),
]


def make_stack():
    r = Replay(range_size=1.0, ema_fast=2, ema_slow=3)
    e = FillEngine()
    s = WFStrategy(r, e, keltner_period=2, mult_inner=1.0, mult_outer=2.0,
                   part_size=1.0)
    return r, e, s


def test_no_orders_before_warmup_gate():
    # Stop right after bar4 (tick 104.2@130_000): keltner is ready but
    # ema_slow is not -> bias gated to None -> no entry orders may exist.
    r, e, s = make_stack()
    r.run(WARMUP[:7], s)
    assert e.open_orders == []
    assert s.trades == []


def test_entries_placed_after_bar5():
    r, e, s = make_stack()
    r.run(WARMUP, s)
    orders = {o.tag: o for o in e.open_orders}
    assert set(orders) == {"part1", "part2"}
    assert orders["part1"].side == "buy" and orders["part1"].price == 104.5
    assert orders["part2"].side == "buy" and orders["part2"].price == 103.5


def test_part1_fill_places_stop_and_take():
    r, e, s = make_stack()
    r.run(WARMUP + [(104.4, 200_000)], s)
    tags = {o.tag: o for o in e.open_orders}
    # part1 gone (filled); part2, stop, take resting
    assert set(tags) == {"part2", "stop", "take"}
    assert tags["stop"].side == "sell" and tags["stop"].price == 102.5
    assert tags["stop"].size == 1.0
    assert tags["take"].side == "sell" and tags["take"].price == 105.5
    assert s.trades == []                      # nothing closed yet


def test_scenario_take_profit():
    # (105.6, 210_000): on_tick -> 105.6 > 105.5 fills the take at 105.5.
    # part1: entry 104.5, exit 105.5, risk 104.5-102.5=2 -> r=+0.5, "take".
    # Position empty -> part2 entry and stop cancelled.
    # Same tick then closes bar6 (o105 h105.4 l104.4 c105.4, up);
    # refresh: Keltner closes[105,105.4] c=105.2 r=1 -> h=105.2 q=104.2;
    # last price 105.6 -> both resting -> re-placed.
    r, e, s = make_stack()
    r.run(WARMUP + [(104.4, 200_000), (105.6, 210_000)], s)

    assert len(s.trades) == 1
    t = s.trades[0]
    assert t.side == "long" and t.tag == "part1"
    assert t.entry_price == 104.5 and t.entry_ts == 200_000
    assert t.exit_price == 105.5 and t.exit_ts == 210_000
    assert t.exit_reason == "take"
    assert math.isclose(t.r_multiple, 0.5, rel_tol=1e-9)

    orders = {o.tag: o for o in e.open_orders}
    assert set(orders) == {"part1", "part2"}
    assert math.isclose(orders["part1"].price, 105.2, rel_tol=1e-9)
    assert math.isclose(orders["part2"].price, 104.2, rel_tol=1e-9)


def test_scenario_gap_through_stop_flattens_everything():
    # (102.3, 210_000): one gap tick.
    #   part2 buy limit 103.5: 102.3 < 103.5 -> fills at 103.5;
    #   stop  sell stop 102.5: 102.3 <= 102.5 -> fills at 102.3 (tick price);
    #   stop fill flattens BOTH parts at 102.3:
    #     part1: (102.3-104.5)/2   = -1.1
    #     part2: risk 103.5-102.5=1 -> (102.3-103.5)/1 = -1.2
    # Bar bookkeeping: the tick closes two DOWN bars (104.1 then 103.1);
    # position already flat, and the refreshed lines (h=104.55/103.6) are
    # all ABOVE the last price 102.3 -> resting guard skips them -> no orders.
    r, e, s = make_stack()
    r.run(WARMUP + [(104.4, 200_000), (102.3, 210_000)], s)

    assert len(s.trades) == 2
    by_tag = {t.tag: t for t in s.trades}
    p1, p2 = by_tag["part1"], by_tag["part2"]
    assert p1.exit_reason == "stop" and p2.exit_reason == "stop"
    assert p1.exit_price == 102.3 and p2.exit_price == 102.3
    assert math.isclose(p1.r_multiple, -1.1, rel_tol=1e-9)
    assert math.isclose(p2.r_multiple, -1.2, rel_tol=1e-9)
    assert p2.entry_price == 103.5             # filled on the same gap tick

    assert e.open_orders == []                 # nothing resting afterwards


def test_scenario_reversal_bar_exits_remaining():
    # (104.05, 210_000): fills nothing (above part2 103.5, above stop 102.5).
    # Closes bar6 DOWN: o105 h105.1 l104.1 c104.1 (down, since 104.05 dragged
    # range to 1.05 and close<open). Long position + down bar -> reversal:
    # part1 exits at bar.close=104.1: r = (104.1-104.5)/2 = -0.2.
    # Cleanup cancels part2 entry, stop, take. Refresh with last price
    # 104.05: h=104.55 NOT resting (above price), q=103.55 resting ->
    # ONLY part2 re-placed.
    r, e, s = make_stack()
    r.run(WARMUP + [(104.4, 200_000), (104.05, 210_000)], s)

    assert len(s.trades) == 1
    t = s.trades[0]
    assert t.exit_reason == "reversal"
    assert math.isclose(t.exit_price, 104.1, rel_tol=1e-9)
    assert t.exit_ts == 210_000
    assert math.isclose(t.r_multiple, -0.2, rel_tol=1e-9)

    orders = {o.tag: o for o in e.open_orders}
    assert set(orders) == {"part2"}
    assert math.isclose(orders["part2"].price, 103.55, rel_tol=1e-9)
