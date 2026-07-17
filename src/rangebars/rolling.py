"""
Rolling (trailing-window) range-bar sizing — the fix for AUDIT item A4.

WHY THIS EXISTS
---------------
`config.yaml` carries a single `range_size` per market, calibrated once on
2026-06-29..07-02 BTC data. Measured 2026-07-10, the mrcvokka heuristic
(`0.30 x mean 1m range`) actually asks for a different size almost every day:

    Jun 29  14.09     Jul 03  11.65     Jul 07  15.27
    Jun 30  13.97     Jul 04   8.89     Jul 08  14.67
    Jul 01  16.11     Jul 05   9.43     Jul 09  14.00
    Jul 02  15.29     Jul 06  16.03

Nearly a 2x spread. Every out-of-sample run so far used the constant 15.3,
so on Jul 4-5 the backtest traded bars ~60-70% larger than the instrument's
own volatility called for. That confounds the OOS verdict: it measured "does
this work with a mis-sized bar", not "does this work on unseen days".

A constant is also not deployable. A live bot cannot know 2026-07-04's mean
1m range at 00:00:00 on 2026-07-04. Whatever the backtest does here has to be
something the bot can do too.

THE RULE (and the lookahead trap it avoids)
-------------------------------------------
For UTC day D, `range_size(D) = pct * mean_1m_range(D-1)`.

Day D-1 is *complete* before day D's first tick, so this is knowable at
midnight and carries no lookahead. The obvious-looking alternative —
calibrating on day D and trading day D — is precisely the leak the
out-of-sample split exists to catch: the bar size would encode the
volatility of the very session being traded.

`max_staleness_days` (default 1) makes the "previous day" strict. If D-1 is
missing from the data, day D gets NO size rather than silently inheriting one
from D-2 — a gap in the collector is not permission to reach further back.
Raise it deliberately (and knowingly) if you want that fallback.

DELIBERATE SIMPLIFICATIONS, named:
  - The window is exactly one calendar day, because that is the granularity
    the heuristic and the collector's files are both stated in. A trailing
    N-hour window would adapt faster and is a reasonable follow-up; it is not
    what mrcvokka describes.
  - Switching size at a UTC midnight is arbitrary relative to the market
    (crypto has no session close). It is, however, the same boundary the
    calibration is computed over, and any switch instant has to be chosen
    somehow. A bar in progress at the boundary is NOT retroactively resized
    — see Replay's handling: the new size takes effect for the bars that
    follow, and an in-progress bar that already exceeds the new size simply
    closes at the next tick, as it would have anyway.
"""
from __future__ import annotations

from typing import Iterable, Mapping

DAY_MS = 86_400_000


def utc_day(ts_ms: int) -> int:
    """UTC day index (days since epoch). Same bucket-index idiom as the 1m
    candle builder's `ts // 60000` and costs.py's `ts // HOUR_MS`."""
    return ts_ms // DAY_MS


def mean_1m_range_by_day(ticks: Iterable[tuple[float, int]]) -> dict[int, float]:
    """Mean 1-minute high-low range, grouped by UTC day.

    Computed from the tick stream itself rather than from filenames, so a
    file that straddles midnight (or a day split across files) still yields
    the right per-day numbers.

    A minute with trades at a single price has range 0.0 and IS included —
    this mirrors `calibrate.py`'s headline "mean over all non-empty minutes"
    reading of the heuristic. (calibrate.py also reports a mean over nonzero
    minutes for illiquid markets, where the two diverge; on BTC/ETH there are
    no zero-range minutes at all, so the distinction does not arise here. If
    this is ever pointed at XAU/HYPE, revisit — see calibrate.py's note.)
    """
    # (day, minute) -> [low, high]
    buckets: dict[tuple[int, int], list[float]] = {}
    for price, ts_ms in ticks:
        key = (ts_ms // DAY_MS, ts_ms // 60_000)
        b = buckets.get(key)
        if b is None:
            buckets[key] = [price, price]
        else:
            if price < b[0]:
                b[0] = price
            if price > b[1]:
                b[1] = price

    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for (day, _minute), (lo, hi) in buckets.items():
        sums[day] = sums.get(day, 0.0) + (hi - lo)
        counts[day] = counts.get(day, 0) + 1

    return {day: sums[day] / counts[day] for day in sums}


def rolling_range_sizes(mean_by_day: Mapping[int, float], pct: float = 0.30,
                        tick: float | None = None,
                        max_staleness_days: int = 1) -> dict[int, float]:
    """Map each day D to `pct * mean_1m_range(D')`, where D' is the most
    recent day with data such that `0 < D - D' <= max_staleness_days`.

    The earliest day in `mean_by_day` never appears in the result: nothing
    precedes it, so it has no legitimate size. Callers must decide what to do
    with it — the intended use is to feed it as a calibration-only warm-up
    day whose trades are discarded.

    `tick`, if given, rounds each size to a whole number of ticks (>= 1),
    matching `calibrate.py::_round_to_tick`.
    """
    if pct <= 0:
        raise ValueError(f"pct must be > 0, got {pct}")
    if max_staleness_days < 1:
        raise ValueError(
            f"max_staleness_days must be >= 1, got {max_staleness_days} "
            "(0 would mean calibrating a day on itself — the lookahead this "
            "module exists to prevent)"
        )
    if tick is not None and tick <= 0:
        raise ValueError(f"tick must be > 0 when given, got {tick}")

    days = sorted(mean_by_day)
    out: dict[int, float] = {}
    for day in days:
        source = None
        for back in range(1, max_staleness_days + 1):
            if (day - back) in mean_by_day:
                source = day - back
                break
        if source is None:
            continue
        size = pct * mean_by_day[source]
        if size <= 0:
            # A completely flat prior day would produce range_size=0, which
            # makes RangeBarBuilder close a bar on every tick. Refuse rather
            # than emit a size that silently destroys the run.
            continue
        if tick is not None:
            size = max(1, round(size / tick)) * tick
        out[day] = size
    return out


def schedule_from_ticks(ticks: Iterable[tuple[float, int]], pct: float = 0.30,
                        tick: float | None = None,
                        max_staleness_days: int = 1) -> dict[int, float]:
    """Convenience: one pass over ticks -> a day -> range_size schedule.

    NOTE this consumes the whole stream, so it needs a re-readable source
    (a list, or a fresh generator per pass). The backtest runner reads the
    tick files twice: once to build the schedule, once to replay. That is
    NOT lookahead — the schedule for day D only ever reads days < D — but it
    does mean the runner must not be handed a one-shot iterator.
    """
    return rolling_range_sizes(mean_1m_range_by_day(ticks), pct, tick,
                               max_staleness_days)
