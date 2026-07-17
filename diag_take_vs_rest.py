#!/usr/bin/env python3
"""
Diagnostic (standalone, does NOT touch the repo package): for each trade,
compute what conditions looked like in the WINDOW OF BARS just before
entry, then split trades into two groups —
  (A) reached the take-profit target ("take")
  (B) everything else (reversal / stop)
— and compare the pre-entry conditions between the two groups.

Three pre-entry metrics, all derivable from data we already collect:
  1. pre-entry volatility  = (max high - min low) over the N bars before
     entry, in bps of entry price — "how choppy was it going in"
  2. ticks per bar         = mean n_ticks over the N bars before entry —
     proxy for participation / liquidity
  3. aggressor imbalance   = |buy_vol - sell_vol| / (buy_vol + sell_vol)
     over the N bars before entry — one-sidedness of taker flow, the
     thing forex-style logic structurally can't see

Also runs the same strategy on whatever market_id / files you pass, so
the SAME script answers "does BTC (very liquid) behave differently from
SOL / HYPE / XAU (less liquid)" — Ivan's ligquidity hypothesis — without
any code change, just different files.

Usage:
  python diag_take_vs_rest.py --market 1 \
      data/ticks/trades_1_20260703.jsonl data/ticks/trades_1_20260704.jsonl ...

Prints a compact comparison table. No files written, no repo imports
beyond the already-installed strategy package.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy

PRE_WINDOW = 10  # bars before entry to summarize


# ---------------------------------------------------------------------------
# Read a tick file keeping side + size (iter_price_ts drops them)
# ---------------------------------------------------------------------------

def iter_full(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                yield float(r["p"]), int(r["t"]), r.get("side"), float(r["s"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue


# ---------------------------------------------------------------------------
# Observer: capture per-bar aggregates AND the entry bar index of each trade
# ---------------------------------------------------------------------------

class DiagStrategy(WFStrategy):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._bar_index = -1
        # per-bar aggregates, parallel to self-observed bars
        self.bar_ends: list[int] = []       # end_ts of each bar
        self.bar_hi: list[float] = []
        self.bar_lo: list[float] = []
        self.bar_ntick: list[int] = []

    def on_range_bar(self, bar):
        super().on_range_bar(bar)
        self._bar_index += 1
        self.bar_ends.append(bar.end_ts)
        self.bar_hi.append(bar.high)
        self.bar_lo.append(bar.low)
        self.bar_ntick.append(bar.n_ticks)


def bar_index_for_ts(ts: int, bar_ends: list[int]) -> int:
    """Index of the first bar whose end_ts >= ts (the bar the entry
    happened in). Bars are time-ordered."""
    import bisect
    i = bisect.bisect_left(bar_ends, ts)
    return min(i, len(bar_ends) - 1)


# ---------------------------------------------------------------------------
# Side/size aggregation per bar (second pass over ticks, bucketed by bar)
# ---------------------------------------------------------------------------

def aggregate_flow(files: list[Path], bar_ends: list[int]):
    """For each bar, sum buy vol and sell vol. Returns (buy[], sell[])
    aligned to bar index. Uses bar_ends to bucket each tick."""
    import bisect
    buy = [0.0] * len(bar_ends)
    sell = [0.0] * len(bar_ends)
    for p in files:
        for price, ts, side, size in iter_full(p):
            i = bisect.bisect_left(bar_ends, ts)
            if i >= len(bar_ends):
                i = len(bar_ends) - 1
            if side == "buy":
                buy[i] += size
            elif side == "sell":
                sell[i] += size
    return buy, sell


# ---------------------------------------------------------------------------
# Pre-entry metric computation for one trade
# ---------------------------------------------------------------------------

def pre_entry_metrics(entry_bar: int, entry_price: float,
                      hi, lo, ntick, buy, sell):
    lo_i = max(0, entry_bar - PRE_WINDOW)
    hi_i = entry_bar  # exclusive of entry bar itself
    if hi_i <= lo_i:
        return None
    window_hi = max(hi[lo_i:hi_i])
    window_lo = min(lo[lo_i:hi_i])
    vol_bps = (window_hi - window_lo) / entry_price * 1e4
    mean_ticks = statistics.mean(ntick[lo_i:hi_i])
    b = sum(buy[lo_i:hi_i])
    s = sum(sell[lo_i:hi_i])
    imb = abs(b - s) / (b + s) if (b + s) > 0 else 0.0
    return vol_bps, mean_ticks, imb


def summarize(label, rows):
    if not rows:
        print(f"  {label:20} (no trades)")
        return
    vols = [r[0] for r in rows]
    ticks = [r[1] for r in rows]
    imbs = [r[2] for r in rows]
    print(f"  {label:20} n={len(rows):>5}  "
          f"vol_bps median={statistics.median(vols):>7.1f}  "
          f"ticks/bar median={statistics.median(ticks):>6.1f}  "
          f"imbalance median={statistics.median(imbs):>5.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--range-size", type=float, default=15.3)
    ap.add_argument("--tick-size", type=float, default=0.1)
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    files = [Path(f) for f in args.files]
    for f in files:
        if not f.exists():
            print(f"missing: {f}", file=sys.stderr)
            sys.exit(2)

    replay = Replay(range_size=args.range_size)
    engine = FillEngine(tick_size=args.tick_size)
    strat = DiagStrategy(replay, engine, exit_mode="swing", trailing=False)

    def ticks():
        for p in files:
            for price, ts, side, size in iter_full(p):
                yield price, ts

    replay.run(ticks(), strat)

    buy, sell = aggregate_flow(files, strat.bar_ends)

    take_rows, rest_rows = [], []
    for t in strat.trades:
        eb = bar_index_for_ts(t.entry_ts, strat.bar_ends)
        m = pre_entry_metrics(eb, t.entry_price, strat.bar_hi, strat.bar_lo,
                              strat.bar_ntick, buy, sell)
        if m is None:
            continue
        if t.exit_reason == "take":
            take_rows.append(m)
        else:
            rest_rows.append(m)

    print(f"\n=== market {args.market}  range_size={args.range_size}  "
          f"{len(files)} file(s)  {len(replay.bars)} bars  "
          f"{len(strat.trades)} trades ===")
    print(f"(pre-entry window = {PRE_WINDOW} bars before each entry)")
    summarize("TAKE (reached target)", take_rows)
    summarize("REST (rev/stop)", rest_rows)

    # crude effect-size hint: ratio of medians
    if take_rows and rest_rows:
        import statistics as st
        def med(rows, i): return st.median([r[i] for r in rows])
        print("  ---")
        print(f"  take/rest ratio:  "
              f"vol={med(take_rows,0)/med(rest_rows,0):.2f}  "
              f"ticks={med(take_rows,1)/med(rest_rows,1):.2f}  "
              f"imbalance={med(take_rows,2)/med(rest_rows,2):.2f}")


if __name__ == "__main__":
    main()
