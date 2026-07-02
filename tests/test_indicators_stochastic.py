"""Stochastic(3,2,3) unit tests. Expected values hand-computed independently.
Convention under test: %K period=3, slowing=2, %D period=3 (slow stochastic)."""
import math

import pytest

from src.rangebars.builder import RangeBar
from src.indicators.stochastic import Stochastic


def _bar(h, l, c):
    return RangeBar(open=c, high=h, low=l, close=c, start_ts=0, end_ts=0)


# (high, low, close) — same sequence used in the hand computation.
BARS = [
    (11.0, 9.0, 10.0),
    (12.0, 10.0, 11.5),
    (13.0, 9.5, 12.5),
    (14.0, 11.0, 13.5),
    (15.0, 12.0, 12.5),
    (14.5, 11.5, 14.0),
    (16.0, 13.0, 15.5),
]

# hand-computed (slow_%K, %D) per bar index; None during warm-up
EXPECTED = [
    (None, None),
    (None, None),
    (None, None),
    (88.194444, None),
    (71.717172, None),
    (64.772727, 74.894781),
    (81.944444, 72.811448),
]


def test_stochastic_323_hand_computed_sequence():
    s = Stochastic(k_period=3, slowing=2, d_period=3)
    for (bar, (ek, ed)) in zip(BARS, EXPECTED):
        k, d = s.update(_bar(*bar))
        if ek is None:
            assert k is None
        else:
            assert math.isclose(k, ek, rel_tol=1e-5)
        if ed is None:
            assert d is None
        else:
            assert math.isclose(d, ed, rel_tol=1e-5)


def test_ready_becomes_true_when_d_defined():
    s = Stochastic(k_period=3, slowing=2, d_period=3)
    for bar in BARS[:5]:
        s.update(_bar(*bar))
    assert s.ready is False          # %D not yet defined at bar index 4
    s.update(_bar(*BARS[5]))
    assert s.ready is True           # %D defined at bar index 5


def test_flat_window_gives_neutral_50():
    # All highs==lows==close → HH==LL every window → raw %K forced to 50.
    s = Stochastic(k_period=3, slowing=2, d_period=3)
    for _ in range(6):
        k, d = s.update(_bar(100.0, 100.0, 100.0))
    # slow %K is SMA of 50s = 50; %D is SMA of 50s = 50.
    assert math.isclose(k, 50.0, rel_tol=1e-12)
    assert math.isclose(d, 50.0, rel_tol=1e-12)


def test_close_at_top_of_range_is_100():
    # close == HH over the window and range non-zero → raw %K = 100.
    s = Stochastic(k_period=1, slowing=1, d_period=1)
    k, d = s.update(_bar(10.0, 5.0, 10.0))
    assert math.isclose(k, 100.0, rel_tol=1e-12)


def test_close_at_bottom_of_range_is_0():
    s = Stochastic(k_period=1, slowing=1, d_period=1)
    k, d = s.update(_bar(10.0, 5.0, 5.0))
    assert math.isclose(k, 0.0, abs_tol=1e-12)


def test_invalid_params_raise():
    with pytest.raises(ValueError):
        Stochastic(k_period=0)
    with pytest.raises(ValueError):
        Stochastic(slowing=0)
    with pytest.raises(ValueError):
        Stochastic(d_period=0)
