"""
Slice 5 of the backtester: metrics.

Aggregates a Trade list (Slice 3) and its CostBreakdown list (Slice 4a) into
the numbers docs/ytc_scalper_skeleton.md §4.5 / CONTEXT.md work-plan item 9
asked for: expectancy, win-rate, profit factor, max drawdown, part-2 fill rate.

SIMPLIFICATION, named not hidden: expectancy/win-rate/profit-factor are
computed PER PART — each Trade record (part1 and part2 are separate entries
in the list) counts as its own outcome, not aggregated per session. Part1 and
part2 have genuinely different entry prices, so this is defensible, but it's a
choice — a session-level aggregate is the documented alternative if the split
distorts things.

WHAT'S EXCLUDED, on purpose: a position still open when the tick data ends
(WFStrategy.has_open_position) is unrealized — never reached _flat_reset, so
it's in neither self.trades nor the session counters. Same "don't fabricate
closure" discipline as replay.py's still-open final bar.

SESSION OUTCOMES are three-way, not binary: `n_sessions_part1_only`,
`n_sessions_part2_only`, `n_sessions_both`. A single "part2 fill rate" flag
conflated genuine scale-in (both filled) with part1 simply never filling
(found on real data: 13 "both" vs 666 "part2 only" of 2909 sessions).
`part2_fill_rate` keeps the original meaning (any part2 fill) for continuity;
`scale_in_rate` is the unambiguous "both parts joined" number.

R vs bps — the reason bps is the headline unit: R-multiple divides each
trade's PnL by |entry - stop|, the distance to the line-0 stop. On real BTC
data the stop fired 1 time in 2909 — trades exit by reversal-bar far before
reaching it — so that denominator is almost never the realized risk, and
every R number is scaled by a risk that didn't happen. bps (basis points of
entry price, 1 bps = 0.01%) divides by entry price instead, near-constant
over a short window, so bps expectancy reflects the actual price move
captured, undistorted. bps is the headline; R is kept because for the rare
take trades the stop distance genuinely IS the risk, so their R is meaningful
and comparable to how Beggs would think about them.

DISPERSION: stdev, standard error and a rough t-stat (expectancy/SE) on each
series. SANITY CHECK, not a formal test — assumes trades are independent
(questionable; shared market regimes) and roughly normal. |t| < ~2 means
"can't rule out noise", not a verdict.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass

from src.backtest.strategy import Trade
from src.backtest.costs import CostBreakdown


def _expectancy(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _win_rate(values: list[float]) -> float | None:
    return sum(1 for v in values if v > 0) / len(values) if values else None


def _profit_factor(values: list[float]) -> float | None:
    """sum(wins) / abs(sum(losses)). math.inf if there are wins and no
    losses (genuinely undefined-but-meaningful). None if nothing to divide."""
    pos = sum(v for v in values if v > 0)
    neg = abs(sum(v for v in values if v < 0))
    if neg == 0:
        return math.inf if pos > 0 else None
    return pos / neg


def _stdev(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def _standard_error(values: list[float]) -> float | None:
    sd = _stdev(values)
    return sd / math.sqrt(len(values)) if sd is not None else None


def _t_stat(expectancy: float | None, se: float | None) -> float | None:
    if expectancy is None or se is None or se == 0:
        return None
    return expectancy / se


def _bps(pnl: float, size: float, entry_price: float) -> float:
    """Per-trade PnL as basis points of entry price (1 bps = 0.01%).

    THE honest scalping unit — see module docstring's R-vs-bps note. pnl is
    the TOTAL (size-scaled) amount, so divide by (size * entry_price) for a
    per-unit fraction, then * 10000 for bps."""
    denom = size * entry_price
    return (pnl / denom) * 10000.0 if denom > 0 else 0.0


@dataclass(frozen=True)
class StatBlock:
    """expectancy / stdev / standard error / rough t-stat for one series,
    bundled so gross and net, R and bps, don't each need four loose fields."""
    expectancy: float | None
    stdev: float | None
    se: float | None
    t_stat: float | None


def _stat_block(values: list[float]) -> StatBlock:
    exp = _expectancy(values)
    se = _standard_error(values)
    return StatBlock(expectancy=exp, stdev=_stdev(values), se=se,
                     t_stat=_t_stat(exp, se))


def _max_drawdown(ordered: list[float]) -> float | None:
    """Max peak-to-trough decline on a cumulative curve, in the order given
    (caller sorts by exit_ts — realization order)."""
    if not ordered:
        return None
    cum = 0.0
    peak = float("-inf")
    max_dd = 0.0
    for v in ordered:
        cum += v
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


@dataclass(frozen=True)
class GroupStat:
    """One row of a breakdown: a subset of trades sharing a label (an
    exit_reason like "stop", or a tag like "part1"), with count, mean R and
    mean bps (gross+net) and net win rate. Decomposes a good aggregate — e.g.
    whether a positive overall result is carried by one exit type or one entry
    part while another quietly bleeds."""
    label: str
    n: int
    mean_r_gross: float
    mean_r_net: float
    mean_bps_gross: float
    mean_bps_net: float
    win_rate_net: float


def _group_stats(records: list[tuple[float, float, float, float, str]]) -> list[GroupStat]:
    """records: (gross_r, net_r, gross_bps, net_bps, label). One GroupStat
    per distinct label, sorted by label for deterministic output."""
    groups: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    for gr, nr, gb, nb, label in records:
        groups[label].append((gr, nr, gb, nb))
    out: list[GroupStat] = []
    for label in sorted(groups):
        rows = groups[label]
        n = len(rows)
        out.append(GroupStat(
            label=label, n=n,
            mean_r_gross=sum(r[0] for r in rows) / n,
            mean_r_net=sum(r[1] for r in rows) / n,
            mean_bps_gross=sum(r[2] for r in rows) / n,
            mean_bps_net=sum(r[3] for r in rows) / n,
            win_rate_net=sum(1 for r in rows if r[1] > 0) / n,
        ))
    return out


@dataclass(frozen=True)
class Metrics:
    n_trades: int
    n_sessions: int
    n_sessions_part1_only: int
    n_sessions_part2_only: int
    n_sessions_both: int
    part2_fill_rate: float | None    # any part2 fill, regardless of part1 — continuity
    scale_in_rate: float | None      # BOTH parts filled — the unambiguous number

    # R-multiple stats: meaningful for take trades (stop distance IS the risk
    # there), misleading overall since the stop rarely fires. See bps below.
    r_gross: StatBlock
    r_net: StatBlock
    win_rate_gross: float | None
    win_rate_net: float | None
    profit_factor_gross: float | None
    profit_factor_net: float | None

    # bps-of-entry-price stats — THE headline unit (see module docstring).
    bps_gross: StatBlock
    bps_net: StatBlock

    max_drawdown_r: float | None     # on cumulative net_r_multiple, by exit_ts
    max_drawdown_bps: float | None   # on cumulative net_bps, by exit_ts

    by_exit_reason: list[GroupStat]  # take / stop / reversal
    by_tag: list[GroupStat]          # part1 / part2


def compute_metrics(trades: list[Trade], breakdowns: list[CostBreakdown],
                    n_sessions: int, n_sessions_part1_only: int,
                    n_sessions_part2_only: int, n_sessions_both: int) -> Metrics:
    """Pure aggregation — no I/O, no side effects. `breakdowns` must be the
    same trades in the same order as `trades` (as apply_costs_to_trades
    produces)."""
    gross_r = [t.r_multiple for t in trades]
    net_r = [cb.net_r_multiple for cb in breakdowns]
    gross_bps = [_bps(cb.gross_pnl, t.size, t.entry_price)
                 for t, cb in zip(trades, breakdowns)]
    net_bps = [_bps(cb.net_pnl, t.size, t.entry_price)
               for t, cb in zip(trades, breakdowns)]

    # drawdown needs realization order (exit_ts). Sort one index so R and bps
    # curves stay row-aligned.
    order = sorted(range(len(breakdowns)),
                   key=lambda i: breakdowns[i].trade.exit_ts)
    ordered_net_r = [net_r[i] for i in order]
    ordered_net_bps = [net_bps[i] for i in order]

    any_part2 = n_sessions_part2_only + n_sessions_both
    part2_rate = (any_part2 / n_sessions) if n_sessions > 0 else None
    scale_in_rate = (n_sessions_both / n_sessions) if n_sessions > 0 else None

    reason_records = [(gross_r[i], net_r[i], gross_bps[i], net_bps[i],
                       trades[i].exit_reason) for i in range(len(trades))]
    tag_records = [(gross_r[i], net_r[i], gross_bps[i], net_bps[i],
                    trades[i].tag) for i in range(len(trades))]

    return Metrics(
        n_trades=len(trades),
        n_sessions=n_sessions,
        n_sessions_part1_only=n_sessions_part1_only,
        n_sessions_part2_only=n_sessions_part2_only,
        n_sessions_both=n_sessions_both,
        part2_fill_rate=part2_rate,
        scale_in_rate=scale_in_rate,
        r_gross=_stat_block(gross_r),
        r_net=_stat_block(net_r),
        win_rate_gross=_win_rate(gross_r),
        win_rate_net=_win_rate(net_r),
        profit_factor_gross=_profit_factor(gross_r),
        profit_factor_net=_profit_factor(net_r),
        bps_gross=_stat_block(gross_bps),
        bps_net=_stat_block(net_bps),
        max_drawdown_r=_max_drawdown(ordered_net_r),
        max_drawdown_bps=_max_drawdown(ordered_net_bps),
        by_exit_reason=_group_stats(reason_records),
        by_tag=_group_stats(tag_records),
    )
