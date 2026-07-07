"""Tests for export_sample.py — window selection, trade filtering, JSON round-trip.

All expected OHLC/n_ticks values below were derived BY HAND from the synthetic
tick stream and the RangeBarBuilder rules (builder.py), on paper, before
running the code.  This is the project convention — see CONTEXT.md Anti-patterns:
"Tests that verify the code against itself".
"""

import json
import math

import pytest

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine
from src.backtest.strategy import Trade
from src.backtest.export_sample import (
    ObservedStrategy,
    filter_trades,
    load_ticks,
)


# ---------------------------------------------------------------------------
# Synthetic tick stream for window-selection test
# ---------------------------------------------------------------------------
# range_size = 1.0.  Each tick moves price +1.0 from the previous, always
# up, always in a new UTC minute (ts spaced 60_000 apart).  This produces
# exactly one range bar per tick (after the first).
#
# Hand derivation (RangeBarBuilder.update logic, builder.py:33-58):
#
#   Tick (100.0,       0): bar 0 opens (o=100, n_ticks=1)
#   Tick (101.0,  60_000): bar 0 range 1.0 ≥ 1.0 → up bar.
#       close_px = low + range_size = 100 + 1 = 101.
#       Bar 0:  o=100  h=101  l=100  c=101  start_ts=0        end_ts=60_000   n_ticks=2
#     New bar opens at 101.0 (n_ticks=0).
#   Tick (102.0, 120_000): bar 1 range 1.0 → up bar.
#       Bar 1:  o=101  h=102  l=101  c=102  start_ts=60_000   end_ts=120_000  n_ticks=1
#   Tick (103.0, 180_000):
#       Bar 2:  o=102  h=103  l=102  c=103  start_ts=120_000  end_ts=180_000  n_ticks=1
#   Tick (104.0, 240_000):
#       Bar 3:  o=103  h=104  l=103  c=104  start_ts=180_000  end_ts=240_000  n_ticks=1
#   Tick (105.0, 300_000):
#       Bar 4:  o=104  h=105  l=104  c=105  start_ts=240_000  end_ts=300_000  n_ticks=1
#   Tick (106.0, 360_000):
#       Bar 5:  o=105  h=106  l=105  c=106  start_ts=300_000  end_ts=360_000  n_ticks=1
#   Tick (107.0, 420_000):
#       Bar 6:  o=106  h=107  l=106  c=107  start_ts=360_000  end_ts=420_000  n_ticks=1
#
# start_bar=2, n_bars=3 → snapshots for bars 2, 3, 4.

TICKS_7_BARS = [
    (100.0, 0),
    (101.0, 60_000),
    (102.0, 120_000),
    (103.0, 180_000),
    (104.0, 240_000),
    (105.0, 300_000),
    (106.0, 360_000),
    (107.0, 420_000),
]

EXPECTED_BAR_2 = {"index": 2, "o": 102.0, "h": 103.0, "l": 102.0, "c": 103.0,
                  "start_ts": 120_000, "end_ts": 180_000, "n_ticks": 1}
EXPECTED_BAR_3 = {"index": 3, "o": 103.0, "h": 104.0, "l": 103.0, "c": 104.0,
                  "start_ts": 180_000, "end_ts": 240_000, "n_ticks": 1}
EXPECTED_BAR_4 = {"index": 4, "o": 104.0, "h": 105.0, "l": 104.0, "c": 105.0,
                  "start_ts": 240_000, "end_ts": 300_000, "n_ticks": 1}


# ---------------------------------------------------------------------------
# Window selection
# ---------------------------------------------------------------------------

def test_window_selection_snapshots_bars_2_3_4():
    """With a stream producing 7 known bars, start_bar=2, n_bars=3 selects
    exactly bars index 2, 3, 4 with hand-verified OHLC values."""
    replay = Replay(range_size=1.0, ema_fast=15, ema_slow=20)
    engine = FillEngine()
    strategy = ObservedStrategy(replay, engine, start_bar=2, n_bars=3)

    replay.run(TICKS_7_BARS, strategy)

    assert len(strategy.snapshots) == 3

    for key in ("index", "o", "h", "l", "c", "start_ts", "end_ts", "n_ticks"):
        assert strategy.snapshots[0][key] == EXPECTED_BAR_2[key], \
            f"bar 2 mismatch on {key}"
        assert strategy.snapshots[1][key] == EXPECTED_BAR_3[key], \
            f"bar 3 mismatch on {key}"
        assert strategy.snapshots[2][key] == EXPECTED_BAR_4[key], \
            f"bar 4 mismatch on {key}"

    # Bias depends on EMA readiness; with 15/20 periods and only 7 ticks
    # it's not ready, but field must be present.
    for s in strategy.snapshots:
        assert "bias" in s
        assert "lines" in s
        assert "swing_low" in s
        assert "swing_high" in s
        assert "in_position" in s
        assert "side" in s
        assert "stop_price" in s


def test_window_respects_n_bars_truncation():
    """n_bars can be larger than remaining bars — snapshots stop at end."""
    replay = Replay(range_size=1.0, ema_fast=15, ema_slow=20)
    engine = FillEngine()
    strategy = ObservedStrategy(replay, engine, start_bar=5, n_bars=10)

    replay.run(TICKS_7_BARS, strategy)

    # Only bars 5 and 6 exist (7 bars total, indices 0-6)
    assert len(strategy.snapshots) == 2
    assert strategy.snapshots[0]["index"] == 5
    assert strategy.snapshots[1]["index"] == 6


def test_start_bar_beyond_end_gives_empty():
    """start_bar beyond the last bar index produces empty snapshots."""
    replay = Replay(range_size=1.0, ema_fast=15, ema_slow=20)
    engine = FillEngine()
    strategy = ObservedStrategy(replay, engine, start_bar=100, n_bars=10)

    replay.run(TICKS_7_BARS, strategy)

    assert strategy.snapshots == []


# ---------------------------------------------------------------------------
# Trade filtering
# ---------------------------------------------------------------------------

def _make_trade(side="long", tag="part1", entry_price=100.0, entry_ts=0,
                exit_price=105.0, exit_ts=10, exit_reason="take",
                r_multiple=0.5, risk=10.0, size=1.0) -> Trade:
    return Trade(side=side, tag=tag, size=size,
                 entry_price=entry_price, entry_ts=entry_ts,
                 exit_price=exit_price, exit_ts=exit_ts,
                 exit_reason=exit_reason, r_multiple=r_multiple, risk=risk)


def _make_snapshot(start_ts: int, end_ts: int) -> dict:
    """Minimal dict with only the fields filter_trades reads."""
    return {"start_ts": start_ts, "end_ts": end_ts}


def test_trade_entry_inside_window_included():
    """Trade whose entry_ts falls inside the snapshot window is included,
    even if exit_ts is after the window."""
    # Window: [2000, 5000]
    snapshots = [
        _make_snapshot(2000, 3000),
        _make_snapshot(3000, 4000),
        _make_snapshot(4000, 5000),
    ]
    trade = _make_trade(entry_ts=3000, exit_ts=6000)

    result = filter_trades([trade], snapshots)
    assert len(result) == 1
    assert result[0]["entry_ts"] == 3000
    assert result[0]["exit_ts"] == 6000


def test_trade_exit_inside_window_included():
    """Trade whose exit_ts falls inside the snapshot window is included,
    even if entry_ts is before the window."""
    snapshots = [
        _make_snapshot(2000, 3000),
        _make_snapshot(3000, 4000),
    ]
    trade = _make_trade(entry_ts=1000, exit_ts=3000)

    result = filter_trades([trade], snapshots)
    assert len(result) == 1
    assert result[0]["entry_ts"] == 1000
    assert result[0]["exit_ts"] == 3000


def test_trade_both_before_window_excluded():
    """Trade with both entry_ts and exit_ts before the snapshot window
    is excluded."""
    snapshots = [
        _make_snapshot(2000, 3000),
        _make_snapshot(3000, 4000),
    ]
    trade = _make_trade(entry_ts=1000, exit_ts=1500)

    result = filter_trades([trade], snapshots)
    assert result == []


def test_trade_both_after_window_excluded():
    """Trade with both entry_ts and exit_ts after the snapshot window
    is excluded."""
    snapshots = [
        _make_snapshot(2000, 3000),
    ]
    trade = _make_trade(entry_ts=5000, exit_ts=6000)

    result = filter_trades([trade], snapshots)
    assert result == []


def test_trade_on_window_boundary_included():
    """Trade entry_ts exactly at t_min (start of window) is included
    (inclusive boundary)."""
    snapshots = [
        _make_snapshot(2000, 3000),
    ]
    trade = _make_trade(entry_ts=2000, exit_ts=2500)

    result = filter_trades([trade], snapshots)
    assert len(result) == 1


def test_filter_trades_empty_snapshots():
    """Empty snapshots → empty result, no crash."""
    trade = _make_trade(entry_ts=1000, exit_ts=2000)
    result = filter_trades([trade], [])
    assert result == []


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------

def test_json_output_round_trips():
    """Compact JSON output must be valid JSON and round-trip to the same
    in-memory dict."""
    output = {
        "meta": {"range_size": 1.0, "tick_size": 0.1, "bars_exported": 3},
        "bars": [
            {"index": 0, "o": 1.0, "c": 2.0, "bias": "bull", "lines": None},
        ],
        "trades": [
            {"side": "long", "tag": "part1", "r_multiple": 0.5},
        ],
    }

    encoded = json.dumps(output, indent=None)
    # Must be valid JSON
    decoded = json.loads(encoded)
    assert decoded == output

    # indent=None means compact (no extra whitespace)
    assert "\n" not in encoded


def test_json_round_trip_with_special_floats():
    """Infinity and integer-valued floats survive round-trip intact."""
    output = {
        "meta": {"range_size": 15.3},
        "bars": [{"o": 100.0, "h": float("inf")}],
        "trades": [],
    }

    encoded = json.dumps(output, indent=None)
    decoded = json.loads(encoded)

    assert decoded["meta"]["range_size"] == 15.3
    assert decoded["bars"][0]["o"] == 100.0
    assert decoded["bars"][0]["h"] == float("inf")
    # int-valued float stays a float (JSON number round-trips as int or
    # float depending on parser — we just assert value equality)
    assert decoded["bars"][0]["o"] == 100.0
