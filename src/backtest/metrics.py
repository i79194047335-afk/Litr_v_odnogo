"""
Slice 5 of the backtester: metrics.

Aggregates a Trade list (Slice 3) and its CostBreakdown list (Slice 4a) into
the numbers docs/ytc_scalper_skeleton.md §4.5 / CONTEXT.md work-plan item 9
actually asked for: expectancy in R, win-rate, profit factor, max drawdown,
part-2 fill rate.

SIMPLIFICATION, named not hidden: expectancy/win-rate/profit-factor are
computed PER PART — each Trade record (part1 and part2 are separate entries
in the list) counts as its own R-bearing outcome, not aggregated per session.
This is defensible: part1 and part2 have genuinely different entry prices and
therefore different risk, even though they share one stop and one directional
bet. But it's a choice, not a law — if these numbers look distorted by the
split (e.g. a session that filled both parts effectively gets "two trades"
in the stats), a session-level aggregate (size-weighted R per session) is the
documented alternative to build if needed.

DRAWDOWN UNIT: cumulative net-R, not currency or %. This project has no
account-equity concept anywhere yet (no starting balance, no position sizing
beyond part_size=1 unit) — a dollar or percentage drawdown would need one,
and picking an arbitrary balance to make the number look like a number would
be worse than being explicit that it's not modeled. R units are portable and
consistent with expectancy already being reported in R.

WHAT'S EXCLUDED, on purpose: a position still open when the tick data ends
(WFStrategy.has_open_position) is unrealized — never reached _flat_reset, so
it's in neither self.trades nor the session counters. Same "don't fabricate
closure" discipline as replay.py's still-open final bar. The runner reports
this count as a caveat, not as a metric.

CORRECTED 2026-07-04 — session outcomes are three-way, not binary:
`n_sessions_part1_only`, `n_sessions_part2_only`, `n_sessions_both`. A single
"part2 fill rate" flag conflated genuine scale-in (both parts filled) with
part1 simply never filling (see strategy.py's _flat_reset comment — this was
found on real data: 13 "both" vs 666 "part2 only" out of 2909 sessions, a
huge difference the old binary metric hid). `part2_fill_rate` below keeps the
original meaning (any session where part2 ended up filled, regardless of
part1) for continuity; `scale_in_rate` is the new, unambiguous "both parts
joined" number — read that one, not part2_fill_rate, if the question is
"is the two-part entry actually averaging in".

DISPERSION, added 2026-07-04: stdev and standard error on the R-multiple
distributions (gross and net), plus a rough t-statistic (expectancy / SE).
This is a SANITY CHECK, not a rigorous test — it assumes trades are
independent (questionable; they share overlapping market regimes) and
roughly normal. Treat |t| < ~2 as "can't rule out this is noise", not as a
formal hypothesis-test verdict.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from src.backtest.strategy import Trade
from src.backtest.costs import CostBreakdown


def _expectancy(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _win_rate(values: list[float]) -> float | None:
    return sum(1 for v in values if v > 0) / len(values) if values else None


def _profit_factor(values: list[float]) -> float | None:
    """sum(wins) / abs(sum(losses)). math.inf if there are wins and no
    losses (a genuinely undefined-but-meaningful case). None if there is
    nothing to divide (no trades, or every trade exactly breakeven)."""
    pos = sum(v for v in values if v > 0)
    neg = abs(sum(v for v in values if v < 0))
    if neg == 0:
        return math.inf if pos > 0 else None
    return pos / neg


def _stdev(values: list[float]) -> float | None:
    """Sample stdev (ddof=1). None if fewer than 2 values (undefined)."""
    return statistics.stdev(values) if len(values) >= 2 else None


def _standard_error(values: list[float]) -> float | None:
    sd = _stdev(values)
    return sd / math.sqrt(len(values)) if sd is not None else None


def _t_stat(expectancy: float | None, se: float | None) -> float | None:
    """Rough expectancy/SE ratio — a sanity check, not a formal test (see
    module docstring's caveat on independence/normality assumptions)."""
    if expectancy is None or se is None or se == 0:
        return None
    return expectancy / se


def _max_drawdown_r(ordered_net_r: list[float]) -> float | None:
    """Max peak-to-trough decline on the cumulative net-R curve, in the
    order given (caller sorts by exit_ts — realization order, not entry
    order, since drawdown is about equity as it's actually realized)."""
    if not ordered_net_r:
        return None
    cum = 0.0
    peak = float("-inf")
    max_dd = 0.0
    for v in ordered_net_r:
        cum += v
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


@dataclass(frozen=True)
class Metrics:
    n_trades: int
    n_sessions: int
    n_sessions_part1_only: int
    n_sessions_part2_only: int
    n_sessions_both: int
    part2_fill_rate: float | None    # any part2 fill, regardless of part1 — kept for continuity
    scale_in_rate: float | None      # BOTH parts filled — the unambiguous scale-in number

    expectancy_r_gross: float | None
    win_rate_gross: float | None
    profit_factor_gross: float | None
    stdev_r_gross: float | None
    se_r_gross: float | None
    t_stat_gross: float | None

    expectancy_r_net: float | None
    win_rate_net: float | None
    profit_factor_net: float | None
    stdev_r_net: float | None
    se_r_net: float | None
    t_stat_net: float | None

    max_drawdown_r: float | None     # on cumulative net_r_multiple, by exit_ts


def compute_metrics(trades: list[Trade], breakdowns: list[CostBreakdown],
                    n_sessions: int, n_sessions_part1_only: int,
                    n_sessions_part2_only: int, n_sessions_both: int) -> Metrics:
    """Pure aggregation — no I/O, no side effects. `breakdowns` must be the
    same trades in the same order as `trades` (as apply_costs_to_trades
    produces); this is not re-validated here since both come from one
    strategy run in the runner."""
    gross_r = [t.r_multiple for t in trades]
    net_r = [cb.net_r_multiple for cb in breakdowns]

    # drawdown needs realization order (exit_ts), not list order.
    by_exit = sorted(zip(breakdowns, net_r), key=lambda pair: pair[0].trade.exit_ts)
    ordered_net_r = [r for _, r in by_exit]

    any_part2 = n_sessions_part2_only + n_sessions_both
    part2_rate = (any_part2 / n_sessions) if n_sessions > 0 else None
    scale_in_rate = (n_sessions_both / n_sessions) if n_sessions > 0 else None

    exp_g, exp_n = _expectancy(gross_r), _expectancy(net_r)
    se_g, se_n = _standard_error(gross_r), _standard_error(net_r)

    return Metrics(
        n_trades=len(trades),
        n_sessions=n_sessions,
        n_sessions_part1_only=n_sessions_part1_only,
        n_sessions_part2_only=n_sessions_part2_only,
        n_sessions_both=n_sessions_both,
        part2_fill_rate=part2_rate,
        scale_in_rate=scale_in_rate,
        expectancy_r_gross=exp_g,
        win_rate_gross=_win_rate(gross_r),
        profit_factor_gross=_profit_factor(gross_r),
        stdev_r_gross=_stdev(gross_r),
        se_r_gross=se_g,
        t_stat_gross=_t_stat(exp_g, se_g),
        expectancy_r_net=exp_n,
        win_rate_net=_win_rate(net_r),
        profit_factor_net=_profit_factor(net_r),
        stdev_r_net=_stdev(net_r),
        se_r_net=se_n,
        t_stat_net=_t_stat(exp_n, se_n),
        max_drawdown_r=_max_drawdown_r(ordered_net_r),
    )
