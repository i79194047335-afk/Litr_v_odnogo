"""
Acceptance-window variant — tests widening the swing-break confirmation
window (`acceptance_bars` in check_break_and_acceptance) independently of
`swing_confirm_bars`.

Subclasses WFStrategy to route the swing-mode reversal check through the
generalized `check_break_and_acceptance` with a configurable
`acceptance_bars`, instead of the hardcoded default of 1. Does not modify
strategy.py.

Runs the standard pipeline (Replay + FillEngine + AcceptanceWindowStrategy +
costs + metrics) on in-sample and out-of-sample BTC data separately, for
acceptance_bars in (1, 3, 5), printing the same metrics block as
run_backtest.py. acceptance_bars=1 should reproduce the already-on-record
baseline (in-sample expectancy -0.5046 bps / t=-2.95, out-of-sample
-0.1740 bps / t=-0.86) as a harness sanity check.

Usage:
    python -m src.backtest.acceptance_variant
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

from src.rangebars.calibrate import iter_price_ts
from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy
from src.backtest.swings import check_break_and_acceptance
from src.backtest.costs import apply_costs_to_trades
from src.backtest.metrics import compute_metrics

# ---------------------------------------------------------------------------
# BTC data — same as bias_audit.py / no_reversal_variant.py
# ---------------------------------------------------------------------------

BTC_DIR = Path("data/ticks")
RANGE_SIZE = 15.3
TICK_SIZE = 0.1

CALIBRATION_DAYS = ["20260629", "20260630", "20260701", "20260702"]
OOS_DAYS = ["20260703", "20260704", "20260705", "20260706"]

ACCEPTANCE_BARS_VALUES = [1, 3, 5]


def btc_files(days: list[str]) -> list[Path]:
    files = [BTC_DIR / f"trades_1_{d}.jsonl" for d in days]
    missing = [f for f in files if not f.exists()]
    if missing:
        print(f"ERROR: missing files: {missing}", file=sys.stderr)
        sys.exit(2)
    return files


# ---------------------------------------------------------------------------
# AcceptanceWindowStrategy
# ---------------------------------------------------------------------------

class AcceptanceWindowStrategy(WFStrategy):
    """Swing-mode reversal exit with a configurable acceptance window
    (default 1, matching WFStrategy's own hardcoded behavior exactly).
    Everything else (entries, costs, trailing if enabled) is unchanged."""

    def __init__(self, *args, acceptance_bars: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self.acceptance_bars = acceptance_bars

    def _check_reversal_swing_mode(self, bar) -> bool:
        exit_now, self._break_pending = check_break_and_acceptance(
            self._side, self.swings.last_swing_low, self.swings.last_swing_high,
            self._break_pending, bar, acceptance_bars=self.acceptance_bars,
        )
        return exit_now


# ---------------------------------------------------------------------------
# Formatting — borrowed from run_backtest.py / no_reversal_variant.py
# ---------------------------------------------------------------------------

def _fmt(x, pct=False):
    if x is None:
        return "n/a"
    if pct and isinstance(x, float) and x == float("inf"):
        return "100%"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    if pct:
        return f"{x:.2%}"
    return f"{x:.2f}"


# ---------------------------------------------------------------------------
# Run one file set at one acceptance_bars value
# ---------------------------------------------------------------------------

def run_variant(files: list[Path], label: str, acceptance_bars: int) -> None:
    print(f"\n{'=' * 70}")
    print(f"  ACCEPTANCE-WINDOW VARIANT — {label}  (acceptance_bars={acceptance_bars})")
    print(f"  exit_mode=swing  trailing=False  range_size={RANGE_SIZE}  exchange=1 (BTC)")
    print(f"{'=' * 70}")

    replay = Replay(range_size=RANGE_SIZE)
    engine = FillEngine(tick_size=TICK_SIZE)
    strategy = AcceptanceWindowStrategy(
        replay, engine,
        exit_mode="swing", trailing=False,
        acceptance_bars=acceptance_bars,
    )

    ticks = itertools.chain.from_iterable(iter_price_ts(p) for p in files)
    replay.run(ticks, strategy)

    breakdowns = apply_costs_to_trades(strategy.trades)
    m = compute_metrics(
        strategy.trades, breakdowns,
        strategy.n_sessions, strategy.n_sessions_part1_only,
        strategy.n_sessions_part2_only, strategy.n_sessions_both,
    )

    print()
    print(f"ticks processed      : {replay.n_ticks:,}")
    print(f"range bars closed    : {len(replay.bars):,}")
    print(f"sessions (positions) : {m.n_sessions:,}")
    if strategy.has_open_position:
        print("NOTE: one position was still open at end of data — "
              "excluded from all metrics below (unrealized).")
    print()
    print(f"trades (per part)    : {m.n_trades:,}")
    print(f"  part1 only         : {m.n_sessions_part1_only:,}")
    print(f"  part2 only         : {m.n_sessions_part2_only:,}  "
          f"(part1 never filled — price passed its level before refresh)")
    print(f"  both (scale-in)    : {m.n_sessions_both:,}")
    print(f"scale-in rate        : {_fmt(m.scale_in_rate, pct=True)} "
          f"(both parts filled)")
    print(f"part-2 fill rate     : {_fmt(m.part2_fill_rate, pct=True)} "
          f"(any part2 fill, incl. part2-only — prefer scale-in rate above)")
    print()
    print("            (bps = basis points of entry price; 1 bps = 0.01%)")
    print("                        gross         net")
    print(f"expectancy (bps)      {_fmt(m.bps_gross.expectancy):>10}   {_fmt(m.bps_net.expectancy):>10}")
    print(f"stdev (bps)           {_fmt(m.bps_gross.stdev):>10}   {_fmt(m.bps_net.stdev):>10}")
    print(f"std error (bps)       {_fmt(m.bps_gross.se):>10}   {_fmt(m.bps_net.se):>10}")
    print(f"t-stat (expect./SE)   {_fmt(m.bps_gross.t_stat):>10}   {_fmt(m.bps_net.t_stat):>10}")
    print(f"win rate              {_fmt(m.win_rate_gross, pct=True):>10}   {_fmt(m.win_rate_net, pct=True):>10}")
    print(f"profit factor         {_fmt(m.profit_factor_gross):>10}   {_fmt(m.profit_factor_net):>10}")
    print(f"                      (t-stat is a rough sanity check only — |t|<~2")
    print(f"                       means 'can't rule out noise', not a formal test)")
    print()
    print("R-multiple (denominator is distance to line-0 stop — meaningful")
    print("only for take exits; misleading overall since the stop rarely fires):")
    print(f"expectancy (R)        {_fmt(m.r_gross.expectancy):>10}   {_fmt(m.r_net.expectancy):>10}")
    print()
    print(f"max drawdown (net, bps) : {_fmt(m.max_drawdown_bps)}")
    print(f"max drawdown (net, R)   : {_fmt(m.max_drawdown_r)}")

    print()
    print("by exit reason        n     mean bps(gross) mean bps(net)  win% (net)")
    for g in m.by_exit_reason:
        print(f"  {g.label:12} {g.n:>7,}   {g.mean_bps_gross:>12.2f}  "
              f"{g.mean_bps_net:>12.2f}  {g.win_rate_net:>8.2%}")
    print()
    print("by entry part         n     mean bps(gross) mean bps(net)  win% (net)")
    for g in m.by_tag:
        print(f"  {g.label:12} {g.n:>7,}   {g.mean_bps_gross:>12.2f}  "
              f"{g.mean_bps_net:>12.2f}  {g.win_rate_net:>8.2%}")

    print()
    print("NOTE: costs are all zero (defaults) — gross and net differ only")
    print(f"      by rounding. acceptance_bars={acceptance_bars} means a break must")
    print(f"      hold for {acceptance_bars} bar(s) after the break bar before the")
    print("      reversal exit fires (acceptance_bars=1 is the pre-2026-07-07 default).")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("  ACCEPTANCE-WINDOW VARIANT — SWEEP OVER acceptance_bars = 1, 3, 5")
    print("=" * 70)

    cal_files = btc_files(CALIBRATION_DAYS)
    oos_files = btc_files(OOS_DAYS)

    for ab in ACCEPTANCE_BARS_VALUES:
        run_variant(cal_files, "In-sample (Jun 29 – Jul 2)", ab)
        run_variant(oos_files, "Out-of-sample (Jul 3–6)", ab)


if __name__ == "__main__":
    main()
