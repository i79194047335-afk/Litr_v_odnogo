"""
Diagnostic tool: export a window of range bars + trades as JSON for visualization.

Read-only — subclasses WFStrategy to snapshot per-bar state without modifying
strategy.py.  Run with the same arguments as run_backtest.py plus --start-bar,
--n-bars, and --out.

Usage:
    python -m src.backtest.export_sample \\
        --files data/ticks/trades_1_20260703.jsonl \\
        --range-size 15.3 --tick-size 0.1 \\
        --exit-mode swing \\
        --start-bar 0 --n-bars 300 \\
        --out sample.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Iterator

from src.rangebars.calibrate import iter_price_ts
from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy, Trade


# ---------------------------------------------------------------------------
# Observed strategy — subclass, don't modify
# ---------------------------------------------------------------------------

class ObservedStrategy(WFStrategy):
    """Wraps WFStrategy to snapshot per-bar state after each bar close,
    without touching strategy.py. Snapshots only bars in
    [start_bar, start_bar + n_bars) by 0-indexed order of closure."""

    def __init__(self, *args, start_bar: int = 0, n_bars: int = 300,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.start_bar = start_bar
        self.n_bars = n_bars
        self._bar_index = -1
        self.snapshots: list[dict] = []

    def on_range_bar(self, bar) -> None:
        super().on_range_bar(bar)
        self._bar_index += 1
        if self.start_bar <= self._bar_index < self.start_bar + self.n_bars:
            b = self.bias()
            self.snapshots.append({
                "index": self._bar_index,
                "start_ts": bar.start_ts,
                "end_ts": bar.end_ts,
                "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close,
                "n_ticks": bar.n_ticks,
                "bias": b,
                "lines": dict(self._lines) if self._lines else None,
                "swing_low": self.swings.last_swing_low if self.swings else None,
                "swing_high": self.swings.last_swing_high if self.swings else None,
                "in_position": bool(self._pos),
                "side": self._side,
                "stop_price": self._stop_price,
            })


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_ticks(paths: list[Path]) -> Iterator[tuple[float, int]]:
    """Chain (price, ts) across files in the given order. Streaming — does
    not materialize the full tick list in memory."""
    return itertools.chain.from_iterable(iter_price_ts(p) for p in paths)


def _trade_to_dict(t: Trade) -> dict:
    return {
        "side": t.side,
        "tag": t.tag,
        "entry_price": t.entry_price,
        "entry_ts": t.entry_ts,
        "exit_price": t.exit_price,
        "exit_ts": t.exit_ts,
        "exit_reason": t.exit_reason,
        "r_multiple": t.r_multiple,
    }


def filter_trades(trades: list[Trade], snapshots: list[dict]) -> list[dict]:
    """Return trades whose entry_ts OR exit_ts falls within the snapshot
    window (inclusive).  A trade opened just before the window or closed
    just after it still shows up — its entry or exit marker is relevant
    context even if the other end is outside the window."""
    if not snapshots:
        return []
    t_min = snapshots[0]["start_ts"]
    t_max = snapshots[-1]["end_ts"]
    result = []
    for t in trades:
        if t_min <= t.entry_ts <= t_max or t_min <= t.exit_ts <= t_max:
            result.append(_trade_to_dict(t))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export a window of range bars + trades as JSON")
    ap.add_argument("--files", nargs="+", required=True,
                    help="JSONL tick files, IN CHRONOLOGICAL ORDER")
    ap.add_argument("--range-size", type=float, required=True)
    ap.add_argument("--tick-size", type=float, default=0.0)
    ap.add_argument("--exit-mode", choices=["bar", "swing"], default="bar",
                    help="part-2 reversal exit rule")
    ap.add_argument("--trailing", action="store_true", default=False)
    ap.add_argument("--swing-confirm-bars", type=int, default=2)
    ap.add_argument("--start-bar", type=int, default=0,
                    help="0-indexed bar to start snapshot window")
    ap.add_argument("--n-bars", type=int, default=300,
                    help="number of bars to snapshot")
    ap.add_argument("--out", type=str, default=None,
                    help="output JSON path (stdout if omitted)")
    # Passthrough to Replay / WFStrategy
    ap.add_argument("--ema-fast", type=int, default=15)
    ap.add_argument("--ema-slow", type=int, default=20)
    ap.add_argument("--keltner-period", type=int, default=35)
    ap.add_argument("--mult-inner", type=float, default=4.0)
    ap.add_argument("--mult-outer", type=float, default=8.0)
    ap.add_argument("--part-size", type=float, default=1.0)
    args = ap.parse_args()

    files = [Path(f) for f in args.files]
    for f in files:
        if not f.exists():
            print(f"ERROR: file not found: {f}", file=sys.stderr)
            sys.exit(2)

    replay = Replay(range_size=args.range_size, ema_fast=args.ema_fast,
                    ema_slow=args.ema_slow)
    engine = FillEngine(tick_size=args.tick_size)
    strategy = ObservedStrategy(
        replay, engine,
        keltner_period=args.keltner_period,
        mult_inner=args.mult_inner, mult_outer=args.mult_outer,
        part_size=args.part_size, trailing=args.trailing,
        exit_mode=args.exit_mode,
        swing_confirm_bars=args.swing_confirm_bars,
        start_bar=args.start_bar, n_bars=args.n_bars,
    )

    replay.run(load_ticks(files), strategy)

    output = {
        "meta": {
            "files": args.files,
            "range_size": args.range_size,
            "tick_size": args.tick_size,
            "exit_mode": args.exit_mode,
            "trailing": args.trailing,
            "swing_confirm_bars": args.swing_confirm_bars,
            "start_bar": args.start_bar,
            "n_bars": args.n_bars,
            "bars_exported": len(strategy.snapshots),
        },
        "bars": strategy.snapshots,
        "trades": filter_trades(strategy.trades, strategy.snapshots),
    }

    json_str = json.dumps(output, indent=None)
    if args.out:
        Path(args.out).write_text(json_str)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
