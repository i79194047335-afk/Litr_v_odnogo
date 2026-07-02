"""EMA unit tests. Expected values hand-computed independently, not read
back from the implementation. period=3 → k=0.5, so the arithmetic is checkable
in your head: each value is the average of the new price and the prior EMA."""
import math

import pytest

from src.indicators.ema import EMA


def test_first_value_seeds_from_price():
    e = EMA(period=3)
    assert e.value is None
    assert e.update(10.0) == 10.0
    assert e.value == 10.0


def test_ema_period3_hand_computed():
    # k = 2/(3+1) = 0.5. EMA_t = 0.5*price + 0.5*prev.
    e = EMA(period=3)
    expected = [10.0, 10.5, 11.25, 12.125, 13.0625]
    got = [e.update(p) for p in [10.0, 11.0, 12.0, 13.0, 14.0]]
    for g, x in zip(got, expected):
        assert math.isclose(g, x, rel_tol=1e-12)


def test_ema_period1_is_passthrough():
    # k = 2/2 = 1.0 → EMA always equals the latest price.
    e = EMA(period=1)
    for p in [5.0, 9.0, 2.0]:
        assert e.update(p) == p


def test_ready_flag_tracks_count():
    e = EMA(period=3)
    e.update(1.0); assert e.ready is False   # 1 seen
    e.update(2.0); assert e.ready is False   # 2 seen
    e.update(3.0); assert e.ready is True    # 3 seen == period


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        EMA(period=0)


def test_constant_input_converges_to_that_constant():
    e = EMA(period=10)
    for _ in range(200):
        v = e.update(42.0)
    assert math.isclose(v, 42.0, rel_tol=1e-9)
