"""Tests for participant tape aggregation.

Every expectation here is derived on paper from the field semantics documented
in `docs/START_participant_mining.md` and the Lighter WS reference — never read
back off the implementation. A test that agrees with whatever the code does is
worse than no test.

Field semantics being relied on:
  - each record is ONE fill with two sides: `ask_account_id` and `bid_account_id`;
  - `is_maker_ask` says which side rested. If true the ask was the maker and the
    bid was the taker; if false, the reverse;
  - `size` is in base units, `price` in quote units, both as decimal strings;
  - `*_position_size_before` is the signed position that side held BEFORE this
    fill — negative is short;
  - `maker_fee` is in basis-point-hundredths (a rate, not an amount).
"""

import pytest

from src.analysis.participant_stats import (
    Sides,
    accumulate,
    notional,
    sides_of,
)


def rec(**kw):
    """A minimal well-formed fill; individual tests override what they exercise."""
    base = {
        "trade_id": 1,
        "type": "trade",
        "market_id": 1,
        "size": "1",
        "price": "100",
        "ask_account_id": 10,
        "bid_account_id": 20,
        "is_maker_ask": True,
        "timestamp": 1785455999870,
        "taker_position_size_before": "0",
        "maker_position_size_before": "0",
        "maker_fee": 0,
    }
    base.update(kw)
    return base


class TestSides:
    """Who was maker and who was taker, per is_maker_ask."""

    def test_ask_is_maker(self):
        # is_maker_ask=True -> the resting order was the ask (account 10),
        # so account 20 crossed the spread.
        s = sides_of(rec(is_maker_ask=True, ask_account_id=10, bid_account_id=20))
        assert s == Sides(maker=10, taker=20, taker_is_buyer=True)

    def test_bid_is_maker(self):
        # is_maker_ask=False -> the bid rested (account 20), the ask crossed.
        s = sides_of(rec(is_maker_ask=False, ask_account_id=10, bid_account_id=20))
        assert s == Sides(maker=20, taker=10, taker_is_buyer=False)

    def test_taker_direction_is_opposite_of_maker_side(self):
        # The taker buys exactly when the maker was the ask. Stated separately
        # because a sign error here silently inverts every flow figure.
        assert sides_of(rec(is_maker_ask=True)).taker_is_buyer is True
        assert sides_of(rec(is_maker_ask=False)).taker_is_buyer is False


class TestNotional:
    """size * price, computed on paper."""

    def test_simple(self):
        assert notional(rec(size="2", price="50")) == pytest.approx(100.0)

    def test_fractional(self):
        # 0.00021 * 64758.5 = 13.599285 exactly.
        assert notional(rec(size="0.00021", price="64758.5")) == pytest.approx(13.599285)

    def test_zero_size(self):
        assert notional(rec(size="0", price="64758.5")) == 0.0


class TestAccumulate:
    """Per-account rollup over a stream of fills."""

    def test_single_fill_credits_both_sides(self):
        # One fill of 1 @ 100 = 100 notional. Account 10 made it, 20 took it.
        stats = accumulate([rec(size="1", price="100", ask_account_id=10,
                                bid_account_id=20, is_maker_ask=True)])
        assert stats[10].maker_fills == 1
        assert stats[10].taker_fills == 0
        assert stats[10].maker_notional == pytest.approx(100.0)
        assert stats[20].taker_fills == 1
        assert stats[20].maker_fills == 0
        assert stats[20].taker_notional == pytest.approx(100.0)

    def test_same_account_both_roles(self):
        # An account that makes once and takes once must show 1 and 1 —
        # not 2 of either, and not be split into two entries.
        stats = accumulate([
            rec(ask_account_id=7, bid_account_id=8, is_maker_ask=True),   # 7 makes
            rec(ask_account_id=9, bid_account_id=7, is_maker_ask=True),   # 7 takes
        ])
        assert (stats[7].maker_fills, stats[7].taker_fills) == (1, 1)

    def test_notional_sums_across_fills(self):
        # 1@100 + 3@100 = 400 taker notional for account 20.
        stats = accumulate([
            rec(size="1", price="100", bid_account_id=20, is_maker_ask=True),
            rec(size="3", price="100", bid_account_id=20, is_maker_ask=True),
        ])
        assert stats[20].taker_notional == pytest.approx(400.0)

    def test_liquidation_counted_separately(self):
        # A liquidation is still a fill and still counts toward volume; it is
        # additionally tallied on its own so the two questions stay separable.
        stats = accumulate([
            rec(type="liquidation", size="1", price="100",
                ask_account_id=10, bid_account_id=20, is_maker_ask=True),
        ])
        assert stats[20].taker_fills == 1
        assert stats[20].liquidations == 1
        assert stats[10].liquidations == 1

    def test_plain_trade_is_not_a_liquidation(self):
        stats = accumulate([rec(type="trade")])
        assert all(s.liquidations == 0 for s in stats.values())

    def test_empty_stream(self):
        assert accumulate([]) == {}

    def test_self_trade_counts_once_per_role(self):
        # Same account on both sides: it is simultaneously maker and taker.
        # Whatever the venue's stance on wash trading, the arithmetic must not
        # silently double a single fill into two.
        stats = accumulate([rec(ask_account_id=5, bid_account_id=5)])
        assert stats[5].maker_fills == 1
        assert stats[5].taker_fills == 1
        assert stats[5].fills == 2  # it participated in both roles


class TestPositionBefore:
    """Directional state carried in the record, used to spot one-way accounts."""

    def test_short_position_is_negative(self):
        stats = accumulate([
            rec(is_maker_ask=True, ask_account_id=10, bid_account_id=20,
                maker_position_size_before="-1.73613"),
        ])
        # The maker (10) was short before this fill.
        assert stats[10].last_position < 0

    def test_flat_position(self):
        stats = accumulate([rec(maker_position_size_before="0")])
        assert stats[10].last_position == 0.0
