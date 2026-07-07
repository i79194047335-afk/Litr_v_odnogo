"""Tests for bias_audit.py — regime extraction, stats, and age-at-entry.

All expected values below were derived BY HAND from the synthetic bias sequence,
on paper, before running the code.  This is the project convention — see
CONTEXT.md Anti-patterns: "Tests that verify the code against itself".
"""
from __future__ import annotations

import pytest

from src.backtest.strategy import Trade
from src.backtest.bias_audit import (
    extract_regimes,
    regime_stats,
    compute_ages_at_entry,
)


# ---------------------------------------------------------------------------
# Hand-derived synthetic bias sequence
# ---------------------------------------------------------------------------
# Sequence (8 bars, indices 0-7):
#   idx:  0      1      2      3      4      5      6      7
#   bias: None   None   bear   bear   bear   bull   bull   None
#
# Walking through extract_regimes:
#   bar 0: bias=None     → start regime (None, 0, 1)
#   bar 1: bias=None     → same, length=2
#   bar 2: bias="bear"   → CHANGE → close (None, 0, 2), start ("bear", 2, 1)
#   bar 3: bias="bear"   → same, length=2
#   bar 4: bias="bear"   → same, length=3
#   bar 5: bias="bull"   → CHANGE → close ("bear", 2, 3), start ("bull", 5, 1)
#   bar 6: bias="bull"   → same, length=2
#   bar 7: bias=None     → CHANGE → close ("bull", 5, 2), start (None, 7, 1)
#   END                  → close (None, 7, 1)
#
# Expected regimes: [(None, 0, 2), ("bear", 2, 3), ("bull", 5, 2), (None, 7, 1)]

BIAS_HISTORY_8 = [
    (0, None),
    (1, None),
    (2, "bear"),
    (3, "bear"),
    (4, "bear"),
    (5, "bull"),
    (6, "bull"),
    (7, None),
]

EXPECTED_REGIMES_8 = [
    (None, 0, 2),
    ("bear", 2, 3),
    ("bull", 5, 2),
    (None, 7, 1),
]

# Hand-derived stats from EXPECTED_REGIMES_8:
#   None: lengths [2, 1] → n=2, median=1.5, min=1, max=2
#   bear: lengths [3]   → n=1, median=3,   min=3, max=3
#   bull: lengths [2]   → n=1, median=2,   min=2, max=2


# ---------------------------------------------------------------------------
# extract_regimes
# ---------------------------------------------------------------------------

def test_extract_regimes_hand_derived_8():
    regimes = extract_regimes(BIAS_HISTORY_8)
    assert regimes == EXPECTED_REGIMES_8


def test_extract_regimes_empty():
    assert extract_regimes([]) == []


def test_extract_regimes_single_bar():
    history = [(0, "bull")]
    assert extract_regimes(history) == [("bull", 0, 1)]


def test_extract_regimes_no_change():
    """Three bars all bull — one regime of length 3."""
    history = [(0, "bull"), (1, "bull"), (2, "bull")]
    assert extract_regimes(history) == [("bull", 0, 3)]


def test_extract_regimes_changes_every_bar():
    """Every bar changes bias — 4 regimes of length 1 each."""
    history = [(0, None), (1, "bull"), (2, "bear"), (3, None)]
    expected = [(None, 0, 1), ("bull", 1, 1), ("bear", 2, 1), (None, 3, 1)]
    assert extract_regimes(history) == expected


def test_extract_regimes_all_none():
    history = [(0, None), (1, None), (2, None)]
    assert extract_regimes(history) == [(None, 0, 3)]


# ---------------------------------------------------------------------------
# regime_stats
# ---------------------------------------------------------------------------

def test_regime_stats_hand_derived_8():
    stats = regime_stats(EXPECTED_REGIMES_8)

    # None: [2, 1]
    assert stats[None]["n"] == 2
    assert stats[None]["median"] == 1.5
    assert stats[None]["min"] == 1
    assert stats[None]["max"] == 2
    assert stats[None]["<5"] == 2
    assert stats[None]["5-20"] == 0
    assert stats[None][">20"] == 0

    # bear: [3]
    assert stats["bear"]["n"] == 1
    assert stats["bear"]["median"] == 3
    assert stats["bear"]["min"] == 3
    assert stats["bear"]["max"] == 3
    assert stats["bear"]["<5"] == 1
    assert stats["bear"]["5-20"] == 0
    assert stats["bear"][">20"] == 0

    # bull: [2]
    assert stats["bull"]["n"] == 1
    assert stats["bull"]["median"] == 2
    assert stats["bull"]["min"] == 2
    assert stats["bull"]["max"] == 2
    assert stats["bull"]["<5"] == 1
    assert stats["bull"]["5-20"] == 0
    assert stats["bull"][">20"] == 0


def test_regime_stats_empty():
    stats = regime_stats([])
    for bias_val in ["bull", "bear", None]:
        assert stats[bias_val]["n"] == 0
        assert stats[bias_val]["median"] is None


def test_regime_stats_histogram_buckets():
    """Hand-crafted regimes to hit all histogram buckets."""
    # bull: lengths [1, 3] → both <5
    # bear: lengths [7, 12] → one 5-20, one 5-20
    # None: lengths [25] → >20
    regimes = [
        ("bull", 0, 1),
        ("bear", 1, 7),
        ("bull", 8, 3),
        ("bear", 11, 12),
        (None, 23, 25),
    ]
    stats = regime_stats(regimes)

    assert stats["bull"]["<5"] == 2
    assert stats["bull"]["5-20"] == 0
    assert stats["bull"][">20"] == 0

    assert stats["bear"]["<5"] == 0
    assert stats["bear"]["5-20"] == 2  # 7 and 12 both in [5,20]
    assert stats["bear"][">20"] == 0

    assert stats[None]["<5"] == 0
    assert stats[None]["5-20"] == 0
    assert stats[None][">20"] == 1  # 25 > 20


# ---------------------------------------------------------------------------
# compute_ages_at_entry
# ---------------------------------------------------------------------------

def _make_trade(entry_ts: int) -> Trade:
    """Minimal Trade for age-at-entry testing — only entry_ts matters."""
    return Trade(
        side="long", tag="part1", size=1.0,
        entry_price=100.0, entry_ts=entry_ts,
        exit_price=101.0, exit_ts=entry_ts + 1000,
        exit_reason="take", r_multiple=0.5, risk=2.0,
    )


def test_compute_ages_at_entry_hand_derived():
    """Two trades in the 8-bar sequence:
    - Trade A entry at bar 3 (bear regime, started bar 2): age = 3-2+1 = 2
    - Trade B entry at bar 5 (bull regime, started bar 5): age = 5-5+1 = 1
    """
    trades = [_make_trade(100), _make_trade(200)]
    entry_events = [(100, 3), (200, 5)]  # (ts, bar_index)

    ages = compute_ages_at_entry(
        trades, BIAS_HISTORY_8, entry_events, EXPECTED_REGIMES_8,
    )
    assert ages == [2, 1]


def test_compute_ages_at_entry_both_parts_same_bar():
    """Part1 and part2 fill on same tick (same bar_index) — both get same age."""
    # History: three bear bars then two bull bars
    #   [(0, "bear"), (1, "bear"), (2, "bear"), (3, "bull"), (4, "bull")]
    # Regimes: [("bear", 0, 3), ("bull", 3, 2)]
    history = [(0, "bear"), (1, "bear"), (2, "bear"), (3, "bull"), (4, "bull")]
    regimes = extract_regimes(history)

    # Both parts fill at bar_index=3 (bull started at bar 3) → age=1
    trades = [_make_trade(100), _make_trade(100)]  # same ts
    entry_events = [(100, 3)]

    ages = compute_ages_at_entry(trades, history, entry_events, regimes)
    assert ages == [1, 1]  # both trades match entry_ts=100 → bar 3


def test_compute_ages_at_entry_mid_regime():
    """Entry at bar 1 in a [bear, bear, bear] regime (started bar 0).
    Age = 1-0+1 = 2."""
    history = [(0, "bear"), (1, "bear"), (2, "bear")]
    regimes = extract_regimes(history)
    trades = [_make_trade(100)]
    entry_events = [(100, 1)]

    ages = compute_ages_at_entry(trades, history, entry_events, regimes)
    assert ages == [2]


def test_compute_ages_at_entry_no_matching_entry_event():
    """Trade with entry_ts not in entry_events → skipped with warning."""
    history = [(0, "bull"), (1, "bull")]
    regimes = extract_regimes(history)
    trades = [_make_trade(999)]  # ts not in entry_events
    entry_events = [(100, 0)]

    ages = compute_ages_at_entry(trades, history, entry_events, regimes)
    assert ages == []  # skipped


def test_compute_ages_at_entry_bias_none_at_entry():
    """Entry at bar where bias is None → skipped (entries shouldn't happen
    during None, but be defensive)."""
    history = [(0, None), (1, "bull")]
    regimes = extract_regimes(history)  # [(None, 0, 1), ("bull", 1, 1)]
    trades = [_make_trade(100)]
    entry_events = [(100, 0)]  # bar 0 has bias=None

    ages = compute_ages_at_entry(trades, history, entry_events, regimes)
    assert ages == []  # bar 0 has no non-None regime


def test_compute_ages_at_entry_empty():
    """Empty inputs → empty output."""
    assert compute_ages_at_entry([], [], [], []) == []
