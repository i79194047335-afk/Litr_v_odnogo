"""
Slice 1 of the backtester: the replay harness.

ONE pass over the tick stream builds TWO series in parallel:
  - range bars   (via src.rangebars.builder.RangeBarBuilder) — the scalping
    chart, Keltner zones live here
  - 1-minute candles (built here) — the bias chart, EMA(15)/EMA(20) live here

There is NO trading logic in this file. The harness's only job is to deliver
events to a strategy object in the correct order with a hard guarantee:

    LOOKAHEAD DISCIPLINE
    ---------------------
    When the strategy is called for an event at tick timestamp T, every piece
    of state it can see was computed ONLY from ticks with timestamp <= T.

    The subtle case is the 1-minute candle. A candle for minute M is only
    KNOWN to be finished when the first tick of a minute > M arrives — there
    is no clock in tick data, the next trade IS the clock. So:
      * the candle for minute M closes (and EMAs update) at the timestamp of
        the first tick belonging to a later minute;
      * before that tick, the strategy sees the EMAs as of minute M-1, even
        if minute M is "really" over in wall-clock terms.
    This mirrors what a live process consuming the same WS stream would know,
    which is exactly the point.

    Range bars have the same property by construction: RangeBarBuilder only
    yields a bar once a tick has traversed the range.

Event order within a single tick (matters when one tick does several things):
    1. minute rollover — if this tick starts a new minute, the previous
       candle closes first and EMAs update (that candle ended BEFORE this
       tick's minute began);
    2. the tick itself → strategy.on_tick(price, ts) — fill checks in later
       slices live here;
    3. any range bars the tick closed → strategy.on_range_bar(bar) — entry /
       signal decisions live here.
So a strategy reacting to a closed range bar already sees the freshest
legitimately-closed 1m state, and fill logic sees ticks before bar logic
reacts to them — the same order reality delivers them.

Strategy interface (duck-typed, all optional):
    on_tick(price: float, ts: int)            every tick, after minute roll
    on_minute_close(candle: MinuteCandle)     every closed 1m candle
    on_range_bar(bar: RangeBar)               every closed range bar

State exposed to strategies via the harness object:
    .ema_fast.value / .ema_slow.value   — EMAs over CLOSED 1m candle closes
    .last_closed_minute                 — the last closed MinuteCandle or None
    .bars                               — list of closed range bars so far

At the end of the stream the final 1m candle and range bar are still open —
they are NOT closed retroactively (`flush()` exists for inspection but marks
its output clearly). A live process wouldn't know the day ended either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from src.rangebars.builder import RangeBar, RangeBarBuilder
from src.rangebars.rolling import DAY_MS
from src.indicators.ema import EMA


@dataclass
class MinuteCandle:
    """One closed 1-minute candle. minute = ts_ms // 60000 (UTC)."""
    minute: int
    open: float
    high: float
    low: float
    close: float
    n_ticks: int

    @property
    def start_ts(self) -> int:
        return self.minute * 60000

    @property
    def end_ts(self) -> int:
        return self.minute * 60000 + 59999


class MinuteCandleBuilder:
    """Streaming 1m candle builder. A candle closes when a tick from a LATER
    minute arrives (the next trade is the clock — see module docstring)."""

    __slots__ = ("_minute", "_open", "_high", "_low", "_close", "_n")

    def __init__(self):
        self._minute: int | None = None

    def update(self, price: float, ts: int) -> MinuteCandle | None:
        """Feed one tick. Returns the previous candle if this tick closed it.

        Ticks are assumed non-decreasing in time (the collector writes them
        in arrival order). A tick for an EARLIER minute than the current one
        would indicate corrupted input and raises.
        """
        minute = ts // 60000
        closed: MinuteCandle | None = None

        if self._minute is None:
            self._minute = minute
            self._open = self._high = self._low = self._close = price
            self._n = 1
            return None

        if minute < self._minute:
            raise ValueError(
                f"tick time went backwards: minute {minute} < current {self._minute}"
            )

        if minute > self._minute:
            closed = MinuteCandle(
                self._minute, self._open, self._high, self._low, self._close, self._n
            )
            self._minute = minute
            self._open = self._high = self._low = self._close = price
            self._n = 1
            return closed

        # same minute
        self._high = max(self._high, price)
        self._low = min(self._low, price)
        self._close = price
        self._n += 1
        return None

    def flush(self) -> MinuteCandle | None:
        """Return the still-open candle (end of stream). Does NOT reset.
        Marked clearly: this candle never 'closed' in-stream — do not feed
        it to anything that assumes closed-candle semantics."""
        if self._minute is None:
            return None
        return MinuteCandle(
            self._minute, self._open, self._high, self._low, self._close, self._n
        )


class Replay:
    """The harness. Construct with parameters, then run(ticks, strategy)."""

    def __init__(self, range_size: float, ema_fast: int = 15, ema_slow: int = 20,
                 range_size_schedule: "Mapping[int, float] | None" = None):
        """`range_size_schedule` (optional, AUDIT item A4): UTC-day-index ->
        range_size, as produced by src.rangebars.rolling. When given, the
        range-bar size is switched at each UTC midnight for which the schedule
        has an entry; days absent from the schedule keep whatever size was
        last active, starting from `range_size` itself.

        The schedule must be built ONLY from days strictly before the day it
        sizes (rolling.py enforces this) — otherwise this parameter becomes a
        lookahead channel straight into the bar series.

        A bar in progress across the boundary is not retroactively resized:
        the new size governs the very next `while` check, so a bar that
        already spans more than the new size closes on the next tick. The
        default (schedule=None) leaves behaviour byte-identical to before.
        """
        self.range_builder = RangeBarBuilder(range_size=range_size)
        self.minute_builder = MinuteCandleBuilder()
        self.ema_fast = EMA(ema_fast)
        self.ema_slow = EMA(ema_slow)
        self.last_closed_minute: MinuteCandle | None = None
        self.bars: list[RangeBar] = []
        self.n_ticks = 0
        self.range_size_schedule = range_size_schedule
        self._cur_day: int | None = None
        # (day_index, new_size) for every switch actually applied — reporting
        # and, more importantly, evidence that the schedule did something.
        self.range_size_changes: list[tuple[int, float]] = []

    def _maybe_switch_range_size(self, ts: int) -> None:
        if self.range_size_schedule is None:
            return
        day = ts // DAY_MS
        if day == self._cur_day:
            return
        self._cur_day = day
        size = self.range_size_schedule.get(day)
        if size is not None and size != self.range_builder.range_size:
            self.range_builder.range_size = size
            self.range_size_changes.append((day, size))

    def run(self, ticks: Iterable[tuple[float, int]], strategy=None) -> None:
        """One pass. `ticks` is an iterable of (price, ts_ms), time-ordered."""
        on_tick = getattr(strategy, "on_tick", None)
        on_minute = getattr(strategy, "on_minute_close", None)
        on_bar = getattr(strategy, "on_range_bar", None)

        for price, ts in ticks:
            # 0) UTC-day rollover: swap in the size calibrated on the PRIOR
            #    day before this tick can contribute to any bar.
            self._maybe_switch_range_size(ts)

            # 1) minute rollover BEFORE anything else sees this tick:
            #    the closed candle ended strictly before this tick's minute.
            closed = self.minute_builder.update(price, ts)
            if closed is not None:
                self.last_closed_minute = closed
                self.ema_fast.update(closed.close)
                self.ema_slow.update(closed.close)
                if on_minute:
                    on_minute(closed)

            # 2) the tick itself (fill checks in later slices live here)
            self.n_ticks += 1
            if on_tick:
                on_tick(price, ts)

            # 3) range bars closed by this tick
            for bar in self.range_builder.update(price, ts):
                self.bars.append(bar)
                if on_bar:
                    on_bar(bar)
