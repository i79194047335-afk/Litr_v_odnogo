"""
Slow Stochastic oscillator — the mrcvokka "стохастик 3/2/3" trigger.

CONVENTION (an assumption — surfaced here, not buried):
    "3/2/3" is read as the NinjaTrader/MT slow-stochastic triple:
        %K period   = 3   (look-back for the raw stochastic)
        slowing     = 2   (SMA smoothing applied to raw %K → "slow %K")
        %D period   = 3   (SMA of slow %K → the signal line)
    If the diary turns out to mean a different ordering, this is the one
    place to change it — and the tests below pin the arithmetic so a change
    is visible, not silent.

Computation, per bar:
    HH        = highest High over the last `k_period` bars
    LL        = lowest  Low  over the last `k_period` bars
    raw_%K    = 100 * (close - LL) / (HH - LL)
    slow_%K   = SMA(raw_%K, slowing)
    %D        = SMA(slow_%K, d_period)

FLAT-RANGE EDGE CASE:
    When HH == LL over the window (no movement — possible on quiet range
    bars), (HH - LL) is zero and raw_%K is undefined. We set raw_%K = 50.0
    (neutral) in that case. This is a deliberate choice: 50 means "neither
    overbought nor oversold", which is the honest reading of a flat window.
    Carrying forward the previous value would also be defensible; 50 is
    simpler and testable. Documented so it isn't mistaken for signal.

WARM-UP:
    raw_%K needs `k_period` bars.
    slow_%K needs a further `slowing - 1` raw values.
    %D      needs a further `d_period - 1` slow values.
    `.k` and `.d` return None until each is fully defined.
"""
from __future__ import annotations

from collections import deque

from src.rangebars.builder import RangeBar


class Stochastic:
    """Incremental slow stochastic. Feed range bars; read `.k` and `.d`."""

    __slots__ = (
        "k_period", "slowing", "d_period",
        "_highs", "_lows",
        "_raw_k", "_slow_k",
        "_k", "_d",
    )

    def __init__(self, k_period: int = 3, slowing: int = 2, d_period: int = 3):
        for name, v in (("k_period", k_period), ("slowing", slowing), ("d_period", d_period)):
            if v < 1:
                raise ValueError(f"Stochastic {name} must be >= 1, got {v}")
        self.k_period = k_period
        self.slowing = slowing
        self.d_period = d_period
        # rolling windows of highs/lows for the raw %K look-back
        self._highs: deque[float] = deque(maxlen=k_period)
        self._lows: deque[float] = deque(maxlen=k_period)
        # rolling windows for the two smoothing SMAs
        self._raw_k: deque[float] = deque(maxlen=slowing)
        self._slow_k: deque[float] = deque(maxlen=d_period)
        self._k: float | None = None
        self._d: float | None = None

    def update(self, bar: RangeBar) -> tuple[float | None, float | None]:
        """Feed one range bar. Returns (slow_%K, %D), either possibly None."""
        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))

        # raw %K needs a full look-back window
        if len(self._highs) < self.k_period:
            return self._k, self._d

        hh = max(self._highs)
        ll = min(self._lows)
        if hh == ll:
            raw = 50.0  # flat window — neutral (see module docstring)
        else:
            raw = 100.0 * (float(bar.close) - ll) / (hh - ll)
        self._raw_k.append(raw)

        # slow %K = SMA(raw %K, slowing)
        if len(self._raw_k) < self.slowing:
            return self._k, self._d
        self._k = sum(self._raw_k) / self.slowing
        self._slow_k.append(self._k)

        # %D = SMA(slow %K, d_period)
        if len(self._slow_k) < self.d_period:
            return self._k, None
        self._d = sum(self._slow_k) / self.d_period

        return self._k, self._d

    @property
    def k(self) -> float | None:
        """Slow %K, or None during warm-up."""
        return self._k

    @property
    def d(self) -> float | None:
        """%D signal line, or None during warm-up."""
        return self._d

    @property
    def ready(self) -> bool:
        """True once both %K and %D are defined."""
        return self._k is not None and self._d is not None
