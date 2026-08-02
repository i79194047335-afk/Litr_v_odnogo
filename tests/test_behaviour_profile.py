"""Tests for behavioural fingerprints and the permutation statistics.

Two jobs here.

First, paying a debt: `perm_test` and `cliffs_delta` came from
`diag_take_vs_rest.py`, which carried the inference that closed an entire
track while having no tests at all. MINING.md §5 required covering them before
reuse. Expected values below are derived from the definitions on paper.

Second, the feature set's own trap. These features exist to test whether
behaviour is stable where outcome is not — so a feature that quietly encodes
outcome would make the comparison meaningless. `test_no_feature_reads_pnl`
guards that directly.
"""

from __future__ import annotations

import math
import random

from src.analysis.behaviour_profile import (
    Behaviour,
    accumulate_behaviour,
    cliffs_delta,
    perm_test,
    stability,
)


# --- cliffs_delta, from its definition -------------------------------------

def test_delta_is_minus_one_when_every_a_is_below_every_b():
    """P(a>b)=0, P(a<b)=1, so delta = 0 - 1 = -1."""
    assert cliffs_delta([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == -1.0


def test_delta_is_plus_one_when_every_a_is_above_every_b():
    assert cliffs_delta([4.0, 5.0, 6.0], [1.0, 2.0, 3.0]) == 1.0


def test_identical_samples_give_zero():
    """Every pair ties, so neither term contributes."""
    assert cliffs_delta([1.0, 2.0], [1.0, 2.0]) == 0.0


def test_delta_counts_pairs_not_means():
    """a=[1,2,3], b=[2]. Pairs: 1<2, 2==2, 3>2.

    gt = 1, lt = 1, total = 3, so delta = (1-1)/3 = 0 — even though the means
    differ. A mean-based effect size would report a difference here.
    """
    assert cliffs_delta([1.0, 2.0, 3.0], [2.0]) == 0.0


def test_delta_handles_partial_overlap():
    """a=[1,2,3,4], b=[3]. Below: 1,2 -> lt=2. Above: 4 -> gt=1. Tie: 3.

    delta = (1 - 2) / 4 = -0.25.
    """
    assert cliffs_delta([1.0, 2.0, 3.0, 4.0], [3.0]) == -0.25


def test_empty_input_is_zero_not_a_crash():
    assert cliffs_delta([], [1.0]) == 0.0


# --- perm_test, from its definition ----------------------------------------

def test_observed_difference_is_the_difference_of_medians():
    """medians 2 and 20 -> obs = -18, regardless of the p-value."""
    obs, _ = perm_test([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], 50, random.Random(1))

    assert obs == -18.0


def test_identical_groups_are_not_significant():
    """No relabelling can produce a gap smaller than zero, so every
    permutation counts as a hit and p reaches its maximum of 1.0."""
    _, p = perm_test([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], 100, random.Random(1))

    assert p == 1.0


def test_p_value_can_never_be_zero():
    """(hits+1)/(n_perm+1) has a floor of 1/(n_perm+1) by construction.

    A finite permutation test cannot observe p = 0, and reporting one would
    overstate the evidence beyond what the number of permutations can support.
    The floor must hold for any input, including maximally separated groups.
    """
    for a, b in (([100.0] * 8, [-100.0] * 8),
                 ([1.0, 2.0, 3.0, 4.0], [500.0, 600.0, 700.0, 800.0])):
        _, p = perm_test(a, b, 99, random.Random(1))

        assert p >= 1 / 100
        assert p > 0


def test_separated_groups_are_more_significant_than_overlapping_ones():
    rng = random.Random(4)
    _, p_far = perm_test([10.0, 11.0, 12.0, 13.0], [90.0, 91.0, 92.0, 93.0],
                         400, rng)
    _, p_near = perm_test([10.0, 50.0, 12.0, 90.0], [11.0, 49.0, 13.0, 91.0],
                          400, rng)

    assert p_far < p_near


# --- the features must not encode outcome ----------------------------------

def rec(trade_id, ask, bid, is_maker_ask, size, price, market=1, ts=None, **extra):
    out = {
        "trade_id": trade_id, "type": "trade", "market_id": market,
        "size": str(size), "price": str(price),
        "ask_account_id": ask, "bid_account_id": bid,
        "is_maker_ask": is_maker_ask,
        "transaction_time": (ts if ts is not None else trade_id) * 1_000_000,
    }
    out.update(extra)
    return out


def test_no_feature_reads_pnl():
    """The whole point is testing behaviour where outcome was unstable.

    Two accounts with identical trading and wildly different position state
    must be indistinguishable: if a feature moved here, it would be smuggling
    outcome back in and the stability comparison would answer nothing.
    """
    plain = [rec(1, ask=2, bid=1, is_maker_ask=True, size=5, price=100)]
    with_pnl_state = [rec(1, ask=2, bid=1, is_maker_ask=True, size=5, price=100,
                          taker_position_size_before="900",
                          taker_entry_quote_before="123456",
                          maker_position_size_before="-900",
                          maker_entry_quote_before="654321")]

    a = accumulate_behaviour(plain)
    b = accumulate_behaviour(with_pnl_state)

    for feature in ("maker_share", "fills_per_hour", "size_median", "size_cv",
                    "markets_traded", "flip_rate", "hour_concentration"):
        assert a[1].value(feature) == b[1].value(feature), feature


# --- individual features ----------------------------------------------------

def test_maker_share_follows_is_maker_ask():
    """is_maker_ask=True means the ask rested: account 2 makes, account 1 takes."""
    rows = accumulate_behaviour([rec(1, ask=2, bid=1, is_maker_ask=True,
                                     size=1, price=100)])

    assert rows[2].maker_share == 1.0
    assert rows[1].maker_share == 0.0


def test_size_cv_is_zero_for_a_fixed_clip():
    """A bot always trading the same size has no dispersion."""
    rows = accumulate_behaviour([
        rec(i, ask=2, bid=1, is_maker_ask=True, size=7, price=100)
        for i in range(1, 5)
    ])

    assert rows[1].size_cv == 0.0
    assert rows[1].size_median == 7.0


def test_size_cv_is_scale_free():
    """Sizes 1,2,3 and 100,200,300 have the same relative spread.

    Without normalising by the mean, an account trading whole BTC would always
    look more variable than one trading fractions.
    """
    small = accumulate_behaviour([
        rec(i, ask=2, bid=1, is_maker_ask=True, size=s, price=100)
        for i, s in enumerate([1, 2, 3], start=1)
    ])
    large = accumulate_behaviour([
        rec(i, ask=2, bid=1, is_maker_ask=True, size=s, price=100)
        for i, s in enumerate([100, 200, 300], start=1)
    ])

    assert abs(small[1].size_cv - large[1].size_cv) < 1e-12


def test_flip_rate_counts_the_venues_own_sign_change_flag():
    rows = accumulate_behaviour([
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_sign_changed=True),
        rec(2, ask=2, bid=1, is_maker_ask=True, size=1, price=100),
    ])

    # Account 1 is the taker on both fills; one carried the flag.
    assert rows[1].flip_rate == 0.5
    assert rows[2].flip_rate == 0.0


def test_markets_traded_counts_distinct_markets():
    rows = accumulate_behaviour([
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100, market=0),
        rec(2, ask=2, bid=1, is_maker_ask=True, size=1, price=100, market=24),
        rec(3, ask=2, bid=1, is_maker_ask=True, size=1, price=100, market=24),
    ])

    assert rows[1].markets_traded == 2


def test_hour_concentration_separates_bursty_from_steady():
    """Three fills in one hour and one in another: 3/4 in the busiest."""
    bursty = accumulate_behaviour([
        rec(i, ask=2, bid=1, is_maker_ask=True, size=1, price=100, ts=t)
        for i, t in enumerate([0, 60, 120, 7200], start=1)
    ])

    assert bursty[1].hour_concentration == 0.75


def test_fills_per_hour_uses_the_observed_span():
    """Four fills spread over exactly two hours = 2 per hour."""
    rows = accumulate_behaviour([
        rec(i, ask=2, bid=1, is_maker_ask=True, size=1, price=100, ts=t)
        for i, t in enumerate([0, 2400, 4800, 7200], start=1)
    ])

    assert abs(rows[1].fills_per_hour - 2.0) < 1e-9


# --- stability scoring ------------------------------------------------------

def prof(account_id: int, **kw) -> Behaviour:
    b = Behaviour(account_id=account_id)
    b.fills = kw.get("fills", 100)
    b.maker_fills = int(b.fills * kw.get("maker_share", 0.5))
    return b


def test_a_feature_that_repeats_beats_its_null():
    a = {i: prof(i, maker_share=i / 30) for i in range(30)}
    b = {i: prof(i, maker_share=i / 30) for i in range(30)}

    s = stability(a, b, "maker_share")

    assert s["rho"] > s["null_p95"]


def test_a_feature_that_is_reshuffled_does_not():
    a = {i: prof(i, maker_share=i / 30) for i in range(30)}
    b = {i: prof(i, maker_share=(29 - i) / 30) for i in range(30)}

    s = stability(a, b, "maker_share")

    assert s["rho"] < 0                       # inverted, not stable
    assert s["rho"] < s["null_p95"]


def test_too_few_common_accounts_is_not_a_measurement():
    s = stability({1: prof(1)}, {1: prof(1)}, "maker_share")

    assert s["n"] == 1
    assert math.isnan(s["rho"])
