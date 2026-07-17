"""Tests for the duplicate diagnostic.

Every expected value below is derived by hand from the fixture rows, on
paper, from the definitions — never read back from what the code produced.
(Repo standard, see CONTEXT.md anti-patterns.)
"""
import pytest

from src.collector.dupe_diagnostic import (
    DupeReport, analyze, legacy_key, _count_duplicates,
)


def rec(t, p, s, side, tid=None):
    r = {"m": 1, "t": t, "p": p, "s": s, "side": side}
    if tid is not None:
        r["tid"] = tid
    return r


# --- _count_duplicates --------------------------------------------------------

def test_count_duplicates_none():
    assert _count_duplicates([1, 2, 3]) == 0


def test_count_duplicates_counts_repeats_not_distinct_values():
    # 1 appears 3x -> 2 repeats; 2 appears 2x -> 1 repeat; 3 appears once -> 0.
    assert _count_duplicates([1, 1, 1, 2, 2, 3]) == 3


def test_count_duplicates_empty():
    assert _count_duplicates([]) == 0


# --- legacy_key ---------------------------------------------------------------

def test_legacy_key_ignores_tid_and_market():
    a = rec(100, 50.0, 1.0, "buy", tid=7)
    b = rec(100, 50.0, 1.0, "buy", tid=8)
    assert legacy_key(a) == legacy_key(b) == (100, 50.0, 1.0, "buy")


# --- analyze: the core claim --------------------------------------------------

def test_v2_sweep_is_reported_as_false_collapse_not_duplicate():
    """Three distinct fills of one sweep: same ms, price, size, side —
    different tids. Hand-derived: key collisions = 2 (rows 2 and 3 repeat
    row 1's key), tid duplicates = 0, therefore false collapses = 2."""
    rows = [
        rec(1000, 50.0, 0.5, "buy", tid=1),
        rec(1000, 50.0, 0.5, "buy", tid=2),
        rec(1000, 50.0, 0.5, "buy", tid=3),
    ]
    r = analyze(rows)
    assert r.schema == "v2"
    assert r.n_rows == 3
    assert r.tid_duplicates == 0
    assert r.key_collisions == 2
    assert r.false_collapses == 2
    assert r.false_collapse_pct == pytest.approx(200.0 / 3)


def test_v2_genuine_duplicate_is_counted_by_tid():
    """Same trade delivered twice (same tid). key collisions = 1, tid
    duplicates = 1, so false collapses = 0 — the key would have been right
    here, and the diagnostic says so."""
    rows = [
        rec(1000, 50.0, 0.5, "buy", tid=1),
        rec(1000, 50.0, 0.5, "buy", tid=1),
    ]
    r = analyze(rows)
    assert r.tid_duplicates == 1
    assert r.key_collisions == 1
    assert r.false_collapses == 0


def test_v2_mixture_separates_the_two():
    """tid=1 delivered twice (1 real dupe). tid=2,3 are distinct fills that
    share tid=1's key (2 more key collisions). Hand count:
    keys = 4 rows all identical -> 3 collisions; tid dupes = 1;
    false collapses = 3 - 1 = 2."""
    rows = [
        rec(1000, 50.0, 0.5, "buy", tid=1),
        rec(1000, 50.0, 0.5, "buy", tid=1),
        rec(1000, 50.0, 0.5, "buy", tid=2),
        rec(1000, 50.0, 0.5, "buy", tid=3),
    ]
    r = analyze(rows)
    assert r.n_rows == 4
    assert r.tid_duplicates == 1
    assert r.key_collisions == 3
    assert r.false_collapses == 2


def test_v1_refuses_to_guess():
    """No tid anywhere: the two quantities are indistinguishable and the
    report must say so rather than report a number that looks authoritative."""
    rows = [
        rec(1000, 50.0, 0.5, "buy"),
        rec(1000, 50.0, 0.5, "buy"),
        rec(1001, 50.1, 0.2, "sell"),
    ]
    r = analyze(rows)
    assert r.schema == "v1"
    assert r.key_collisions == 1
    assert r.tid_duplicates is None
    assert r.false_collapses is None
    assert r.false_collapse_pct is None


def test_mixed_schema_also_refuses_to_guess():
    rows = [
        rec(1000, 50.0, 0.5, "buy", tid=1),
        rec(1000, 50.0, 0.5, "buy"),
    ]
    r = analyze(rows)
    assert r.schema == "mixed"
    assert r.n_with_tid == 1
    assert r.tid_duplicates is None
    assert r.false_collapses is None


def test_distinct_trades_collide_on_nothing():
    rows = [
        rec(1000, 50.0, 0.5, "buy", tid=1),
        rec(1001, 50.0, 0.5, "buy", tid=2),   # different ms
        rec(1000, 50.1, 0.5, "buy", tid=3),   # different price
        rec(1000, 50.0, 0.6, "buy", tid=4),   # different size
        rec(1000, 50.0, 0.5, "sell", tid=5),  # different side
    ]
    r = analyze(rows)
    assert r.key_collisions == 0
    assert r.false_collapses == 0


def test_empty_input():
    r = analyze([])
    assert r == DupeReport(0, "v1", 0, None, 0, None)
    assert r.key_collision_pct == 0.0


def test_key_collision_pct():
    rows = [rec(1, 1.0, 1.0, "buy", tid=i) for i in range(1, 5)]  # all same key
    r = analyze(rows)
    # 4 identical keys -> 3 collisions of 4 rows = 75%
    assert r.key_collisions == 3
    assert r.key_collision_pct == pytest.approx(75.0)
