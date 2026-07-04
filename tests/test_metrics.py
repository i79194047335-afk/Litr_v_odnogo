"""Metrics tests. Expected values hand-computed independently (see the
derivation script referenced in commit notes)."""
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


def test_expectancy_win_rate_profit_factor_hand_computed():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, n_sessions=3,
                        n_sessions_part1_only=2, n_sessions_part2_only=0,
                        n_sessions_both=1)

    assert math.isclose(m.expectancy_r_gross, 0.3, rel_tol=1e-12)
    assert math.isclose(m.win_rate_gross, 0.4, rel_tol=1e-12)
    assert math.isclose(m.profit_factor_gross, 2.0, rel_tol=1e-12)

    assert math.isclose(m.expectancy_r_net, 0.18, rel_tol=1e-12)
    assert math.isclose(m.win_rate_net, 0.4, rel_tol=1e-12)
    assert math.isclose(m.profit_factor_net, 1.5, rel_tol=1e-12)


def test_dispersion_hand_computed():
    # sample stdev of GROSS (ddof=1): mean=0.3, sum((x-mean)^2)=5.8, /4=1.45
    # stdev=sqrt(1.45)=1.2041594... ; SE=stdev/sqrt(5)=0.5385164...
    # t = 0.3/0.5385164... = 0.5570860...
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 1, 0, 0, 1)
    assert math.isclose(m.stdev_r_gross, 1.2041594578792296, rel_tol=1e-9)
    assert math.isclose(m.se_r_gross, 0.5385164807134504, rel_tol=1e-9)
    assert math.isclose(m.t_stat_gross, 0.5570860145311556, rel_tol=1e-9)


def test_dispersion_none_for_single_trade():
    trades, breakdowns = _make(gross=[1.0], net=[0.9])
    m = compute_metrics(trades, breakdowns, 1, 1, 0, 0)
    assert m.stdev_r_gross is None
    assert m.se_r_gross is None
    assert m.t_stat_gross is None


def test_max_drawdown_hand_traced():
    # cumulative net-R: 0.9, -0.2, 1.6, 1.0, 0.9
    # running peak:     0.9,  0.9, 1.6, 1.6, 1.6
    # drawdown:         0.0,  1.1, 0.0, 0.6, 0.7  -> max = 1.1
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 1, 0, 0, 1)
    assert math.isclose(m.max_drawdown_r, 1.1, rel_tol=1e-12)


def test_drawdown_uses_exit_ts_order_not_list_order():
    trades, breakdowns = _make()
    shuffled_trades = [trades[2], trades[0], trades[4], trades[1], trades[3]]
    shuffled_bd = [breakdowns[2], breakdowns[0], breakdowns[4],
                  breakdowns[1], breakdowns[3]]
    m = compute_metrics(shuffled_trades, shuffled_bd, 1, 0, 0, 1)
    assert math.isclose(m.max_drawdown_r, 1.1, rel_tol=1e-12)


def test_scale_in_rate_vs_part2_fill_rate_distinction():
    # 10 sessions: 5 part1-only, 3 part2-only, 2 both.
    # scale_in_rate (both/n)      = 2/10 = 0.2  <- the unambiguous number
    # part2_fill_rate (any part2) = (3+2)/10 = 0.5  <- kept for continuity
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, n_sessions=10,
                        n_sessions_part1_only=5, n_sessions_part2_only=3,
                        n_sessions_both=2)
    assert math.isclose(m.scale_in_rate, 0.2, rel_tol=1e-12)
    assert math.isclose(m.part2_fill_rate, 0.5, rel_tol=1e-12)


def test_rates_none_when_no_sessions():
    m = compute_metrics([], [], 0, 0, 0, 0)
    assert m.scale_in_rate is None
    assert m.part2_fill_rate is None


def test_empty_trades_all_none_not_crash():
    m = compute_metrics([], [], 0, 0, 0, 0)
    assert m.n_trades == 0
    assert m.expectancy_r_gross is None
    assert m.win_rate_gross is None
    assert m.profit_factor_gross is None
    assert m.expectancy_r_net is None
    assert m.max_drawdown_r is None
    assert m.stdev_r_gross is None


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
    assert math.isclose(m.expectancy_r_gross, 0.0, abs_tol=1e-12)


def test_n_trades_matches_list_length():
    trades, breakdowns = _make()
    m = compute_metrics(trades, breakdowns, 3, 2, 0, 1)
    assert m.n_trades == 5
