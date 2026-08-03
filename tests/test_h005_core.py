"""Test for the one piece of logic H-005 adds on top of tested tools.

`h005_core_restrict.py` reuses `pnl_persistence.compare` and `day_pnl`
unchanged — the correlation and null-band maths are covered by
`test_pnl_persistence.py` (13 tests). The only new logic is `core_accounts`,
which decides *which* accounts enter the comparison, and the entire H-005
verdict hangs on that choice: pick the population wrongly and the measurement
answers a different question than the one pre-registered.

Expected values here are derived from the rule ("present on every day"), not
read off the implementation.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import h005_core_restrict as h005  # noqa: E402


@pytest.fixture
def fake_days(monkeypatch):
    """Three days with a known intersection.

    Account 1 trades every day -> core. Account 2 misses day 2. Account 3
    appears only on day 3. Account 4 is present everywhere but below threshold
    on day 2, which day_pnl already filters out before we see it.
    """
    tape = {
        "d1": {1: 10.0, 2: 5.0, 4: 1.0},
        "d2": {1: -3.0, 3: 0.0, 4: 2.0},   # 2 absent
        "d3": {1: 7.0, 2: 4.0, 3: 9.0},    # 4 absent
    }

    def fake_day_pnl(market, day, min_fills):
        return dict(tape[day])

    monkeypatch.setattr(h005, "day_pnl", fake_day_pnl)
    return tape


def test_core_is_intersection_across_all_days(fake_days):
    """Only accounts present on every day qualify."""
    core = h005.core_accounts(0, ("d1", "d2", "d3"), 50)
    assert core == {1}


def test_account_missing_one_day_is_excluded(fake_days):
    """Account 2 trades on d1 and d3 but not d2 — it must not be core.

    This is the case that matters: a two-of-three account is exactly what a
    'stable core' must exclude, otherwise the restriction stops discriminating
    between a persistent core and a transient population.
    """
    core = h005.core_accounts(0, ("d1", "d2", "d3"), 50)
    assert 2 not in core


def test_shorter_window_admits_more_accounts(fake_days):
    """Dropping the day an account missed lets it back in — the restriction
    depends on the window, so the window has to be stated with any result."""
    core = h005.core_accounts(0, ("d1", "d3"), 50)
    assert core == {1, 2}


def test_single_day_window_is_that_day(fake_days):
    core = h005.core_accounts(0, ("d2",), 50)
    assert core == {1, 3, 4}


def test_empty_intersection_is_empty_not_error(fake_days):
    """Two disjoint days must give an empty core rather than raising: the
    caller checks for a too-small core and reports it, which is a real
    possibility on a thin market."""
    core = h005.core_accounts(0, ("d1", "d2"), 50)
    assert core == {1, 4}

    # A day sharing nothing with d1.
    def only_new(market, day, min_fills):
        return {99: 1.0} if day == "dx" else {1: 1.0, 4: 1.0}

    import types
    h005.day_pnl = only_new
    assert h005.core_accounts(0, ("d1", "dx"), 50) == set()


def test_core_does_not_depend_on_day_order(fake_days):
    """Intersection is commutative; a different day order must not change it."""
    a = h005.core_accounts(0, ("d1", "d2", "d3"), 50)
    b = h005.core_accounts(0, ("d3", "d1", "d2"), 50)
    assert a == b
