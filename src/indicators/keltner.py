"""
Keltner channel — NinjaTrader variant, as used by Beggs/YTC.

    centerline = SMA(close, period)
    band(mult) = centerline ± mult * SMA(High - Low, period)

The strategy needs TWO channels on the same range-bar series:
    Keltner(35, mult=4)  → inner lines
    Keltner(35, mult=8)  → outer lines
(see docs/ytc_scalper_skeleton.md §2.3)

Both channels share the SAME centerline (SMA of close) and the SAME
SMA(High-Low). Only the multiplier differs. So we compute the two rolling
SMAs ONCE in a shared core, and expose `.band(mult)` for each multiplier.
This avoids duplicating the rolling computation and guarantees the two
channels can never drift out of sync.

Rolling SMA is done with a fixed-length deque, recomputed by sum() each bar.
At period=35 that's trivially cheap and avoids the numerical drift that
running-sum accumulators develop over millions of bars. If profiling ever
shows this matters (it won't at 35), switch to a running sum with periodic
re-baselining — but not before.

WARM-UP: `.centerline` and `.band()` return None until `period` bars have
been fed. No half-populated window — the SMA is only defined once full.
"""
from __future__ import annotations

from collections import deque

from src.rangebars.builder import RangeBar


class KeltnerCore:
    """Shared rolling core for one or more Keltner channels on one series.

    Feed range bars via `update(bar)`. Then read `.centerline` and call
    `.band(mult)` for whichever multiplier(s) you need.
    """

    __slots__ = ("period", "_closes", "_ranges")

    def __init__(self, period: int):
        if period < 1:
            raise ValueError(f"Keltner period must be >= 1, got {period}")
        self.period = period
        self._closes: deque[float] = deque(maxlen=period)
        self._ranges: deque[float] = deque(maxlen=period)  # High - Low per bar

    def update(self, bar: RangeBar) -> None:
        """Feed one range bar into the rolling windows."""
        self._closes.append(float(bar.close))
        self._ranges.append(float(bar.high) - float(bar.low))

    @property
    def ready(self) -> bool:
        """True once the window is full (`period` bars seen)."""
        return len(self._closes) == self.period

    @property
    def centerline(self) -> float | None:
        """SMA(close, period), or None during warm-up."""
        if not self.ready:
            return None
        return sum(self._closes) / self.period

    @property
    def _range_sma(self) -> float | None:
        """SMA(High-Low, period), or None during warm-up."""
        if not self.ready:
            return None
        return sum(self._ranges) / self.period

    def band(self, mult: float) -> tuple[float, float] | None:
        """Return (upper, lower) for the given multiplier, or None in warm-up.

            upper = centerline + mult * SMA(High-Low)
            lower = centerline - mult * SMA(High-Low)
        """
        c = self.centerline
        r = self._range_sma
        if c is None or r is None:
            return None
        off = mult * r
        return (c + off, c - off)


class KeltnerChannels:
    """Convenience wrapper: the two channels the strategy actually uses.

    Wraps a single KeltnerCore and surfaces the inner (mult=4) and outer
    (mult=8) bands. Multipliers are injected (from config), not hardcoded.
    """

    __slots__ = ("_core", "mult_inner", "mult_outer")

    def __init__(self, period: int = 35, mult_inner: float = 4, mult_outer: float = 8):
        self._core = KeltnerCore(period)
        self.mult_inner = mult_inner
        self.mult_outer = mult_outer

    def update(self, bar: RangeBar) -> None:
        self._core.update(bar)

    @property
    def ready(self) -> bool:
        return self._core.ready

    @property
    def centerline(self) -> float | None:
        return self._core.centerline

    @property
    def inner(self) -> tuple[float, float] | None:
        """(upper, lower) for the inner channel (mult_inner)."""
        return self._core.band(self.mult_inner)

    @property
    def outer(self) -> tuple[float, float] | None:
        """(upper, lower) for the outer channel (mult_outer)."""
        return self._core.band(self.mult_outer)
