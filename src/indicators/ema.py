"""
Exponential Moving Average — streaming, O(1) per update.

Used for the Beggs/YTC bias proxy: EMA(15) vs EMA(20) cross on the 1-minute
series (see docs/ytc_scalper_skeleton.md §2.2).

Formula:
    k        = 2 / (period + 1)
    EMA_t    = price_t * k + EMA_{t-1} * (1 - k)

SEEDING CONVENTION (important, and an assumption — not a spec):
    The very first value is seeded directly from the first price:
        EMA_0 = price_0
    We do NOT warm up with an N-period SMA first. This is the simplest and
    most common streaming convention, and it suits range bars (which have no
    natural "session open" the way daily candles do). The trade-off: the
    first ~period values are biased toward the seed and should be treated as
    warm-up, not signal. Downstream code that cares can ignore the first
    `period` updates.

    If a backtest ever needs SMA-seeded EMA for parity with some charting
    package, that's a separate constructor — not a silent change here.
"""
from __future__ import annotations


class EMA:
    """Incremental EMA. Feed one price at a time; read `.value`."""

    __slots__ = ("period", "_k", "_value", "_count")

    def __init__(self, period: int):
        if period < 1:
            raise ValueError(f"EMA period must be >= 1, got {period}")
        self.period = period
        self._k = 2.0 / (period + 1)
        self._value: float | None = None
        self._count = 0

    def update(self, price: float) -> float:
        """Feed one price. Returns the new EMA value.

        On the first call the EMA is seeded to `price` (see module docstring).
        """
        if self._value is None:
            self._value = float(price)
        else:
            self._value = price * self._k + self._value * (1.0 - self._k)
        self._count += 1
        return self._value

    @property
    def value(self) -> float | None:
        """Current EMA, or None if no price has been fed yet."""
        return self._value

    @property
    def ready(self) -> bool:
        """True once at least `period` prices have been seen.

        Before this, the value is warm-up-biased by the seed and should not
        be trusted as signal. This does NOT gate `.value` — it's advisory.
        """
        return self._count >= self.period
