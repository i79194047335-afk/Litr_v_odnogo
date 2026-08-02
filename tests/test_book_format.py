"""Tests for the compact order-book format.

The format exists to make a month of L2 capture fit on disk — 25x smaller than
raw JSON. That saving is worthless, and worse than worthless, if the data
cannot be turned back into book states: a month of unreadable capture looks
like an asset while being none.

So the load-bearing test is `test_compact_roundtrip_matches_raw_replay`: it
builds the book twice by independent paths — raw JSON straight to book, and
raw -> compact -> book — and requires the results to be identical. A bug that
only affects the compact path shows up there; a bug shared by both would not,
which is why the raw path deliberately does not reuse the compact decoder.

Semantics verified against a live capture of `order_book/1` (2026-08-02, 2210
messages): `size == 0` appears on 25.9% of level updates and means removal,
and `begin_nonce` of each frame equals the previous frame's `nonce`.
"""

from __future__ import annotations

from src.collector.book_format import (
    Frame,
    apply_frame,
    continuity_gaps,
    decode,
    encode_frame,
    frames_from_raw,
    new_book,
    replay,
)


def msg(kind, ts, nonce, begin, asks=(), bids=()):
    return {
        "type": kind, "timestamp": ts,
        "order_book": {
            "nonce": nonce, "begin_nonce": begin,
            "asks": [{"price": p, "size": s} for p, s in asks],
            "bids": [{"price": p, "size": s} for p, s in bids],
        },
    }


# --- the reversibility guarantee -------------------------------------------

def test_compact_roundtrip_matches_raw_replay():
    """Two independent paths to the same book must agree.

    Snapshot sets two levels a side; a delta then moves one, adds one, and
    deletes one. Worked out on paper, the final ask side is
    {100: 5, 102: 7} — 101 was deleted, 100 was overwritten from 3 to 5.
    """
    raw = [
        msg("subscribed/order_book", 1, 10, 0,
            asks=[("100", "3"), ("101", "4")], bids=[("99", "8")]),
        msg("update/order_book", 2, 11, 10,
            asks=[("100", "5"), ("101", "0"), ("102", "7")], bids=[("98", "2")]),
    ]

    direct = replay(frames_from_raw(raw))

    lines = []
    for seq, m in enumerate(raw):
        lines.extend(encode_frame(seq, m))
    round_tripped = replay(decode(lines))

    assert direct == round_tripped
    assert direct["a"] == {"100": "5", "102": "7"}
    assert direct["b"] == {"99": "8", "98": "2"}


def test_deletion_is_carried_through_the_format():
    """A level removed by size 0 must not reappear after a round trip.

    If the compact writer dropped zero-size lines as "empty", the level would
    survive and every downstream imbalance figure would be wrong.
    """
    raw = [
        msg("subscribed/order_book", 1, 10, 0, asks=[("100", "3")]),
        msg("update/order_book", 2, 11, 10, asks=[("100", "0")]),
    ]

    lines = []
    for seq, m in enumerate(raw):
        lines.extend(encode_frame(seq, m))

    assert replay(decode(lines))["a"] == {}
    assert any(line.endswith(",0") for line in lines)


def test_zero_written_with_decimals_still_deletes():
    """The venue may send '0.00000'; text comparison against '0' would miss it."""
    book = new_book()
    apply_frame(book, Frame(0, 1, 1, 0, True, asks=[("100", "3")]))
    apply_frame(book, Frame(1, 2, 2, 1, False, asks=[("100", "0.00000")]))

    assert book["a"] == {}


def test_snapshot_clears_rather_than_merges():
    """A resubscribe mid-file must reset the book.

    Merging instead would stack a second book on the first and leave stale
    levels that never trade — invisible corruption rather than a crash.
    """
    book = new_book()
    apply_frame(book, Frame(0, 1, 1, 0, True, asks=[("100", "3"), ("101", "4")]))
    apply_frame(book, Frame(1, 2, 2, 1, True, asks=[("200", "9")]))

    assert book["a"] == {"200": "9"}


def test_prices_and_sizes_survive_as_exact_strings():
    """Venue decimals must not be routed through float.

    0.1 + 0.2 style drift on a price key would split one level into two.
    """
    raw = [msg("subscribed/order_book", 1, 10, 0,
               asks=[("62797.40", "0.00048"), ("0.000000001", "12345678.87654321")])]

    lines = encode_frame(0, raw[0])
    book = replay(decode(lines))

    assert book["a"]["62797.40"] == "0.00048"
    assert book["a"]["0.000000001"] == "12345678.87654321"


def test_multi_frame_sequence_replays_in_order():
    """Later frames must win. Reversing the order would give 3, not 7."""
    raw = [
        msg("subscribed/order_book", 1, 10, 0, asks=[("100", "3")]),
        msg("update/order_book", 2, 11, 10, asks=[("100", "5")]),
        msg("update/order_book", 3, 12, 11, asks=[("100", "7")]),
    ]

    lines = []
    for seq, m in enumerate(raw):
        lines.extend(encode_frame(seq, m))

    assert replay(decode(lines))["a"] == {"100": "7"}


# --- frames that carry no levels still matter ------------------------------

def test_empty_frame_keeps_its_place_in_the_nonce_chain():
    """A quiet frame emits a header so continuity stays checkable.

    Dropping it would make a real capture gap indistinguishable from a quiet
    book — the `liquidation_trades` failure in another costume.
    """
    lines = encode_frame(5, msg("update/order_book", 9, 42, 41))

    assert len(lines) == 1
    frames = decode(lines)
    assert frames[0].nonce == 42
    assert frames[0].begin_nonce == 41
    assert frames[0].asks == [] and frames[0].bids == []


def test_non_book_messages_are_skipped():
    assert encode_frame(0, {"type": "connected"}) == []
    assert encode_frame(0, {"type": "ping"}) == []


# --- continuity checking ----------------------------------------------------

def test_unbroken_chain_reports_no_gaps():
    """begin_nonce of each frame equals the previous frame's nonce."""
    frames = [Frame(0, 1, 100, 0, True), Frame(1, 2, 200, 100, False),
              Frame(2, 3, 300, 200, False)]

    assert continuity_gaps(frames) == []


def test_a_missing_frame_is_detected():
    """Frame 2 claims to follow nonce 200, but the last seen was 100."""
    frames = [Frame(0, 1, 100, 0, True), Frame(1, 2, 300, 200, False)]

    gaps = continuity_gaps(frames)

    assert gaps == [(1, 100, 200)]


def test_orphan_level_line_does_not_corrupt_the_book():
    """A level line whose header is missing is skipped, not guessed at."""
    lines = ["0,1,10,0,S", "0,a,100,3", "99,a,500,9"]

    book = replay(decode(lines))

    assert book["a"] == {"100": "3"}
