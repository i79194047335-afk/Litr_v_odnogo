"""Tests for the public-pool external check.

Expected values are computed on paper from the binomial definition, not read
off a run.

This tool's specific danger is that it produces a number which *sounds* like
validation. "9 of 11 agree" reads as strong until one notices that 6 of 11
also "mostly agrees" and means nothing. So the tests concentrate on the
p-value being a real tail probability, on disagreement actually registering,
and on an empty comparison refusing to look like a pass.
"""

from __future__ import annotations

import math

from src.analysis.pool_validation import (
    PoolRow,
    binomial_tail,
    binomial_tail_p,
    match_rate_under_independence,
    score,
)


def pool(apr: float, our: float, fills: int = 100, name: str = "p") -> PoolRow:
    return PoolRow(account_index=1, name=name, apr=apr, sharpe=0.0, status=1,
                   our_pnl=our, fills=fills)


# --- the binomial tail, from the definition --------------------------------

def test_all_agree_is_the_single_tail_term():
    """P(11 of 11 | fair coin) = 1/2^11 = 0.00048828125 exactly."""
    assert abs(binomial_tail(11, 11) - 1 / 2048) < 1e-15


def test_nine_of_eleven_matches_hand_computation():
    """C(11,9)+C(11,10)+C(11,11) = 55+11+1 = 67, over 2^11 = 2048."""
    assert abs(binomial_tail(9, 11) - 67 / 2048) < 1e-15


def test_nine_of_nine():
    """1/2^9 = 0.001953125."""
    assert abs(binomial_tail(9, 9) - 1 / 512) < 1e-15


def test_half_agreement_is_not_significant():
    """Half of a fair coin's outcomes lie at or above the midpoint."""
    assert binomial_tail(5, 10) > 0.5


def test_zero_agreement_has_probability_one():
    """"At least 0 agree" is certain — a tail, not a likelihood."""
    assert binomial_tail(0, 8) == 1.0


def test_empty_sample_is_not_a_probability():
    assert math.isnan(binomial_tail(0, 0))


# --- sign agreement ---------------------------------------------------------

def test_sign_agreement_counts_both_directions():
    """Agreement means same sign, not both positive."""
    rows = [pool(+10.0, +5.0), pool(-10.0, -5.0)]

    s = score(rows)

    assert s["agree"] == 2
    assert s["n"] == 2


def test_opposite_signs_are_counted_as_disagreement():
    rows = [pool(+10.0, -5.0), pool(-10.0, +5.0)]

    s = score(rows)

    assert s["agree"] == 0
    assert s["p"] == 1.0          # "at least 0 of 2" is certain


def test_pools_that_never_traded_are_excluded_from_the_sample():
    """A pool absent from the tape has no evidence, and must not pad n.

    Counting untraded pools as agreements would let the sample be inflated
    with accounts we never observed — the p-value would then measure the size
    of the showcase rather than the quality of the reconstruction.
    """
    rows = [pool(+10.0, +5.0, fills=50), pool(-10.0, 0.0, fills=0)]

    s = score(rows)

    assert s["n"] == 1
    assert s["agree"] == 1


def test_no_traded_pools_yields_no_measurement():
    s = score([pool(+10.0, +5.0, fills=0)])

    assert s["n"] == 0
    assert math.isnan(s["p"])


# --- rank correlation is over the traded subset -----------------------------

def test_rho_tracks_the_order_not_the_magnitudes():
    """APR and our PnL are on different scales by construction.

    Published APR is annual and net; ours is a few days and gross. Only the
    ordering is comparable, so a monotone rescale must not move rho.
    """
    rows = [pool(1.0, 100.0), pool(2.0, 900.0), pool(3.0, 2500.0),
            pool(4.0, 90000.0)]

    s = score(rows)

    assert abs(s["rho"] - 1.0) < 1e-12


def test_inverted_ranking_gives_negative_rho():
    """A reconstruction anti-correlated with the venue is not a success."""
    rows = [pool(1.0, -1.0, name="a"), pool(2.0, -2.0, name="b"),
            pool(3.0, -3.0, name="c"), pool(4.0, -4.0, name="d")]

    s = score(rows)

    assert abs(s["rho"] + 1.0) < 1e-12
    assert s["agree"] == 0


# --- PoolRow bookkeeping ----------------------------------------------------

def test_maker_share_is_reported_per_pool():
    r = PoolRow(account_index=1, name="p", apr=1.0, sharpe=0.0, status=1,
                fills=100, maker_fills=75)

    assert r.maker_share == 0.75


def test_zero_pnl_counts_as_not_positive():
    """A pool that realised nothing does not 'agree' with a positive APR."""
    assert not pool(+10.0, 0.0).agrees
    assert pool(-10.0, 0.0).agrees


# --- the null rate must come from the data, not be assumed ------------------

def test_shared_skew_raises_the_null_above_a_coin_flip():
    """If both series are mostly positive, matching is easy without a relationship.

    Nine of ten positive on each side: P(match) = 0.9*0.9 + 0.1*0.1 = 0.82.
    A fair-coin p-value would call routine agreement significant.
    """
    rows = [pool(+1.0, +1.0) for _ in range(9)] + [pool(-1.0, -1.0)]

    assert abs(match_rate_under_independence(rows) - 0.82) < 1e-12


def test_balanced_marginals_reduce_to_the_coin_flip():
    """Half positive on each side: 0.5*0.5 + 0.5*0.5 = 0.5, the fair coin."""
    rows = [pool(+1.0, +1.0), pool(+1.0, -1.0), pool(-1.0, +1.0), pool(-1.0, -1.0)]

    assert abs(match_rate_under_independence(rows) - 0.5) < 1e-12


def test_corrected_p_is_reported_beside_the_naive_one():
    rows = [pool(+1.0, +1.0) for _ in range(9)] + [pool(-1.0, -1.0)]

    s = score(rows)

    assert s["agree"] == 10
    assert abs(s["null_rate"] - 0.82) < 1e-12
    # 0.82^10 = 0.1374..., versus 0.5^10 = 0.00098 under a fair coin.
    assert abs(s["p_corrected"] - 0.82 ** 10) < 1e-12
    assert s["p_corrected"] > s["p"]


def test_generalised_tail_matches_the_fair_coin_case_at_p_half():
    assert abs(binomial_tail_p(9, 11, 0.5) - binomial_tail(9, 11)) < 1e-15
