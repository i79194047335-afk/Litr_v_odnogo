"""Tests for the PnL reconstruction probe.

Every expected number below is worked out on paper from position-accounting
rules, then written down. None was read off a run of the code.

The suite's real job is the last section: a probe that scores its own
arithmetic against itself would report a perfect match on any formula, and
would have said "H-003 holds" regardless of the truth. Those tests fail if
the check ever degenerates into trivial agreement.
"""

from __future__ import annotations

from src.analysis.pnl_probe import (
    Position,
    apply_fill,
    close_enough,
    in_time_order,
    probe,
)


# --- opening and adding: nothing is realised -------------------------------

def test_open_from_flat_sets_basis_and_realises_nothing():
    # Buy 2 @ 100 from flat: size +2, basis 2*100 = 200, realised 0.
    pos, realised = apply_fill(Position(), signed_qty=2.0, price=100.0)

    assert pos.size == 2.0
    assert pos.entry_quote == 200.0
    assert realised == 0.0


def test_adding_to_a_long_averages_the_basis():
    # Hold +2 @ basis 200 (entry 100). Buy 2 more @ 120 -> basis 200 + 240 = 440,
    # size 4, entry price 110. Nothing realised on an add.
    pos, realised = apply_fill(Position(2.0, 200.0), signed_qty=2.0, price=120.0)

    assert pos.size == 4.0
    assert pos.entry_quote == 440.0
    assert pos.entry_price == 110.0
    assert realised == 0.0


def test_adding_to_a_short_grows_unsigned_basis():
    # Short -1 @ basis 50 (entry 50). Sell 1 more @ 70 -> size -2, basis 120.
    # Basis is a magnitude: it grows, it does not net toward zero.
    pos, realised = apply_fill(Position(-1.0, 50.0), signed_qty=-1.0, price=70.0)

    assert pos.size == -2.0
    assert pos.entry_quote == 120.0
    assert realised == 0.0


# --- closing: PnL appears, with the sign the direction implies -------------

def test_partial_close_of_a_long_realises_on_the_closed_part_only():
    # Hold +4 @ entry 110 (basis 440). Sell 1 @ 130.
    # Realised = 1 * (130 - 110) = +20. Remaining +3 keeps entry 110 -> basis 330.
    pos, realised = apply_fill(Position(4.0, 440.0), signed_qty=-1.0, price=130.0)

    assert realised == 20.0
    assert pos.size == 3.0
    assert pos.entry_quote == 330.0
    assert pos.entry_price == 110.0


def test_short_closed_below_entry_is_a_gain():
    # Short -2 @ entry 60 (basis 120). Buy 2 @ 50 closes it.
    # A short gains when price falls: 2 * (50 - 60) * (-1) = +20.
    pos, realised = apply_fill(Position(-2.0, 120.0), signed_qty=2.0, price=50.0)

    assert realised == 20.0
    assert pos.size == 0.0
    assert pos.entry_quote == 0.0


def test_long_closed_below_entry_is_a_loss():
    # Hold +2 @ entry 100. Sell 2 @ 90 -> 2 * (90 - 100) = -20.
    pos, realised = apply_fill(Position(2.0, 200.0), signed_qty=-2.0, price=90.0)

    assert realised == -20.0
    assert pos.size == 0.0


def test_flip_closes_old_position_and_opens_the_remainder_at_fill_price():
    # Hold +2 @ entry 100 (basis 200). Sell 5 @ 110.
    # Closes 2 -> realised 2 * (110 - 100) = +20.
    # Remaining 3 opens short at 110 -> size -3, basis 330.
    pos, realised = apply_fill(Position(2.0, 200.0), signed_qty=-5.0, price=110.0)

    assert realised == 20.0
    assert pos.size == -3.0
    assert pos.entry_quote == 330.0


# --- the probe must not agree with itself ----------------------------------

def rec(trade_id, ask, bid, is_maker_ask, size, price, **before):
    """One tape record, shaped like the real thing (numbers as strings)."""
    out = {
        "trade_id": trade_id, "type": "trade", "market_id": 1,
        "size": str(size), "price": str(price),
        "ask_account_id": ask, "bid_account_id": bid,
        "is_maker_ask": is_maker_ask,
    }
    out.update({k: str(v) for k, v in before.items()})
    return out


def test_probe_scores_zero_when_the_venue_contradicts_the_arithmetic():
    """The decisive test: wrong reported state must show up as a failure.

    Account 7 buys 1 @ 100 from flat, so the next `before` should be +1 / 100.
    The tape is made to report +999 instead. A probe that checked its own
    output against its own output would score 100% here.
    """
    records = [
        rec(1, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        rec(2, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=999.0, taker_entry_quote_before=999.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0),
    ]

    result = probe(records)

    assert result.checks == 2          # both accounts predicted once
    assert result.both_ok == 1         # the maker agreed, the taker did not
    assert result.both_rate == 0.5


def test_probe_confirms_a_correct_continuation():
    """Same shape, but the tape reports what the rules predict."""
    records = [
        rec(1, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        # Taker bought 1 @ 100 -> +1 / 100. Maker sold 1 @ 100 -> -1 / 100.
        rec(2, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=1.0, taker_entry_quote_before=100.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0),
    ]

    result = probe(records)

    assert result.checks == 2
    assert result.both_ok == 2
    assert result.both_rate == 1.0


def test_probe_keeps_markets_separate():
    """One account trading two markets must not have its positions merged.

    Account 7 buys 1 @ 100 on market 1, then 1 @ 100 on market 2. Its market-2
    position is still flat when the second fill lands. Merging would predict
    +1 there and score a false failure — or, worse, a false success later.
    """
    a = rec(1, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0)
    b = rec(2, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0)
    b["market_id"] = 2
    c = rec(3, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=1.0, taker_entry_quote_before=100.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0)

    result = probe([a, b, c])

    # Only market 1 produces a second appearance, so only it is checked.
    assert result.checks == 2
    assert result.both_ok == 2


def test_probe_reports_nothing_when_no_account_repeats():
    """A single fill per account yields no prediction — and must not read as success."""
    records = [
        rec(1, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]

    result = probe(records)

    assert result.checks == 0
    assert result.both_rate == 0.0


def test_probe_reanchors_on_reported_state_rather_than_chaining_its_own():
    """One bad venue reading must cost one check, not every check after it.

    Account 7: flat, buys 1 @ 100. Tape then misreports +999 (one failure).
    From that reported +999, buying 1 more @ 100 predicts +1000 — and the tape
    says +1000, so the third check passes. If the probe chained its own
    prediction instead of re-anchoring, it would still expect +2 and fail here.
    """
    records = [
        rec(1, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        rec(2, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=999.0, taker_entry_quote_before=99900.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0),
        rec(3, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=1000.0, taker_entry_quote_before=100000.0,
            maker_position_size_before=-2.0, maker_entry_quote_before=200.0),
    ]

    result = probe(records)

    assert result.checks == 4          # two accounts, two later appearances each
    assert result.both_ok == 3         # only the injected contradiction fails
    assert len(result.first_failures) == 1


# --- the tape is not in time order -----------------------------------------

def test_reverse_ordered_frame_is_sorted_before_checking():
    """Fills inside a WS frame arrive newest-first; unsorted they score ~42%.

    Account 7 buys 1 @ 100 from flat, then 1 more @ 100, so its reported
    `before` values run 0 -> +1 -> +2. The records are handed to the probe in
    the order the collector wrote them (descending within the frame). Sorted,
    every prediction holds; unsorted, the sequence is nonsense.
    """
    first = rec(3, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
                taker_position_size_before=0.0, taker_entry_quote_before=0.0,
                maker_position_size_before=0.0, maker_entry_quote_before=0.0)
    second = rec(2, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
                 taker_position_size_before=1.0, taker_entry_quote_before=100.0,
                 maker_position_size_before=-1.0, maker_entry_quote_before=100.0)
    third = rec(1, ask=8, bid=7, is_maker_ask=True, size=1, price=100,
                taker_position_size_before=2.0, taker_entry_quote_before=200.0,
                maker_position_size_before=-2.0, maker_entry_quote_before=200.0)
    for r, t in ((first, 10), (second, 20), (third, 30)):
        r["transaction_time"] = t

    reversed_frame = [third, second, first]      # as written to disk

    assert [r["trade_id"] for r in in_time_order(reversed_frame)] == [3, 2, 1]

    result = probe(reversed_frame)
    assert result.checks == 4
    assert result.both_ok == 4

    # The same records left unsorted must NOT score perfectly — otherwise this
    # test would pass even with sorting removed.
    unsorted = probe(reversed_frame, assume_sorted=True)
    assert unsorted.both_ok < 4


# --- tolerance is a tolerance, not a blank cheque --------------------------

def test_tolerance_accepts_float_noise_but_not_real_differences():
    assert close_enough(0.87950, 0.8795000000001)
    assert not close_enough(0.87950, 0.87960)
