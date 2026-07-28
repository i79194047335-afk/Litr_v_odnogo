#!/usr/bin/env python3
"""
Is the swing structure denser in chop than in trend, on REAL data?

The visualizer showed swing points piling up in consolidation and going
quiet in clean trends. On synthetic random-walk data that effect measured
~1.8x (5.59 vs 3.16 swings per 20 bars). But a random walk has no real
regimes — its "trends" are realized noise, so that number is a mechanism
demo, NOT evidence about markets. This script runs the same measurement on
collector files.

Method: cut the bar series into non-overlapping windows, score each window
by DIRECTIONAL EFFICIENCY = |net displacement| / |path length| (0 = pure
chop, 1 = straight line), sort windows by it, and report swing density,
stage-1 break rate, and exit width per quartile.

Everything is causal — SwingTracker is fed bars in order and never sees the
future. Efficiency is used only to LABEL windows after the fact for
reporting; it is not a trading signal and must not be used as one without
building a causal version first.

Usage:
  python diag_regime.py --range-size 15.3 data/ticks/trades_1_2026070*.jsonl
  python diag_regime.py --range-size 15.3 --window 20 --confirm-bars 2 FILES...
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

from src.rangebars.builder import RangeBarBuilder
from src.backtest.swings import SwingTracker


def iter_price_ts(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                yield float(r["p"]), int(r["t"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range-size", type=float, required=True)
    ap.add_argument("--confirm-bars", type=int, default=2)
    ap.add_argument("--window", type=int, default=20,
                     help="bars per regime window")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    paths = [Path(f) for f in args.files]
    for p in paths:
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            sys.exit(2)

    RS = args.range_size
    builder = RangeBarBuilder(range_size=RS)
    tr = SwingTracker(confirm_bars=args.confirm_bars)

    bars = []
    n_swing = []          # swings confirmed on each bar
    is_break = []         # stage-1 break EVENT on each bar
    gap_to_low = []       # (close - swing_low)/RS, None before structure
    mags = []             # swing amplitudes in RS units
    gaps = []             # bars between consecutive swing confirmations
    last_conf = None
    prev_val = prev_kind = None
    below = above = False

    for path in paths:
        for price, ts in iter_price_ts(path):
            for b in (builder.update(price, ts) or ()):
                i = len(bars)
                bars.append(b)
                bh, bl = tr.last_swing_high, tr.last_swing_low
                tr.update(b)
                ev = 0
                for kind, new, old in (("H", tr.last_swing_high, bh),
                                        ("L", tr.last_swing_low, bl)):
                    if new is not None and new != old:
                        ev += 1
                        if last_conf is not None:
                            gaps.append(i - last_conf)
                        last_conf = i
                        if prev_val is not None and prev_kind != kind:
                            mags.append(abs(new - prev_val) / RS)
                        prev_val, prev_kind = new, kind
                n_swing.append(ev)

                bn = tr.last_swing_low is not None and b.close < tr.last_swing_low
                an = tr.last_swing_high is not None and b.close > tr.last_swing_high
                is_break.append(1 if ((bn and not below) or (an and not above)) else 0)
                below, above = bn, an

                gap_to_low.append((b.close - tr.last_swing_low) / RS
                                   if tr.last_swing_low is not None else None)

    if len(bars) < args.window * 8:
        print(f"only {len(bars)} bars — too few for a quartile split "
              f"(need >= {args.window*8}). Pass more files.", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== {len(paths)} file(s)  range_size={RS}  "
          f"confirm_bars={args.confirm_bars}  {len(bars)} bars ===")
    n_ev = sum(n_swing)
    print(f"swing confirmations: {n_ev}  "
          f"({len(bars)/n_ev:.1f} bars per swing on average)")

    if gaps:
        print(f"\ngap between confirmations (bars): "
              f"median={st.median(gaps):.0f} mean={st.mean(gaps):.2f} "
              f"p90={sorted(gaps)[9*len(gaps)//10]} max={max(gaps)}")
    if mags:
        ms = sorted(mags)
        print(f"swing amplitude (in range_size units): "
              f"median={st.median(ms):.2f} p10={ms[len(ms)//10]:.2f} "
              f"p90={ms[9*len(ms)//10]:.2f}")
        sub = sum(1 for m in ms if m < 1.0) / len(ms)
        print(f"  amplitude < 1 bar: {sub:.1%}  "
              f"(if ~0, range bars are already filtering noise and an "
              f"amplitude threshold would be redundant)")

    W = args.window
    rows = []
    for s in range(0, len(bars) - W, W):
        seg = bars[s:s + W]
        net = abs(seg[-1].close - seg[0].open) / RS
        path_len = sum(abs(b.close - b.open) for b in seg) / RS
        if path_len <= 0:
            continue
        eff = net / path_len
        sw = sum(n_swing[s:s + W])
        br = sum(is_break[s:s + W])
        gl = [g for g in gap_to_low[s:s + W] if g is not None]
        rows.append((eff, sw, br, st.median(gl) if gl else float("nan")))

    rows.sort()
    q = len(rows) // 4
    print(f"\n--- {len(rows)} windows of {W} bars, sorted by directional "
          f"efficiency ---")
    print(f"{'quartile':<22} {'effic':>6} {'swings/win':>11} "
          f"{'breaks/win':>11} {'gap to level':>13}")
    for name, ch in (("Q1 flattest", rows[:q]), ("Q2", rows[q:2*q]),
                      ("Q3", rows[2*q:3*q]), ("Q4 most trending", rows[3*q:])):
        if not ch:
            continue
        g = [x[3] for x in ch if x[3] == x[3]]
        print(f"{name:<22} {st.mean(x[0] for x in ch):>6.2f} "
              f"{st.mean(x[1] for x in ch):>11.2f} "
              f"{st.mean(x[2] for x in ch):>11.2f} "
              f"{(st.mean(g) if g else float('nan')):>13.2f}")

    q1sw = st.mean(x[1] for x in rows[:q])
    q4sw = st.mean(x[1] for x in rows[3*q:])
    print(f"\nchop/trend swing-density ratio: {q1sw/q4sw:.2f}x "
          f"(synthetic random walk gave 1.77x — if real data is close to "
          f"that, the effect is just random-walk geometry, not a market "
          f"regime worth filtering on)")


if __name__ == "__main__":
    main()
