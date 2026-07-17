"""
Rolling-vs-fixed range_size comparison — the measurement AUDIT item A4 asks for.

Runs the SAME strategy, the same days, the same costs, twice:

  FIXED   range_size = 15.3 everywhere (what every prior result used)
  ROLLING range_size(D) = 0.30 * mean_1m_range(D-1), switched at UTC midnight

and reports pooled and per-day results side by side.

WHY A SEED DAY
--------------
Day D is sized from day D-1, so the first day of any stream has no legitimate
size. This script therefore feeds ONE EXTRA LEADING DAY (the seed day), runs it
on the fixed 15.3 in both variants, and EXCLUDES its trades from every number
below. Both variants see an identical warm-up, so the comparison stays
apples-to-apples.

The per-day breakdown is the second half of A4's fix: a pooled average hides a
regime shift, dispersion across days shows it.

Usage:
    python -m src.backtest.rolling_variant
"""
from __future__ import annotations

import itertools
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from src.rangebars.calibrate import iter_price_ts
from src.rangebars.rolling import schedule_from_ticks, utc_day
from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import WFStrategy
from src.backtest.costs import apply_costs_to_trades
# Same aggregation the headline metrics use — imported rather than re-derived
# so a per-day number here can never drift from a pooled number there.
from src.backtest.metrics import _bps, _stat_block, _win_rate, _profit_factor

BTC_DIR = Path("data/ticks")
TICK_SIZE = 0.1
FIXED_RANGE_SIZE = 15.3
CALIB_PCT = 0.30

SEED_DAY = "20260702"                       # sized by nothing; trades discarded
TRADED_DAYS = ["20260703", "20260704", "20260705",
               "20260706", "20260707", "20260708", "20260709"]


def btc_files(days: list[str]) -> list[Path]:
    files = [BTC_DIR / f"trades_1_{d}.jsonl" for d in days]
    missing = [f for f in files if not f.exists()]
    if missing:
        print(f"ERROR: missing files: {missing}", file=sys.stderr)
        sys.exit(2)
    return files


def day_index(yyyymmdd: str) -> int:
    d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    return (d - date(1970, 1, 1)).days


def load(files: list[Path]):
    return itertools.chain.from_iterable(iter_price_ts(p) for p in files)


def _fmt(x, pct=False, nd=4):
    if x is None:
        return "n/a"
    if x == float("inf"):
        return "inf"
    return f"{x:.2%}" if pct else f"{x:.{nd}f}"


def run_once(files: list[Path], schedule, seed_day_idx: int) -> dict:
    """One full replay. Returns per-trade rows keyed by exit day, plus the
    switches the schedule actually applied."""
    replay = Replay(range_size=FIXED_RANGE_SIZE, range_size_schedule=schedule)
    engine = FillEngine(tick_size=TICK_SIZE)
    strategy = WFStrategy(replay, engine, exit_mode="swing", trailing=False)
    replay.run(load(files), strategy)

    breakdowns = apply_costs_to_trades(strategy.trades)
    by_day: dict[int, list[float]] = defaultdict(list)
    for t, cb in zip(strategy.trades, breakdowns):
        d = utc_day(t.exit_ts)
        if d <= seed_day_idx:
            continue                      # seed-day trades are not measured
        by_day[d].append(_bps(cb.net_pnl, t.size, t.entry_price))

    return {
        "by_day": by_day,
        "n_bars": len(replay.bars),
        "n_ticks": replay.n_ticks,
        "switches": replay.range_size_changes,
        "n_sessions": strategy.n_sessions,
    }


def report(label: str, res: dict) -> None:
    all_bps = [v for day in sorted(res["by_day"]) for v in res["by_day"][day]]
    sb = _stat_block(all_bps)
    print(f"\n--- {label} ---")
    print(f"bars={res['n_bars']:,}  sessions={res['n_sessions']:,}  "
          f"trades(measured)={len(all_bps):,}")
    print(f"pooled net bps expectancy = {_fmt(sb.expectancy)}   "
          f"t = {_fmt(sb.t_stat, nd=2)}   "
          f"win = {_fmt(_win_rate(all_bps), pct=True)}   "
          f"PF = {_fmt(_profit_factor(all_bps), nd=2)}")
    print("  per exit day:")
    print(f"    {'day':10} {'n':>7} {'mean bps':>10} {'t':>7} {'win%':>8}")
    for d in sorted(res["by_day"]):
        rows = res["by_day"][d]
        s = _stat_block(rows)
        iso = date.fromordinal(date(1970, 1, 1).toordinal() + d).isoformat()
        print(f"    {iso:10} {len(rows):>7,} {_fmt(s.expectancy, nd=3):>10} "
              f"{_fmt(s.t_stat, nd=2):>7} {_fmt(_win_rate(rows), pct=True):>8}")


def main() -> None:
    files = btc_files([SEED_DAY] + TRADED_DAYS)
    seed_idx = day_index(SEED_DAY)

    print("=" * 74)
    print("  ROLLING vs FIXED range_size — BTC, exit_mode=swing, trailing=False")
    print(f"  seed day {SEED_DAY} (trades discarded), measured {TRADED_DAYS[0]}"
          f"..{TRADED_DAYS[-1]}")
    print("=" * 74)

    schedule = schedule_from_ticks(load(files), pct=CALIB_PCT, tick=TICK_SIZE)
    print(f"\nschedule ({len(schedule)} days, each from the PRIOR day):")
    for d in sorted(schedule):
        iso = date.fromordinal(date(1970, 1, 1).toordinal() + d).isoformat()
        print(f"  {iso}  range_size = {schedule[d]:.1f}")

    fixed = run_once(files, None, seed_idx)
    rolling = run_once(files, schedule, seed_idx)

    report(f"FIXED  range_size={FIXED_RANGE_SIZE}", fixed)
    report("ROLLING range_size (prior-day calibrated)", rolling)

    print(f"\nrolling applied {len(rolling['switches'])} size switch(es).")
    print("\nNOTE: costs are zero (Lighter maker=taker=0). fill_probability=1.0")
    print("      and slippage_ticks=0 — this isolates the sizing change alone.")


if __name__ == "__main__":
    main()
