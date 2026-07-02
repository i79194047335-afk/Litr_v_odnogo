"""Keltner unit tests. Expected values hand-computed independently.
period=3 keeps the SMAs checkable by hand."""
import math

import pytest

from src.rangebars.builder import RangeBar
from src.indicators.keltner import KeltnerCore, KeltnerChannels


def _bar(h, l, c):
    return RangeBar(open=c, high=h, low=l, close=c, start_ts=0, end_ts=0)


# (high, low, close) sequence used for hand computation
BARS = [
    (11.0, 9.0, 10.0),   # H-L = 2
    (13.0, 10.0, 12.0),  # H-L = 3
    (15.0, 12.0, 14.0),  # H-L = 3
    (16.0, 13.0, 15.0),  # H-L = 3
]


def test_warmup_returns_none_until_window_full():
    core = KeltnerCore(period=3)
    core.update(_bar(*BARS[0]))
    assert core.ready is False
    assert core.centerline is None
    assert core.band(4) is None
    core.update(_bar(*BARS[1]))
    assert core.centerline is None
    core.update(_bar(*BARS[2]))          # 3rd bar → window full
    assert core.ready is True
    assert core.centerline is not None


def test_centerline_and_bands_hand_computed_bar3():
    core = KeltnerCore(period=3)
    for b in BARS[:3]:
        core.update(_bar(*b))
    # closes [10,12,14] → SMA 12.0 ; ranges [2,3,3] → SMA 8/3
    assert math.isclose(core.centerline, 12.0, rel_tol=1e-12)
    up4, lo4 = core.band(4)
    assert math.isclose(up4, 22.666667, rel_tol=1e-6)
    assert math.isclose(lo4, 1.333333, rel_tol=1e-6)
    up8, lo8 = core.band(8)
    assert math.isclose(up8, 33.333333, rel_tol=1e-6)
    assert math.isclose(lo8, -9.333333, rel_tol=1e-6)


def test_centerline_and_bands_hand_computed_bar4():
    core = KeltnerCore(period=3)
    for b in BARS:
        core.update(_bar(*b))
    # closes [12,14,15] → SMA 41/3 ; ranges [3,3,3] → SMA 3.0
    assert math.isclose(core.centerline, 13.666667, rel_tol=1e-6)
    up4, lo4 = core.band(4)
    assert math.isclose(up4, 25.666667, rel_tol=1e-6)
    assert math.isclose(lo4, 1.666667, rel_tol=1e-6)


def test_outer_band_is_wider_than_inner_same_core():
    # both channels share one core; outer (mult=8) must strictly contain
    # inner (mult=4) around the same centerline.
    ch = KeltnerChannels(period=3, mult_inner=4, mult_outer=8)
    for b in BARS[:3]:
        ch.update(_bar(*b))
    iu, il = ch.inner
    ou, ol = ch.outer
    assert ou > iu > ch.centerline > il > ol


def test_channels_wrapper_matches_core():
    # KeltnerChannels.inner/outer must equal KeltnerCore.band() for the
    # same inputs — proves the wrapper adds no arithmetic of its own.
    core = KeltnerCore(period=3)
    ch = KeltnerChannels(period=3, mult_inner=4, mult_outer=8)
    for b in BARS[:3]:
        core.update(_bar(*b)); ch.update(_bar(*b))
    assert ch.inner == core.band(4)
    assert ch.outer == core.band(8)


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        KeltnerCore(period=0)
