"""
Diagnostic tool: bias-regime empirical audit.

Runs the standard pipeline (Replay + FillEngine + WFStrategy with
exit_mode="swing", trailing=False) across all 8 real BTC days and reports:

1. Bias regime-length distribution — per file/day, separately for calibration
   (June 29 - July 2) and out-of-sample (July 3-6).
2. Bias age at entry — for every trade, how many bars the current bias regime
   had existed at entry.

Read-only — subclasses WFStrategy to track bias history without modifying
strategy.py.  Same non-invasive pattern as export_sample.py.

Usage (hardcoded file paths, just run):
    python -m src.backtest.bias_audit
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from src.rangebars.calibrate import iter_price_ts
from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy

# ---------------------------------------------------------------------------
# BTC data: exchange 1, range_size 15.3 (from config.yaml)
# ---------------------------------------------------------------------------

BTC_DIR = Path("data/ticks")
RANGE_SIZE = 15.3
TICK_SIZE = 0.1  # from run_backtest.py defaults / prior runs

CALIBRATION_DAYS = ["20260629", "20260630", "20260701", "20260702"]
OOS_DAYS = ["20260703", "20260704", "20260705", "20260706"]

CALIBRATION_LABEL = "Calibration (Jun 29 – Jul 2)"
OOS_LABEL = "Out-of-sample (Jul 3–6)"


def btc_files(days: list[str]) -> list[Path]:
    """Return existing BTC tick files for the given day strings."""
    files = [BTC_DIR / f"trades_1_{d}.jsonl" for d in days]
    missing = [f for f in files if not f.exists()]
    if missing:
        print(f"ERROR: missing files: {missing}", file=sys.stderr)
        sys.exit(2)
    return files


# ---------------------------------------------------------------------------
# Audit strategy — subclass, don't modify strategy.py
# ---------------------------------------------------------------------------

class AuditStrategy(WFStrategy):
    """Wraps WFStrategy to record bias history and entry bar-indices,
    without touching strategy.py."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bar_index = -1
        # (bar_index, bias_value) per bar close — bias_value is "bull"|"bear"|None
        self.bias_history: list[tuple[int, str | None]] = []
        # (ts, bar_index) for every entry fill (part1 or part2)
        self.entry_events: list[tuple[int, int]] = []

    def on_range_bar(self, bar) -> None:
        super().on_range_bar(bar)
        self._bar_index += 1
        self.bias_history.append((self._bar_index, self.bias()))

    def _handle_fill(self, f) -> None:
        super()._handle_fill(f)
        if f.tag in ("part1", "part2"):
            self.entry_events.append((f.ts, self._bar_index))


# ---------------------------------------------------------------------------
# Pure analysis — operates on recorded data, no strategy dependency
# ---------------------------------------------------------------------------

def extract_regimes(
    bias_history: list[tuple[int, str | None]],
) -> list[tuple[str | None, int, int]]:
    """Return list of (bias_value, start_bar, length_in_bars) from a bias
    history.  A new regime begins every time bias() changes value (including
    transitions to/from None)."""
    if not bias_history:
        return []
    regimes: list[tuple[str | None, int, int]] = []
    current_bias = bias_history[0][1]
    start_bar = bias_history[0][0]
    length = 1
    for bar_idx, bias_val in bias_history[1:]:
        if bias_val == current_bias:
            length += 1
        else:
            regimes.append((current_bias, start_bar, length))
            current_bias = bias_val
            start_bar = bar_idx
            length = 1
    regimes.append((current_bias, start_bar, length))
    return regimes


def regime_stats(regimes: list[tuple[str | None, int, int]]) -> dict:
    """Compute median, min, max, histogram per bias value from a regime list."""
    from statistics import median

    by_bias: dict[str | None, list[int]] = defaultdict(list)
    for bias_val, _start, length in regimes:
        by_bias[bias_val].append(length)

    result: dict = {}
    for bias_val in ["bull", "bear", None]:
        lengths = by_bias.get(bias_val, [])
        if not lengths:
            result[bias_val] = {"n": 0, "median": None, "min": None,
                                "max": None, "<5": 0, "5-20": 0, ">20": 0}
        else:
            result[bias_val] = {
                "n": len(lengths),
                "median": median(lengths),
                "min": min(lengths),
                "max": max(lengths),
                "<5": sum(1 for l in lengths if l < 5),
                "5-20": sum(1 for l in lengths if 5 <= l <= 20),
                ">20": sum(1 for l in lengths if l > 20),
            }
    return result


def compute_ages_at_entry(
    trades: list,
    bias_history: list[tuple[int, str | None]],
    entry_events: list[tuple[int, int]],
    regimes: list[tuple[str | None, int, int]],
) -> list[int]:
    """Return list of bias-regime ages (in bars) at entry, one per trade.

    For each trade, finds its entry_ts among recorded entry_events to get the
    bar_index at entry, then looks backward in bias_history to count how many
    consecutive bars had the same bias — i.e. how long the current regime had
    existed at entry.

    Trades that can't be matched (should not happen in practice) are skipped
    with a warning."""
    # Build lookup: entry_ts -> bar_index at entry
    ts_to_bar: dict[int, int] = {}
    for ts, bar_idx in entry_events:
        ts_to_bar[ts] = bar_idx  # last write wins if same ts (both parts)

    # Build regime lookup: bar_index -> regime start_bar for quick age calc
    # For each regime (bias, start, length), all bars [start, start+length)
    # belong to that regime.
    bar_to_regime_start: dict[int, int] = {}
    for bias_val, start, length in regimes:
        if bias_val is not None:  # only non-None regimes can have entries
            for bi in range(start, start + length):
                bar_to_regime_start[bi] = start

    ages: list[int] = []
    for t in trades:
        bar_idx = ts_to_bar.get(t.entry_ts)
        if bar_idx is None:
            print(f"  WARNING: no entry event found for trade entry_ts={t.entry_ts}",
                  file=sys.stderr)
            continue
        regime_start = bar_to_regime_start.get(bar_idx)
        if regime_start is None:
            # Bias was None at entry — should not happen (entries only placed
            # when bias is set), but be defensive.
            continue
        age = bar_idx - regime_start + 1
        ages.append(age)

    return ages


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _print_regime_block(label: str, stats: dict) -> None:
    print(f"\n  {label}")
    print(f"  {'─' * len(label)}")
    for bias_val, display in [("bull", "BULL"), ("bear", "BEAR"), (None, "None")]:
        s = stats[bias_val]
        if s["n"] == 0:
            print(f"    {display}: 0 regimes")
        else:
            print(f"    {display}: {s['n']} regimes  "
                  f"median={s['median']:.0f}  min={s['min']}  max={s['max']}  "
                  f"<5:{s['<5']}  5-20:{s['5-20']}  >20:{s['>20']}")


def _print_age_block(ages: list[int]) -> None:
    if not ages:
        print("  (no trades with matched entry events)")
        return
    from statistics import median
    print(f"  n_trades={len(ages)}  "
          f"median={median(ages):.0f} bars  "
          f"min={min(ages)}  max={max(ages)}")
    # simple histogram
    lt5 = sum(1 for a in ages if a < 5)
    r5_20 = sum(1 for a in ages if 5 <= a <= 20)
    gt20 = sum(1 for a in ages if a > 20)
    print(f"  <5 bars: {lt5}  5-20 bars: {r5_20}  >20 bars: {gt20}")


# ---------------------------------------------------------------------------
# Main — run the audit
# ---------------------------------------------------------------------------

def run_audit_for_files(files: list[Path], label: str) -> None:
    """Run the pipeline across the given files and print the bias audit report."""
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")

    replay = Replay(range_size=RANGE_SIZE)
    engine = FillEngine(tick_size=TICK_SIZE)
    strategy = AuditStrategy(
        replay, engine,
        exit_mode="swing", trailing=False,
    )

    # Streaming load across files
    import itertools
    ticks = itertools.chain.from_iterable(iter_price_ts(p) for p in files)
    replay.run(ticks, strategy)

    n_bars = len(strategy.bias_history)
    n_ticks = replay.n_ticks
    n_trades = len(strategy.trades)
    n_sessions = strategy.n_sessions
    print(f"  ticks={n_ticks}  bars={n_bars}  trades={n_trades}  sessions={n_sessions}")

    # --- per-file regime analysis ---
    # Run each file separately for per-day regime stats
    for f in files:
        _run_single_file_audit(f)

    # --- pooled regime stats (all files in this set) ---
    regimes = extract_regimes(strategy.bias_history)
    stats = regime_stats(regimes)
    print(f"\n  ── Pooled regime-length distribution ({len(regimes)} regimes total) ──")
    _print_regime_block("Pooled", stats)

    # --- bias age at entry (pooled across all files in this set) ---
    print(f"\n  ── Bias age at entry (pooled across {len(files)} days) ──")
    ages = compute_ages_at_entry(
        strategy.trades, strategy.bias_history,
        strategy.entry_events, regimes,
    )
    _print_age_block(ages)


def _run_single_file_audit(filepath: Path) -> None:
    """Run one file through the pipeline and print per-day regime stats."""
    replay = Replay(range_size=RANGE_SIZE)
    engine = FillEngine(tick_size=TICK_SIZE)
    strategy = AuditStrategy(
        replay, engine,
        exit_mode="swing", trailing=False,
    )
    replay.run(iter_price_ts(filepath), strategy)

    regimes = extract_regimes(strategy.bias_history)
    stats = regime_stats(regimes)
    day_label = filepath.stem.replace("trades_1_", "")
    print(f"\n  ── {day_label} ({len(regimes)} regimes, {len(strategy.bias_history)} bars) ──")
    _print_regime_block("Per-day", stats)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the bias audit on all 8 real BTC days."""
    print("=" * 70)
    print("  BIAS-REGIME EMPIRICAL AUDIT")
    print("  exit_mode=swing  trailing=False  range_size=15.3  exchange=1 (BTC)")
    print("=" * 70)

    # Part 1: calibration days
    cal_files = btc_files(CALIBRATION_DAYS)
    run_audit_for_files(cal_files, CALIBRATION_LABEL)

    # Part 2: out-of-sample days
    oos_files = btc_files(OOS_DAYS)
    run_audit_for_files(oos_files, OOS_LABEL)

    print()


if __name__ == "__main__":
    main()
