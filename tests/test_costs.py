"""Cost model tests. Expected values hand-computed independently (see the
derivation script referenced in commit notes: plain arithmetic, no code
reused from the implementation)."""
import math

import pytest

from src.backtest.strategy import Trade
from src.backtest.costs import (
    hourly_boundaries_crossed, funding_cost, fee_cost, apply_costs,
    apply_costs_to_trades, HOUR_MS,
)


def _trade(side="long", entry=100.0, exit_=105.0, size=2.0,
          reason="take", entry_ts=0, exit_ts=1000, risk=2.0):
    pnl = (exit_ - entry) if side == "long" else (entry - exit_)
    r = pnl / risk if risk > 0 else 0.0
    return Trade(side=side, tag="part1", size=size, entry_price=entry,
                entry_ts=entry_ts, exit_price=exit_, exit_ts=exit_ts,
                exit_reason=reason, r_multiple=r, risk=risk)


# ---------------------------------------------------------------------------
# hourly_boundaries_crossed
# ---------------------------------------------------------------------------

def test_no_boundary_within_same_hour():
    assert hourly_boundaries_crossed(0, HOUR_MS - 1) == 0


def test_one_boundary_crossed():
    assert hourly_boundaries_crossed(0, HOUR_MS) == 1


def test_two_boundaries_crossed():
    assert hourly_boundaries_crossed(HOUR_MS + 500, 3 * HOUR_MS + 10) == 2


def test_exit_before_entry_raises():
    with pytest.raises(ValueError):
        hourly_boundaries_crossed(1000, 500)


# ---------------------------------------------------------------------------
# funding_cost
# ---------------------------------------------------------------------------

def test_funding_zero_rate_is_always_zero():
    assert funding_cost("long", 2.0, 100.0, 0, 10 * HOUR_MS, hourly_rate=0.0) == 0.0


def test_funding_zero_when_no_boundary_crossed():
    assert funding_cost("long", 2.0, 100.0, 0, HOUR_MS - 1, hourly_rate=0.001) == 0.0


def test_funding_long_pays_when_rate_positive():
    f = funding_cost("long", 2.0, 100.0, 0, HOUR_MS, hourly_rate=0.0001)
    assert math.isclose(f, 0.02, rel_tol=1e-12)


def test_funding_short_receives_when_rate_positive():
    f = funding_cost("short", 2.0, 100.0, 0, HOUR_MS, hourly_rate=0.0001)
    assert math.isclose(f, -0.02, rel_tol=1e-12)


def test_funding_scales_with_boundaries_crossed():
    f1 = funding_cost("long", 1.0, 100.0, 0, HOUR_MS, hourly_rate=0.0001)
    f2 = funding_cost("long", 1.0, 100.0, 0, 2 * HOUR_MS, hourly_rate=0.0001)
    assert math.isclose(f2, 2 * f1, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# fee_cost
# ---------------------------------------------------------------------------

def test_fee_take_exit_is_maker_both_sides():
    t = _trade(reason="take", entry=100.0, exit_=105.0, size=2.0)
    f = fee_cost(t, maker_bps=2.0, taker_bps=5.0)
    # entry_fee = 100*2*0.0002=0.04 ; exit_fee(maker) = 105*2*0.0002=0.042
    assert math.isclose(f, 0.082, rel_tol=1e-9)


def test_fee_stop_exit_is_taker_on_exit_only():
    t = _trade(reason="stop", entry=100.0, exit_=97.0, size=2.0)
    f = fee_cost(t, maker_bps=2.0, taker_bps=5.0)
    # entry_fee(maker) = 0.04 ; exit_fee(taker) = 97*2*0.0005=0.097
    assert math.isclose(f, 0.137, rel_tol=1e-9)


def test_fee_reversal_exit_is_taker():
    t = _trade(reason="reversal", entry=100.0, exit_=99.0, size=1.0)
    f = fee_cost(t, maker_bps=2.0, taker_bps=5.0)
    # entry_fee(maker)=100*1*0.0002=0.02 ; exit_fee(taker)=99*1*0.0005=0.0495
    assert math.isclose(f, 0.0695, rel_tol=1e-9)


def test_zero_bps_is_a_noop():
    t = _trade(reason="stop")
    assert fee_cost(t, maker_bps=0.0, taker_bps=0.0) == 0.0


# ---------------------------------------------------------------------------
# apply_costs (full breakdown)
# ---------------------------------------------------------------------------

def test_apply_costs_long_take_hand_computed():
    t = _trade(side="long", entry=100.0, exit_=105.0, size=2.0,
              reason="take", risk=2.0)
    cb = apply_costs(t, maker_bps=2.0, taker_bps=5.0, hourly_rate=0.0)
    assert math.isclose(cb.gross_pnl, 10.0, rel_tol=1e-12)
    assert math.isclose(cb.fees, 0.082, rel_tol=1e-9)
    assert cb.funding == 0.0
    assert math.isclose(cb.net_pnl, 9.918, rel_tol=1e-9)
    assert math.isclose(cb.net_r_multiple, 2.4795, rel_tol=1e-9)


def test_apply_costs_long_stop_hand_computed():
    t = _trade(side="long", entry=100.0, exit_=97.0, size=2.0,
              reason="stop", risk=3.0)
    cb = apply_costs(t, maker_bps=2.0, taker_bps=5.0, hourly_rate=0.0)
    assert math.isclose(cb.gross_pnl, -6.0, rel_tol=1e-12)
    assert math.isclose(cb.fees, 0.137, rel_tol=1e-9)
    assert math.isclose(cb.net_pnl, -6.137, rel_tol=1e-9)
    assert math.isclose(cb.net_r_multiple, -6.137 / 6.0, rel_tol=1e-9)


def test_apply_costs_short_take_hand_computed():
    t = _trade(side="short", entry=100.0, exit_=95.0, size=1.0,
              reason="take", risk=1.0)
    cb = apply_costs(t, maker_bps=2.0, taker_bps=5.0, hourly_rate=0.0)
    assert math.isclose(cb.gross_pnl, 5.0, rel_tol=1e-12)
    assert math.isclose(cb.fees, 0.039, rel_tol=1e-9)
    assert math.isclose(cb.net_pnl, 4.961, rel_tol=1e-9)


def test_apply_costs_zero_risk_gives_zero_net_r():
    t = _trade(risk=0.0)
    cb = apply_costs(t)
    assert cb.net_r_multiple == 0.0


def test_apply_costs_zero_everything_matches_gross():
    # bps=0, rate=0 -> net must equal gross exactly (no-op costs).
    t = _trade(side="long", entry=100.0, exit_=105.0, size=2.0, risk=2.0)
    cb = apply_costs(t)
    assert cb.net_pnl == cb.gross_pnl
    assert math.isclose(cb.net_r_multiple, t.r_multiple, rel_tol=1e-12)


def test_apply_costs_to_trades_batch():
    trades = [_trade(exit_=105.0), _trade(side="short", entry=100.0, exit_=95.0)]
    out = apply_costs_to_trades(trades, maker_bps=1.0)
    assert len(out) == 2
    assert all(isinstance(cb.net_pnl, float) for cb in out)
