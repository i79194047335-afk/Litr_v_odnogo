"""Tests for per-account realised PnL reconstruction.

Every expected number is worked out on paper from position-accounting rules
before being written down, never read off a run.

The load-bearing test is `test_closed_round_trip_between_two_accounts_nets_to_zero`.
Derivatives are zero-sum gross of fees: if one account made 10 on a fill, its
counterparty lost 10. A reconstruction that quietly credited both sides, or
lost the sign on one, would still produce a plausible-looking leaderboard —
and nothing else in this module would notice.
"""

from __future__ import annotations

from src.analysis.participant_pnl import accumulate_pnl


def rec(trade_id, ask, bid, is_maker_ask, size, price, market=1, ttime=None, **before):
    out = {
        "trade_id": trade_id, "type": "trade", "market_id": market,
        "size": str(size), "price": str(price),
        "ask_account_id": ask, "bid_account_id": bid,
        "is_maker_ask": is_maker_ask,
        "transaction_time": ttime if ttime is not None else trade_id,
    }
    out.update({k: str(v) for k, v in before.items()})
    return out


# --- the zero-sum invariant ------------------------------------------------

def test_closed_round_trip_between_two_accounts_nets_to_zero():
    """A buys 1 @ 100 from B, then sells 1 @ 110 back to B.

    A: long from 100, closed at 110 -> +10.
    B: short from 100, closed at 110 -> -10.
    Gross of fees the two must cancel exactly.
    """
    records = [
        # A (bid, taker) buys from B (ask, maker). Both start flat.
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        # A sells back at 110. A is now the ask and crosses; B rests on the bid.
        # A holds +1 basis 100; B holds -1 basis 100.
        rec(2, ask=1, bid=2, is_maker_ask=False, size=1, price=110,
            taker_position_size_before=1.0, taker_entry_quote_before=100.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].realised == 10.0
    assert rows[2].realised == -10.0
    assert rows[1].realised + rows[2].realised == 0.0


def test_opening_fill_realises_nothing_for_either_side():
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].realised == 0.0
    assert rows[2].realised == 0.0
    assert rows[1].closing_fills == 0


def test_unrealised_movement_is_not_counted_as_pnl():
    """Holding through a price move realises nothing — the sum stays zero.

    A buys 1 @ 100 from B, then buys 1 more @ 150 from B. Both sides are only
    adding to positions; nobody has closed anything, so realised PnL is 0 for
    both even though the price moved 50%. A module that marked open positions
    to market would report +50 / -50 here.

    This is why the daily total does not net to zero on real data: the tape's
    open interest is carried, not closed, and the imbalance is inventory
    rather than a bookkeeping error.
    """
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        rec(2, ask=2, bid=1, is_maker_ask=True, size=1, price=150,
            taker_position_size_before=1.0, taker_entry_quote_before=100.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].realised == 0.0
    assert rows[2].realised == 0.0
    assert rows[1].open_size == 2.0          # holding, not winning
    assert rows[2].open_size == -2.0


# --- position carried in from before the tape started ----------------------

def test_position_opened_before_collection_uses_the_venues_basis():
    """An account's first appearance can already carry a position.

    Account 1 shows up holding +5 at basis 400 (entry 80) — opened before we
    were watching. It sells 5 @ 100, realising 5 * (100 - 80) = +100 against
    the venue's basis, which is the correct figure even though we never saw
    the opening trades.
    """
    records = [
        rec(1, ask=1, bid=2, is_maker_ask=False, size=5, price=100,
            taker_position_size_before=5.0, taker_entry_quote_before=400.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].realised == 100.0
    assert rows[1].open_size == 0.0


# --- anchoring on the venue, not on our own carry --------------------------

def test_a_tape_gap_costs_only_the_missed_fills():
    """State re-anchors on each reported reading, so a gap does not poison later PnL.

    Account 1 buys 1 @ 100 (flat -> +1). Then the tape jumps: the next record
    reports it holding +10 at basis 900 (entry 90) — fills we never saw. It
    sells 10 @ 100, realising 10 * (100 - 90) = +100 against the reported
    basis. A module carrying its own +1 forward would compute a flip here and
    report something else entirely.
    """
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        rec(2, ask=1, bid=2, is_maker_ask=False, size=10, price=100,
            taker_position_size_before=10.0, taker_entry_quote_before=900.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].realised == 100.0


# --- ordering ---------------------------------------------------------------

def test_records_are_processed_in_time_order():
    """The tape stores fills newest-first within a frame (API_DIGEST.md).

    Three fills for account 1: buy 1 @ 100 (flat -> +1), buy 1 @ 100 (-> +2),
    then sell 2 @ 120 closing it for 2 * (120 - 100) = +40 and leaving nothing
    open. Handed the reversed order, the module must sort and reach the same
    end state.

    The discriminating assertion is `open_size`. Because every fill re-anchors
    on the venue's reported state, realised PnL alone can survive a shuffle —
    but the final open position cannot: it is whatever the *last* processed
    fill left behind. Unsorted, the last fill is the opening buy and the
    account is left holding +1.
    """
    a = rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100, ttime=10,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0)
    b = rec(2, ask=2, bid=1, is_maker_ask=True, size=1, price=100, ttime=20,
            taker_position_size_before=1.0, taker_entry_quote_before=100.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0)
    c = rec(3, ask=1, bid=2, is_maker_ask=False, size=2, price=120, ttime=30,
            taker_position_size_before=2.0, taker_entry_quote_before=200.0,
            maker_position_size_before=-2.0, maker_entry_quote_before=200.0)

    rows = accumulate_pnl([c, b, a])                # as written to disk

    assert rows[1].realised == 40.0
    assert rows[2].realised == -40.0
    assert rows[1].open_size == 0.0                 # fails at +1.0 if unsorted
    assert rows[2].open_size == 0.0


# --- markets stay separate --------------------------------------------------

def test_positions_do_not_leak_between_markets():
    """The same account long on market 1 and short on market 2 keeps both bases.

    Account 1 buys 1 @ 100 on market 1, then sells 1 @ 50 on market 2 from
    flat. The second fill opens a short — it must not be treated as closing
    the market-1 long, which would invent a realised loss of 50.
    """
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100, market=1,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        rec(2, ask=1, bid=2, is_maker_ask=False, size=1, price=50, market=2,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].realised == 0.0
    assert rows[1].markets == {1, 2}


# --- legs without a venue reading are skipped, not guessed ------------------

def test_leg_without_reported_state_is_skipped_rather_than_invented():
    """No anchor means no PnL. Guessing a basis would fabricate money."""
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0),
        # maker_* fields absent entirely
    ]

    rows = accumulate_pnl(records)

    assert 1 in rows                  # the taker had a reading
    assert 2 not in rows              # the maker did not, and was not invented


# --- bookkeeping fields -----------------------------------------------------

def test_liquidation_fills_are_counted_on_both_sides():
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]
    records[0]["type"] = "liquidation"

    rows = accumulate_pnl(records)

    assert rows[1].liquidation_fills == 1
    assert rows[2].liquidation_fills == 1


def test_maker_share_and_notional_are_tracked_per_role():
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=2, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
    ]

    rows = accumulate_pnl(records)

    # is_maker_ask=True -> account 2 rested, account 1 crossed.
    assert rows[2].maker_share == 1.0
    assert rows[1].maker_share == 0.0
    assert rows[1].notional == 200.0


def test_pnl_per_notional_is_expressed_in_basis_points():
    """+10 realised on 210 of traded notional = 476.19 bps."""
    records = [
        rec(1, ask=2, bid=1, is_maker_ask=True, size=1, price=100,
            taker_position_size_before=0.0, taker_entry_quote_before=0.0,
            maker_position_size_before=0.0, maker_entry_quote_before=0.0),
        rec(2, ask=1, bid=2, is_maker_ask=False, size=1, price=110,
            taker_position_size_before=1.0, taker_entry_quote_before=100.0,
            maker_position_size_before=-1.0, maker_entry_quote_before=100.0),
    ]

    rows = accumulate_pnl(records)

    assert rows[1].notional == 210.0
    assert abs(rows[1].pnl_per_notional_bps - 1e4 * 10.0 / 210.0) < 1e-9
