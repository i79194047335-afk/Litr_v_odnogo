"""Tests for day-to-day PnL rank stability.

Expected values are derived on paper. The Spearman cases use textbook inputs
whose correlation is known in closed form, not values read off this code.

This module exists because the first version of H-003 claim B was computed in
scratch scripts and the falsifier returned UNTESTABLE — nine numbers with no
runnable source. So the tests here care most about the two ways this tool
could produce a confident number that means nothing: a null model that does
not actually destroy the association, and an overlap statistic contaminated
by which accounts happen to be eligible.
"""

from __future__ import annotations

from src.analysis.pnl_persistence import compare, spearman


# --- spearman against known values -----------------------------------------

def test_spearman_of_identical_order_is_one():
    assert abs(spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) - 1.0) < 1e-12


def test_spearman_of_reversed_order_is_minus_one():
    assert abs(spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]) + 1.0) < 1e-12


def test_spearman_is_rank_based_not_value_based():
    """Monotone rescaling must not change rho — that is the point of ranks."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    linear = [2.0, 4.0, 6.0, 8.0, 10.0]
    exponential = [1.0, 10.0, 1e3, 1e6, 1e9]

    assert abs(spearman(xs, linear) - spearman(xs, exponential)) < 1e-12


def test_spearman_handles_ties_with_averaged_ranks():
    """xs all tied -> zero variance in one ranking -> correlation undefined.

    Returning 0.0 here would read as "measured no association" when nothing
    was measurable at all.
    """
    import math
    assert math.isnan(spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))


def test_spearman_of_one_swapped_pair_is_high_but_not_one():
    """Ranks 1,2,3,4 vs 2,1,3,4: d^2 = 1+1+0+0 = 2.

    rho = 1 - 6*sum(d^2) / (n*(n^2-1)) = 1 - 12/60 = 0.8 exactly.
    """
    assert abs(spearman([1.0, 2.0, 3.0, 4.0], [2.0, 1.0, 3.0, 4.0]) - 0.8) < 1e-12


# --- the eligibility artifact the falsifier's kill-shot targets -------------

def test_overlap_is_reported_both_as_ranked_and_within_the_common_pool():
    """An account leaving the top by disappearing is not the same as by losing.

    Day A's top-2 is {1, 2}. On day B account 2 is absent entirely and account
    3 has risen, so as-ranked overlap is 1/2. Restricted to accounts present
    on both days, day A's top-2 becomes {1, 3} and day B's is {3, 1} — an
    overlap of 2/2. The gap between the two numbers is the artifact.
    """
    a = {1: 100.0, 2: 90.0, 3: 10.0, 4: -5.0}
    b = {1: 50.0, 3: 80.0, 4: -1.0}

    s = compare(a, b, "dayA", "dayB", top_n=2, draws=50)

    assert s.overlap_raw == 1
    assert s.overlap_common == 2
    assert s.common == 3


# --- the null must actually be a null --------------------------------------

def test_perfectly_persistent_ranking_beats_its_null():
    """Same order both days: rho = 1, and the shuffled null must sit well below."""
    a = {i: float(i) for i in range(30)}
    b = {i: float(i) * 3.0 for i in range(30)}

    s = compare(a, b, "dayA", "dayB", top_n=5, draws=300)

    assert abs(s.spearman - 1.0) < 1e-12
    assert s.spearman > s.spearman_null_p95
    assert s.sign_agree == s.common


def test_reversed_ranking_is_detected_as_negative_not_as_persistence():
    """Yesterday's winners are today's losers: rho = -1.

    A tool that took |rho| would call this strong persistence.
    """
    a = {i: float(i) for i in range(20)}
    b = {i: -float(i) for i in range(20)}

    s = compare(a, b, "dayA", "dayB", top_n=5, draws=200)

    assert abs(s.spearman + 1.0) < 1e-12
    assert s.spearman < s.spearman_null_p95


def test_independent_days_do_not_beat_the_null():
    """Unrelated PnL must not read as persistence.

    Day B is a fixed permutation of day A's values with no relation to the
    accounts' day-A ordering, so the true rho should sit inside the null band.
    """
    a = {i: float(i) for i in range(40)}
    shuffled = [17.0, 3.0, 39.0, 8.0, 22.0, 1.0, 30.0, 12.0, 5.0, 28.0,
                36.0, 0.0, 19.0, 25.0, 7.0, 33.0, 11.0, 38.0, 2.0, 14.0,
                27.0, 6.0, 31.0, 20.0, 9.0, 35.0, 4.0, 23.0, 16.0, 37.0,
                10.0, 29.0, 13.0, 21.0, 34.0, 15.0, 26.0, 18.0, 32.0, 24.0]
    b = {i: shuffled[i] for i in range(40)}

    s = compare(a, b, "dayA", "dayB", top_n=10, draws=500)

    assert s.spearman <= s.spearman_null_p95


def test_sign_null_preserves_the_distribution_it_shuffles():
    """Shuffling must move the pairing, not the population.

    Every account is profitable on both days, so agreement is 100% and stays
    100% under any shuffle: a null that changed the values would break this.
    """
    a = {i: float(i + 1) for i in range(20)}
    b = {i: float(i + 1) * 2 for i in range(20)}

    s = compare(a, b, "dayA", "dayB", top_n=5, draws=200)

    assert s.sign_agree == 20
    assert abs(s.sign_null_mean - 20.0) < 1e-9


# --- degenerate inputs ------------------------------------------------------

def test_no_common_accounts_yields_no_measurement():
    a = {1: 10.0, 2: 20.0}
    b = {3: 30.0, 4: 40.0}

    s = compare(a, b, "dayA", "dayB", top_n=2, draws=50)

    assert s.common == 0
    assert s.sign_agree == 0
    import math
    assert math.isnan(s.spearman)


# --- multi-day windows ------------------------------------------------------

def test_window_threshold_applies_to_the_window_not_to_each_day(monkeypatch, tmp_path):
    """An account trading 15 fills a day for three days has 45 fills, not 0.

    Applying `min_fills` per day would discard it and rebuild exactly the
    eligibility artifact that a longer window exists to escape.
    """
    from src.analysis import pnl_persistence as mod

    class FakeRow:
        def __init__(self, account_id, realised, fills):
            self.account_id, self.realised, self.fills = account_id, realised, fills

    per_day = {
        "d1": {1: FakeRow(1, 10.0, 15), 2: FakeRow(2, -5.0, 1)},
        "d2": {1: FakeRow(1, 20.0, 15), 2: FakeRow(2, -5.0, 1)},
        "d3": {1: FakeRow(1, 30.0, 15), 2: FakeRow(2, -5.0, 1)},
    }
    calls = []

    monkeypatch.setattr(mod, "TAPE_DIR", tmp_path)
    for day in per_day:
        (tmp_path / f"trades_full_9_{day}.jsonl.gz").write_bytes(b"")
    monkeypatch.setattr(mod, "read_tape", lambda paths: calls.append(paths) or [])
    monkeypatch.setattr(mod, "accumulate_pnl",
                        lambda recs: per_day[calls[-1][0].name.split("_")[-1].split(".")[0]])

    out = mod.window_pnl(9, ["d1", "d2", "d3"], min_fills=20)

    # Account 1: 45 fills over the window, PnL 10+20+30 = 60. Kept.
    # Account 2: 3 fills over the window. Dropped.
    assert out == {1: 60.0}


def test_window_sums_pnl_across_days(monkeypatch, tmp_path):
    """A losing day and a winning day net out, rather than counting separately."""
    from src.analysis import pnl_persistence as mod

    class FakeRow:
        def __init__(self, account_id, realised, fills):
            self.account_id, self.realised, self.fills = account_id, realised, fills

    per_day = {"d1": {7: FakeRow(7, -100.0, 30)}, "d2": {7: FakeRow(7, +250.0, 30)}}
    calls = []

    monkeypatch.setattr(mod, "TAPE_DIR", tmp_path)
    for day in per_day:
        (tmp_path / f"trades_full_9_{day}.jsonl.gz").write_bytes(b"")
    monkeypatch.setattr(mod, "read_tape", lambda paths: calls.append(paths) or [])
    monkeypatch.setattr(mod, "accumulate_pnl",
                        lambda recs: per_day[calls[-1][0].name.split("_")[-1].split(".")[0]])

    out = mod.window_pnl(9, ["d1", "d2"], min_fills=20)

    assert out == {7: 150.0}
