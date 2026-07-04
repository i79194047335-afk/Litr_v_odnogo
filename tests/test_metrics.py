"""Metrics tests. Expected values hand-computed independently.

Two units: R-multiple (PnL / distance-to-line-0-stop) and bps (PnL / entry
price, 1 bps = 0.01%). For these fixtures entry_price=100 and size=1, so
bps = R * 100 exactly — differ only by that scale, keeping the hand math
trivial while exercising both code paths."""
import math

from src.backtest.strategy import Trade
from src.backtest.costs import CostBreakdown
from src.backtest.metrics import compute_metrics


def _trade(r, exit_ts, tag="part1"):
    return Trade(side="long", tag=tag, size=1.0, entry_price=100.0,
                entry_ts=0, exit_price=100.0 + r, exit_ts=exit_ts,
                exit_reason="take", r_multiple=r, risk=1.0)


def _breakdown(trade, net_r):
    return CostBreakdown(trade=trade, gross_pnl=trade.r_multiple,
                         fees=0.0, funding=0.0, net_pnl=net_r,
                         net_r_multiple=net_r)


GROSS = [1.0, -1.0, 2.0, -0.5, 0.0]
NET = [0.9, -1.1, 1.8, -0.6, -0.1]


def _make(gross=GROSS, net=NET):
    trades = [_trade(r, exit_ts=i) for i, r in enumerate(gross)]
    breakdowns = [_breakdown(t, n) for t, n in zip(trades, net)]
    return trades, breakdowns


def test_r_and_bps_expectancy_hand_computed():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 3, 2, 0, 1)
    assert math.isclose(m.r_gross.expectancy, 0.3, rel_tol=1e-12)
    assert math.isclose(m.r_net.expectancy, 0.18, rel_tol=1e-12)
    assert math.isclose(m.bps_gross.expectancy, 30.0, rel_tol=1e-12)
    assert math.isclose(m.bps_net.expectancy, 18.0, rel_tol=1e-12)
    assert math.isclose(m.win_rate_gross, 0.4, rel_tol=1e-12)
    assert math.isclose(m.profit_factor_gross, 2.0, rel_tol=1e-12)
    assert math.isclose(m.profit_factor_net, 1.5, rel_tol=1e-12)


def test_dispersion_hand_computed():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 1, 0, 0, 1)
    assert math.isclose(m.r_gross.stdev, 1.2041594578792296, rel_tol=1e-9)
    assert math.isclose(m.r_gross.se, 0.5385164807134504, rel_tol=1e-9)
    assert math.isclose(m.r_gross.t_stat, 0.5570860145311556, rel_tol=1e-9)
    assert math.isclose(m.bps_gross.stdev, 120.41594578792295, rel_tol=1e-9)
    assert math.isclose(m.bps_gross.se, 53.85164807134504, rel_tol=1e-9)
    assert math.isclose(m.bps_gross.t_stat, m.r_gross.t_stat, rel_tol=1e-12)


def test_dispersion_none_for_single_trade():
    trades, breakdowns = _make(gross=[1.0], net=[0.9])
    m = compute_metrics(trades, breakdowns, 1, 1, 0, 0)
    assert m.r_gross.stdev is None
    assert m.r_gross.se is None
    assert m.r_gross.t_stat is None
    assert m.bps_gross.stdev is None


def test_max_drawdown_r_and_bps_hand_traced():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 1, 0, 0, 1)
    assert math.isclose(m.max_drawdown_r, 1.1, rel_tol=1e-12)
    assert math.isclose(m.max_drawdown_bps, 110.0, rel_tol=1e-12)


def test_drawdown_uses_exit_ts_order_not_list_order():
    trades, breakdowns = _make()
    st = [trades[2], trades[0], trades[4], trades[1], trades[3]]
    sb = [breakdowns[2], breakdowns[0], breakdowns[4], breakdowns[1], breakdowns[3]]
    m = compute_metrics(st, sb, 1, 0, 0, 1)
    assert math.isclose(m.max_drawdown_r, 1.1, rel_tol=1e-12)


def test_scale_in_rate_vs_part2_fill_rate_distinction():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 10, 5, 3, 2)
    assert math.isclose(m.scale_in_rate, 0.2, rel_tol=1e-12)
    assert math.isclose(m.part2_fill_rate, 0.5, rel_tol=1e-12)


def test_rates_none_when_no_sessions():
    m = compute_metrics([], [], 0, 0, 0, 0)
    assert m.scale_in_rate is None
    assert m.part2_fill_rate is None


def test_empty_trades_all_none_not_crash():
    m = compute_metrics([], [], 0, 0, 0, 0)
    assert m.n_trades == 0
    assert m.r_gross.expectancy is None
    assert m.bps_gross.expectancy is None
    assert m.win_rate_gross is None
    assert m.profit_factor_gross is None
    assert m.max_drawdown_r is None
    assert m.max_drawdown_bps is None


def test_profit_factor_infinite_when_no_losses():
    trades, breakdowns = _make(gross=[1.0, 2.0], net=[0.9, 1.8])
    m = compute_metrics(trades, breakdowns, 1, 1, 0, 0)
    assert m.profit_factor_gross == math.inf
    assert m.profit_factor_net == math.inf


def test_profit_factor_none_when_all_breakeven():
    trades, breakdowns = _make(gross=[0.0, 0.0], net=[0.0, 0.0])
    m = compute_metrics(trades, breakdowns, 1, 1, 0, 0)
    assert m.profit_factor_gross is None
    assert m.win_rate_gross == 0.0
    assert math.isclose(m.r_gross.expectancy, 0.0, abs_tol=1e-12)
    assert math.isclose(m.bps_gross.expectancy, 0.0, abs_tol=1e-12)


def test_n_trades_matches_list_length():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 3, 2, 0, 1)
    assert m.n_trades == 5


def _trade_full(tag, reason, gross_r, exit_ts=0):
    return Trade(side="long", tag=tag, size=1.0, entry_price=100.0,
                entry_ts=0, exit_price=100.0 + gross_r, exit_ts=exit_ts,
                exit_reason=reason, r_multiple=gross_r, risk=1.0)


BREAKDOWN_ROWS = [
    ("part1", "take",      1.0,  0.9),
    ("part1", "stop",     -1.0, -1.1),
    ("part2", "take",      2.0,  1.8),
    ("part1", "reversal", -0.5, -0.6),
    ("part2", "reversal",  0.0, -0.1),
]


def _make_breakdown_stack():
    trades = [_trade_full(tag, reason, gr, exit_ts=i)
              for i, (tag, reason, gr, _) in enumerate(BREAKDOWN_ROWS)]
    bds = [_breakdown(t, nr) for t, (_, _, _, nr) in zip(trades, BREAKDOWN_ROWS)]
    return trades, bds


def test_by_exit_reason_hand_computed():
    trades, bds = _make_breakdown_stack()
    m = compute_metrics(trades, bds, 5, 3, 2, 0)
    by = {g.label: g for g in m.by_exit_reason}
    assert set(by) == {"take", "stop", "reversal"}
    assert by["take"].n == 2
    assert math.isclose(by["take"].mean_bps_gross, 150.0, rel_tol=1e-12)
    assert math.isclose(by["take"].mean_bps_net, 135.0, rel_tol=1e-12)
    assert by["take"].win_rate_net == 1.0
    assert math.isclose(by["stop"].mean_bps_net, -110.0, rel_tol=1e-12)
    assert math.isclose(by["reversal"].mean_bps_net, -35.0, rel_tol=1e-12)


def test_by_tag_hand_computed():
    trades, bds = _make_breakdown_stack()
    m = compute_metrics(trades, bds, 5, 3, 2, 0)
    by = {g.label: g for g in m.by_tag}
    assert by["part1"].n == 3
    assert math.isclose(by["part1"].mean_bps_net, -80.0 / 3, rel_tol=1e-9)
    assert by["part2"].n == 2
    assert math.isclose(by["part2"].mean_bps_net, 85.0, rel_tol=1e-12)
    assert math.isclose(by["part2"].win_rate_net, 0.5, rel_tol=1e-12)


def test_breakdowns_empty_when_no_trades():
    m = compute_metrics([], [], 0, 0, 0, 0)
    assert m.by_exit_reason == []
    assert m.by_tag == []
