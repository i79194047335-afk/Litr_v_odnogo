"""
Slice 5 of the backtester: the runner.

Ties every prior slice together: real tick JSONL -> Replay (range bars + 1m
EMA) -> FillEngine (fills, slippage, fill-probability) -> WFStrategy (bias,
zones, entries, stop, take, reversal, optional trailing) -> costs -> metrics.

This is the actual deliverable of the whole project so far: run it against
collected live data and read whether the mechanized WF subset has edge.

ALL Slice-4 knobs default to OFF/neutral here too, matching their classes:
running with just --files and --range-size gives the pure, cost-free Slice 3
result. Add one knob at a time (--maker-bps, --slippage-ticks, --trailing...)
to see its isolated effect on expectancy — that isolation was the entire
point of building them as separate, defaultable parameters instead of one
lump "realism" setting.

Usage:
    python -m src.backtest.run_backtest \\
        --files data/ticks/trades_1_20260629.jsonl data/ticks/trades_1_20260630.jsonl \\
        --range-size 15.3 --tick-size 0.1

Files must be passed in chronological order — each is read as one stream in
sequence (a real day's JSONL is already time-ordered from the collector;
this does not re-sort across files, since a global sort would defeat the
point of streaming multi-gigabyte inputs without loading them into memory).
"""
from __future__ import annotations

import argparse
import itertools
import random
import sys
from pathlib import Path

from src.rangebars.calibrate import iter_price_ts
from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy
from src.backtest.costs import apply_costs_to_trades
from src.backtest.metrics import compute_metrics


def load_ticks(paths: list[Path]):
    """Chain (price, ts) across files in the given order. Streaming — does
    not materialize the full tick list in memory."""
    return itertools.chain.from_iterable(iter_price_ts(p) for p in paths)


def _fmt(x, pct=False) -> str:
    if x is None:
        return "n/a"
    if x == float("inf"):
        return "inf (no losing trades)"
    return f"{x:.2%}" if pct else f"{x:.4f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the WF backtest over tick JSONL")
    ap.add_argument("--files", nargs="+", required=True,
                    help="JSONL tick files, IN CHRONOLOGICAL ORDER")
    ap.add_argument("--range-size", type=float, required=True)
    ap.add_argument("--ema-fast", type=int, default=15)
    ap.add_argument("--ema-slow", type=int, default=20)
    ap.add_argument("--keltner-period", type=int, default=35)
    ap.add_argument("--mult-inner", type=float, default=4.0)
    ap.add_argument("--mult-outer", type=float, default=8.0)
    ap.add_argument("--part-size", type=float, default=1.0)
    ap.add_argument("--trailing", action="store_true", default=False)
    ap.add_argument("--slippage-ticks", type=float, default=0.0)
    ap.add_argument("--tick-size", type=float, default=0.0)
    ap.add_argument("--fill-probability", type=float, default=1.0)
    ap.add_argument("--maker-bps", type=float, default=0.0)
    ap.add_argument("--taker-bps", type=float, default=0.0)
    ap.add_argument("--hourly-funding-rate", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for fill_probability rolls (reproducible runs)")
    args = ap.parse_args()

    files = [Path(f) for f in args.files]
    for f in files:
        if not f.exists():
            print(f"ERROR: file not found: {f}", file=sys.stderr)
            sys.exit(2)

    replay = Replay(range_size=args.range_size, ema_fast=args.ema_fast,
                    ema_slow=args.ema_slow)
    engine = FillEngine(slippage_ticks=args.slippage_ticks,
                        tick_size=args.tick_size,
                        fill_probability=args.fill_probability,
                        rng=random.Random(args.seed) if args.seed is not None else None)
    strategy = WFStrategy(replay, engine, keltner_period=args.keltner_period,
                          mult_inner=args.mult_inner, mult_outer=args.mult_outer,
                          part_size=args.part_size, trailing=args.trailing)

    print(f"reading {len(files)} file(s)...")
    replay.run(load_ticks(files), strategy)

    breakdowns = apply_costs_to_trades(
        strategy.trades, maker_bps=args.maker_bps, taker_bps=args.taker_bps,
        hourly_rate=args.hourly_funding_rate,
    )
    m = compute_metrics(strategy.trades, breakdowns,
                        strategy.n_sessions, strategy.n_sessions_part1_only,
                        strategy.n_sessions_part2_only, strategy.n_sessions_both)

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

    if args.maker_bps == 0 and args.taker_bps == 0 and args.hourly_funding_rate == 0:
        print()
        print("NOTE: costs are all zero (defaults) — gross and net differ only")
        print("      by rounding. Pass --maker-bps/--taker-bps/--hourly-funding-rate")
        print("      to see their isolated effect.")


if __name__ == "__main__":
    main()
