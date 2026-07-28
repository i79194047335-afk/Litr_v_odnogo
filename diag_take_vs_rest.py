#!/usr/bin/env python3
"""
Does anything visible BEFORE entry predict which trades reach the take
target? That is the only question that matters about the take population:
"+9.75 bps, ~100% win" is arithmetic, not an edge — a trade that exits at
the take target is profitable by definition. An edge exists only if the
take population is separable EX ANTE.

Split every trade into (A) exit_reason == "take" and (B) everything else,
then compare five pre-entry metrics between the groups:

  1. vol_bps    — (max high - min low) over the N bars before entry, in bps
                  of entry price. "How choppy going in."
  2. ticks/bar  — mean n_ticks over those bars. Participation proxy.
  3. imbalance  — |buy_vol - sell_vol| / total over those bars. One-sidedness
                  of taker flow.
  4. swing_rate — swing-point confirmations per bar over those bars. HIGH
                  means chop (local extrema everywhere), LOW means trend
                  (in a clean run no local extremum can confirm). Added
                  2026-07-23 after the swing visualizer showed density is
                  ~1.8x higher in flat regimes on synthetic data.
  5. level_age  — bars since the swing level the exit rule reads last moved.
                  Stale level = trend = wide exit; fresh level = chop =
                  tight exit. Free, causal, already computed.

Metrics 4-5 are the regime hypothesis; 1-3 were the original three.

WHAT THIS SCRIPT ADDS OVER PRINTING MEDIANS
  - A permutation test per metric. Without it a ratio of 1.15 on n=222 vs
    539 is uninterpretable — it could be pure noise. 20k relabelings,
    assumption-free, no scipy needed.
  - A filter projection: if you only took trades on one side of a threshold,
    what would net bps have been? This is the decision-relevant number; a
    significant median difference that doesn't move net bps is not a
    strategy. READ THE IN-SAMPLE WARNING IT PRINTS.

NO LOOKAHEAD: every metric is computed over bars strictly BEFORE the bar
the entry filled in. Verified by construction (window is [entry_bar-N,
entry_bar), right end exclusive).

Usage:
  python diag_take_vs_rest.py --market 1 \
      data/ticks/trades_1_20260629.jsonl data/ticks/trades_1_20260630.jsonl \
      data/ticks/trades_1_20260701.jsonl data/ticks/trades_1_20260702.jsonl

  # with the friction settings a real run would use
  python diag_take_vs_rest.py --market 1 --maker-bps 0.5 --taker-bps 2.0 \
      --slippage-ticks 1 data/ticks/*.jsonl
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics as st
import sys
from pathlib import Path

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy
from src.backtest.costs import apply_costs_to_trades


# ---------------------------------------------------------------------------
# tick reading (keeps side + size; iter_price_ts drops them)
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
# observer: per-bar aggregates + swing telemetry
# ---------------------------------------------------------------------------

class DiagStrategy(WFStrategy):
    """Subclass, don't modify. Records per-bar state AFTER the parent has
    processed the bar, so swing telemetry reflects exactly what the exit
    rule saw on that bar."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.bar_ends: list[int] = []
        self.bar_hi: list[float] = []
        self.bar_lo: list[float] = []
        self.bar_ntick: list[int] = []
        self.bar_swing_event: list[int] = []   # 0/1/2 swings confirmed on bar
        self.bar_age_low: list[int | None] = []
        self.bar_age_high: list[int | None] = []
        self._age_low: int | None = None
        self._age_high: int | None = None

    def on_range_bar(self, bar):
        before_h = self.swings.last_swing_high if self.swings else None
        before_l = self.swings.last_swing_low if self.swings else None
        super().on_range_bar(bar)
        self.bar_ends.append(bar.end_ts)
        self.bar_hi.append(bar.high)
        self.bar_lo.append(bar.low)
        self.bar_ntick.append(bar.n_ticks)

        n_ev = 0
        if self.swings is not None:
            new_h = self.swings.last_swing_high
            new_l = self.swings.last_swing_low
            if new_h is not None and new_h != before_h:
                n_ev += 1
                self._age_high = 0
            elif self._age_high is not None:
                self._age_high += 1
            if new_l is not None and new_l != before_l:
                n_ev += 1
                self._age_low = 0
            elif self._age_low is not None:
                self._age_low += 1
        self.bar_swing_event.append(n_ev)
        self.bar_age_low.append(self._age_low)
        self.bar_age_high.append(self._age_high)


def aggregate_flow(files: list[Path], bar_ends: list[int]):
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


METRIC_NAMES = ["vol_bps", "ticks/bar", "imbalance", "swing_rate", "level_age"]


def pre_entry_metrics(entry_bar: int, entry_price: float, side: str,
                      s: DiagStrategy, buy, sell, window: int):
    """All five metrics over [entry_bar-window, entry_bar) — right end
    EXCLUSIVE, so the entry bar itself never leaks in."""
    lo_i = max(0, entry_bar - window)
    hi_i = entry_bar
    if hi_i <= lo_i:
        return None
    window_hi = max(s.bar_hi[lo_i:hi_i])
    window_lo = min(s.bar_lo[lo_i:hi_i])
    vol_bps = (window_hi - window_lo) / entry_price * 1e4
    mean_ticks = st.mean(s.bar_ntick[lo_i:hi_i])
    b = sum(buy[lo_i:hi_i])
    sl = sum(sell[lo_i:hi_i])
    imb = abs(b - sl) / (b + sl) if (b + sl) > 0 else 0.0
    swing_rate = sum(s.bar_swing_event[lo_i:hi_i]) / (hi_i - lo_i)

    # the level THIS side's exit rule reads: long exits on swing_low
    ages = s.bar_age_low if side == "long" else s.bar_age_high
    age = ages[hi_i - 1]
    if age is None:
        return None                      # no structure yet — honest skip
    return (vol_bps, mean_ticks, imb, swing_rate, float(age))


# ---------------------------------------------------------------------------
# statistics — permutation test, no scipy
# ---------------------------------------------------------------------------

def perm_test(a: list[float], b: list[float], n_perm: int, rng: random.Random):
    """Two-sided permutation test on the difference of medians. Returns
    (observed_diff, p_value). Assumption-free: it asks how often a random
    relabeling of the SAME numbers produces a gap this large."""
    obs = st.median(a) - st.median(b)
    pool = a + b
    na = len(a)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        d = st.median(pool[:na]) - st.median(pool[na:])
        if abs(d) >= abs(obs) - 1e-15:
            hits += 1
    return obs, (hits + 1) / (n_perm + 1)


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Effect size in [-1,1]: P(a>b) - P(a<b). Interpretable regardless of
    n, unlike a p-value. |d|<0.15 negligible, <0.33 small, <0.47 medium."""
    sb = sorted(b)
    n = len(sb)
    gt = lt = 0
    for x in a:
        gt += bisect.bisect_left(sb, x)
        lt += n - bisect.bisect_right(sb, x)
    tot = len(a) * n
    return (gt - lt) / tot if tot else 0.0


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--range-size", type=float, default=15.3)
    ap.add_argument("--tick-size", type=float, default=0.1)
    ap.add_argument("--pre-window", type=int, default=10,
                     help="bars before entry to summarize")
    ap.add_argument("--maker-bps", type=float, default=0.0)
    ap.add_argument("--taker-bps", type=float, default=0.0)
    ap.add_argument("--slippage-ticks", type=float, default=0.0)
    ap.add_argument("--fill-probability", type=float, default=1.0)
    ap.add_argument("--n-perm", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    files = [Path(f) for f in args.files]
    for f in files:
        if not f.exists():
            print(f"missing: {f}", file=sys.stderr)
            sys.exit(2)

    replay = Replay(range_size=args.range_size)
    engine = FillEngine(tick_size=args.tick_size,
                        slippage_ticks=args.slippage_ticks,
                        fill_probability=args.fill_probability)
    strat = DiagStrategy(replay, engine, exit_mode="swing", trailing=False)

    def ticks():
        for p in files:
            for price, ts, side, size in iter_full(p):
                yield price, ts

    replay.run(ticks(), strat)
    buy, sell = aggregate_flow(files, strat.bar_ends)

    breakdowns = apply_costs_to_trades(strat.trades, maker_bps=args.maker_bps,
                                        taker_bps=args.taker_bps)
    net_bps_of = {}
    for t, cb in zip(strat.trades, breakdowns):
        denom = t.size * t.entry_price
        net_bps_of[id(t)] = (cb.net_pnl / denom) * 1e4 if denom > 0 else 0.0

    take, rest = [], []
    take_bps, rest_bps = [], []
    for t in strat.trades:
        eb = bisect.bisect_left(strat.bar_ends, t.entry_ts)
        eb = min(eb, len(strat.bar_ends) - 1)
        m = pre_entry_metrics(eb, t.entry_price, t.side, strat, buy, sell,
                              args.pre_window)
        if m is None:
            continue
        if t.exit_reason == "take":
            take.append(m)
            take_bps.append(net_bps_of[id(t)])
        else:
            rest.append(m)
            rest_bps.append(net_bps_of[id(t)])

    print(f"\n=== market {args.market}  range_size={args.range_size}  "
          f"{len(files)} file(s)  {len(replay.bars)} bars  "
          f"{len(strat.trades)} trades ===")
    print(f"pre-entry window = {args.pre_window} bars (exclusive of entry bar)")
    print(f"friction: maker={args.maker_bps}bps taker={args.taker_bps}bps "
          f"slip={args.slippage_ticks}tk fill_p={args.fill_probability}")
    if not take or not rest:
        print("\nOne of the groups is empty — nothing to compare.")
        return
    all_bps = take_bps + rest_bps
    print(f"\nTAKE n={len(take):<5} mean net {st.mean(take_bps):+7.2f} bps")
    print(f"REST n={len(rest):<5} mean net {st.mean(rest_bps):+7.2f} bps")
    print(f"ALL  n={len(all_bps):<5} mean net {st.mean(all_bps):+7.2f} bps"
          f"   <-- the number any filter must beat")

    rng = random.Random(args.seed)
    print(f"\n--- pre-entry metrics, TAKE vs REST "
          f"({args.n_perm} permutations) ---")
    print(f"{'metric':<11} {'take med':>9} {'rest med':>9} {'diff':>8} "
          f"{'p':>7} {'cliff d':>8}")
    results = []
    for k, name in enumerate(METRIC_NAMES):
        a = [r[k] for r in take]
        b = [r[k] for r in rest]
        obs, p = perm_test(a, b, args.n_perm, rng)
        d = cliffs_delta(a, b)
        results.append((name, k, p, d))
        print(f"{name:<11} {st.median(a):>9.2f} {st.median(b):>9.2f} "
              f"{obs:>+8.2f} {p:>7.4f} {d:>+8.2f}")

    print(f"\n  {len(METRIC_NAMES)} tests run -> for a 5% family-wise error "
          f"rate treat p < {0.05/len(METRIC_NAMES):.3f} as significant "
          f"(Bonferroni).")
    print("  Cliff's delta is the effect size: |d|<0.15 negligible, "
          "<0.33 small, <0.47 medium.")
    sig = [r for r in results if r[2] < 0.05 / len(METRIC_NAMES)]
    if not sig:
        print("\n  >>> NO metric separates take from rest at the corrected "
              "threshold. On this data the take population is NOT "
              "predictable ex ante from these five features. <<<")
    else:
        print(f"\n  >>> separating metric(s): "
              f"{', '.join(r[0] for r in sig)} <<<")

    # ---- filter projection -------------------------------------------
    print(f"\n--- IF you had filtered entries on each metric (IN-SAMPLE) ---")
    print("  For each metric, the threshold that maximizes mean net bps of")
    print("  the KEPT trades, plus how many trades survive.")
    rows = [(r, net_bps_of_row) for r, net_bps_of_row
            in zip(take + rest, take_bps + rest_bps)]
    baseline = st.mean(all_bps)
    print(f"{'metric':<11} {'rule':>18} {'kept':>6} {'mean net bps':>13} "
          f"{'vs all':>8}")
    for k, name in enumerate(METRIC_NAMES):
        vals = sorted(r[k] for r, _ in rows)
        best = None
        for q in range(5, 100, 5):
            thr = vals[int(len(vals) * q / 100)]
            for op, keep in (("<=", lambda v, t=thr: v <= t),
                              (">=", lambda v, t=thr: v >= t)):
                sel = [bp for r, bp in rows if keep(r[k])]
                if len(sel) < max(30, 0.1 * len(rows)):
                    continue           # refuse to "optimize" into a corner
                m = st.mean(sel)
                if best is None or m > best[0]:
                    best = (m, f"{name} {op} {thr:.2f}", len(sel))
        if best:
            print(f"{name:<11} {best[1]:>18} {best[2]:>6} {best[0]:>+13.2f} "
                  f"{best[0]-baseline:>+8.2f}")

    print("\n  !! These thresholds were CHOSEN AFTER SEEING THE OUTCOMES.")
    print("  !! Sweeping 19 cutoffs x 2 directions x 5 metrics will find an")
    print("  !! improvement in pure noise. Treat any winner here as a")
    print("  !! HYPOTHESIS to test on days not used here — not a result.")
    print("  !! If the permutation block above found nothing significant,")
    print("  !! these numbers are almost certainly overfitting.")


if __name__ == "__main__":
    main()
