"""Tick <-> price/size conversions for the live track.

Every expected value below is derived BY HAND from the market's decimals,
never read back from the code (project convention, see CONTEXT.md
Anti-patterns: "Tests that verify the code against itself").

Live decimals, read from /api/v1/orderBooks on 2026-07-17:
    ETH (market 0): price 2, size 4
    BTC (market 1): price 1, size 5
    SOL (market 2): price 3, size 3
"""

import pytest

from src.live.connect_testnet import _str_to_ticks

ETH_PRICE_DEC, ETH_SIZE_DEC = 2, 4
BTC_PRICE_DEC, BTC_SIZE_DEC = 1, 5
SOL_PRICE_DEC, SOL_SIZE_DEC = 3, 3


def _price_to_ticks(price: float, decimals: int) -> int:
    """Mirror of panel.py's _price_to_ticks with decimals passed in.

    panel.py is a Streamlit script and cannot be imported (its module level
    calls st.*), so the arithmetic under test is restated here. If one
    changes, the other must — that duplication is the reason these tests
    also pin _str_to_ticks, which IS imported from the real module.
    """
    return round(price * (10**decimals))


def _size_to_ticks(size: float, decimals: int) -> int:
    return round(size * (10**decimals))


# ── the truncation bug these fixes exist for ──────────────────────────────
#
# int() on a scaled float truncates, because the float lands just below the
# integer. Worked out by hand from IEEE-754 double representation:
#   0.29 is not exactly representable; 0.29 * 100 == 28.999999999999996
#   int(28.999999999999996) == 28   <- one tick short
#   round(28.999999999999996) == 29 <- correct
# A close of 0.29 therefore went out as 0.28 and left dust behind.


@pytest.mark.parametrize(
    "size, decimals, expected",
    [
        # 0.29 ETH at 4 decimals: 0.29 * 10_000 = 2_900 exactly.
        (0.29, ETH_SIZE_DEC, 2_900),
        # 0.29 at 2 decimals is the reported failure: hand-derived as 29.
        (0.29, 2, 29),
        # 1.15 at 2 decimals: 115. int() gives 114.
        (1.15, 2, 115),
        # 8.87 at 2 decimals: 887. int() gives 886.
        (8.87, 2, 887),
        # SOL position from account 306 on 2026-07-17: 771.278 at 3 decimals
        # = 771_278 ticks. With the panel's old hardcoded 4 it would have
        # been 7_712_780 — the 10x bug.
        (771.278, SOL_SIZE_DEC, 771_278),
        # BTC at 5 decimals: 1.57063 * 100_000 = 157_063.
        (1.57063, BTC_SIZE_DEC, 157_063),
        (0.0, ETH_SIZE_DEC, 0),
    ],
)
def test_size_to_ticks_hand_derived(size, decimals, expected):
    assert _size_to_ticks(size, decimals) == expected


@pytest.mark.parametrize(
    "price, decimals, expected",
    [
        # ETH, 2 decimals: 1846.57 * 100 = 184_657.
        (1846.57, ETH_PRICE_DEC, 184_657),
        # BTC, 1 decimal: 64155.8 * 10 = 641_558.
        (64155.8, BTC_PRICE_DEC, 641_558),
        # SOL, 3 decimals: 75.789 * 1000 = 75_789. The panel's old hardcoded
        # 2 would have priced this at 7_578 — 1/10 of intended.
        (75.789, SOL_PRICE_DEC, 75_789),
        # A price whose scaling truncates: 8.87 * 100 = 887.
        (8.87, 2, 887),
    ],
)
def test_price_to_ticks_hand_derived(price, decimals, expected):
    assert _price_to_ticks(price, decimals) == expected


def test_int_would_truncate_where_round_does_not():
    """Pin the bug itself, so a revert to int() fails loudly here."""
    # Hand-derived: each of these floats lands just under the integer.
    for value, decimals, correct in ((0.29, 2, 29), (1.15, 2, 115), (8.87, 2, 887)):
        scaled = value * (10**decimals)
        assert int(scaled) == correct - 1, "premise: int() truncates by one"
        assert round(scaled) == correct, "round() is the fix"


# ── parsing API decimal strings ───────────────────────────────────────────


@pytest.mark.parametrize(
    "text, decimals, expected",
    [
        # Zero-padded, as the exchange sends today (322/322 values checked
        # live on 2026-07-17).
        ("1846.57", ETH_PRICE_DEC, 184_657),
        ("63213.0", BTC_PRICE_DEC, 632_130),
        ("75.789", SOL_PRICE_DEC, 75_789),
        ("12.63290", BTC_SIZE_DEC, 1_263_290),
        # NOT padded. The old int(s.replace(".", "")) gives 18_465 here —
        # a tenth of the real price — because it depends on the exchange
        # always padding, which is an undocumented courtesy, not a contract.
        ("1846.5", ETH_PRICE_DEC, 184_650),
        # No decimal point at all: replace() would give 1_846.
        ("1846", ETH_PRICE_DEC, 184_600),
        ("0.0100", ETH_SIZE_DEC, 100),
    ],
)
def test_str_to_ticks_hand_derived(text, decimals, expected):
    assert _str_to_ticks(text, decimals) == expected


def test_str_to_ticks_does_not_depend_on_padding():
    """The same value, padded and not, must parse identically."""
    # 1846.5 == 1846.50 == 184_650 ticks at 2 decimals. Hand-derived.
    assert _str_to_ticks("1846.5", 2) == _str_to_ticks("1846.50", 2) == 184_650
    # The old approach disagreed with itself on exactly this pair:
    assert int("1846.5".replace(".", "")) != int("1846.50".replace(".", ""))


def test_round_trip_ticks_to_price_and_back():
    """Hand-derived: 184_657 ticks at 2 decimals is $1846.57."""
    ticks = 184_657
    price = ticks / (10**ETH_PRICE_DEC)
    assert price == 1846.57
    assert _price_to_ticks(price, ETH_PRICE_DEC) == ticks
