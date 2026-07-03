"""
Range-bar size calibration.

mrcvokka's heuristic (docs/kb_mrcvokka_diary.md §1): the range-bar size should
be ~30% of the average 1-minute candle range for the session. This script
derives that number PER MARKET from collected tick data, so `range_size` in
config.yaml is grounded in the instrument's real volatility rather than a
placeholder guess.

What it does:
  1. Read one or more JSONL tick files (live-collector schema; only `p` and
     `t` are needed, so v1 and v2 files both work).
  2. Bucket ticks into 1-minute candles by UTC minute (t_ms // 60000).
  3. For each non-empty minute, range = max(price) - min(price).
  4. Report the mean 1m range and the suggested range_size = pct * mean.

A JUDGMENT CALL, made visible not hidden:
  Minutes with a single trade (or several trades at one price) have range 0.
  Including them is the literal reading of "average 1m candle range" — a quiet
  minute genuinely had ~0 range. But on illiquid markets (XAU, HYPE) many
  such minutes drag the mean down, which would suggest a tiny range_size and
  produce far too many range bars. So this script reports BOTH:
      - mean over ALL non-empty minutes   (the headline / literal heuristic)
      - mean over minutes with range > 0  (ignores dead minutes)
      - the count and fraction of zero-range minutes
  On a liquid market the two means nearly agree and you use the headline. On
  an illiquid one they diverge and YOU decide — the script won't pick for you.
  It prints a note when the gap is large enough to matter.

Usage:
    # pool all live days on disk for one market
    python -m src.rangebars.calibrate --market 1 --data-dir data/ticks

    # or point at explicit files
    python -m src.rangebars.calibrate --files data/ticks/trades_1_20260702.jsonl

Options:
    --pct FLOAT     fraction of mean 1m range to suggest (default 0.30)
    --tick FLOAT    instrument tick size; if given, the suggestion is also
                    rounded to a whole number of ticks and shown that way
                    (e.g. --tick 0.1 for BTC). Optional.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Iterator


# --- pure computation (unit-tested) -----------------------------------------

def one_minute_ranges(ticks: Iterable[tuple[float, int]]) -> list[float]:
    """Bucket (price, ts_ms) ticks into UTC minutes; return per-minute ranges.

    A minute's range is max(price) - min(price) over the trades in that
    minute. Empty minutes produce no entry (only minutes that actually had
    trades appear). Order of the returned list is not meaningful.
    """
    buckets: dict[int, list[float]] = {}  # minute -> [lo, hi]
    for price, ts_ms in ticks:
        minute = ts_ms // 60000
        b = buckets.get(minute)
        if b is None:
            buckets[minute] = [price, price]
        else:
            if price < b[0]:
                b[0] = price
            if price > b[1]:
                b[1] = price
    return [hi - lo for lo, hi in buckets.values()]


def calibrate(ranges: list[float], pct: float) -> dict:
    """Compute calibration stats from a list of per-minute ranges.

    Returns a dict with the headline suggestion and the diagnostics needed
    to judge whether the zero-range-minute question matters for this market.
    """
    n = len(ranges)
    if n == 0:
        raise ValueError("no minute ranges — empty or unreadable input")

    nonzero = [r for r in ranges if r > 0]
    n_zero = n - len(nonzero)

    mean_all = sum(ranges) / n
    mean_nonzero = (sum(nonzero) / len(nonzero)) if nonzero else 0.0

    srt = sorted(ranges)
    mid = n // 2
    median = srt[mid] if n % 2 == 1 else (srt[mid - 1] + srt[mid]) / 2

    return {
        "n_minutes": n,
        "n_zero_minutes": n_zero,
        "zero_fraction": n_zero / n,
        "mean_all": mean_all,
        "mean_nonzero": mean_nonzero,
        "median": median,
        "pct": pct,
        "suggested_from_mean_all": pct * mean_all,
        "suggested_from_mean_nonzero": pct * mean_nonzero,
    }


# --- IO ---------------------------------------------------------------------

def _iter_price_ts(path: Path) -> Iterator[tuple[float, int]]:
    """Stream (price, ts_ms) from one JSONL file. Skips blank/bad lines."""
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


def _resolve_files(args) -> list[Path]:
    if args.files:
        return [Path(f) for f in args.files]
    if args.market is None:
        print("ERROR: provide either --files or --market", file=sys.stderr)
        sys.exit(2)
    data_dir = Path(args.data_dir)
    files = sorted(data_dir.glob(f"trades_{args.market}_*.jsonl"))
    if not files:
        print(f"ERROR: no files match trades_{args.market}_*.jsonl in {data_dir}",
              file=sys.stderr)
        sys.exit(2)
    return files


def _round_to_tick(value: float, tick: float) -> tuple[float, int]:
    """Round `value` to the nearest whole number of ticks (>= 1 tick)."""
    n_ticks = max(1, round(value / tick))
    return n_ticks * tick, n_ticks


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibrate range-bar size from ticks")
    ap.add_argument("--market", type=int, help="market_id (globs data-dir)")
    ap.add_argument("--data-dir", default="data/ticks")
    ap.add_argument("--files", nargs="+", help="explicit JSONL file paths")
    ap.add_argument("--pct", type=float, default=0.30,
                    help="fraction of mean 1m range (default 0.30)")
    ap.add_argument("--tick", type=float, default=None,
                    help="instrument tick size; round suggestion to whole ticks")
    args = ap.parse_args()

    files = _resolve_files(args)
    print(f"reading {len(files)} file(s):")
    for f in files:
        print(f"  {f}")

    all_ranges: list[float] = []
    for f in files:
        if not f.exists():
            print(f"  WARNING: not found, skipping: {f}", file=sys.stderr)
            continue
        all_ranges.extend(one_minute_ranges(_iter_price_ts(f)))

    stats = calibrate(all_ranges, args.pct)

    print()
    print(f"minutes with trades : {stats['n_minutes']:,}")
    print(f"  zero-range minutes: {stats['n_zero_minutes']:,} "
          f"({100 * stats['zero_fraction']:.1f}%)")
    print(f"mean 1m range (all)    : {stats['mean_all']:.6f}")
    print(f"mean 1m range (nonzero): {stats['mean_nonzero']:.6f}")
    print(f"median 1m range        : {stats['median']:.6f}")
    print()
    print(f"pct = {stats['pct']}")
    print(f"suggested range_size (from mean-all)    : "
          f"{stats['suggested_from_mean_all']:.6f}")
    print(f"suggested range_size (from mean-nonzero): "
          f"{stats['suggested_from_mean_nonzero']:.6f}")

    if args.tick is not None:
        rounded, n = _round_to_tick(stats["suggested_from_mean_all"], args.tick)
        print()
        print(f"rounded to tick {args.tick}: {rounded:.6f}  ({n} ticks)")

    # note when the zero-minute question actually matters
    ma, mn = stats["mean_all"], stats["mean_nonzero"]
    if mn > 0 and (mn - ma) / mn > 0.15:
        print()
        print("NOTE: mean-all is >15% below mean-nonzero — this market has")
        print("      enough dead minutes that the choice matters. Look at the")
        print("      zero-range fraction above and decide which mean to use")
        print("      before writing range_size to config.")


if __name__ == "__main__":
    main()
