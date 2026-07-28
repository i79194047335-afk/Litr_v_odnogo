"""Tests for the participant-mining collector (src/collector/lighter_participants.py).

Two things here are worth more than the rest.

First: conditional fields must never be invented. `taker_position_sign_changed`
arrives on ~28% of trades and is `True` on every trade that carries it (measured
2026-07-28 over 401 and 353 live trades), so absence means False. A collector
that writes a default would turn "not stated" into "stated as False" for three
quarters of the tape, and nothing downstream could tell the difference
afterwards — collection is forward-only.

Second: append-not-truncate. A restart mid-day must extend the day's file. Get
that wrong and every deploy silently eats the hours already collected.
"""

import gzip
import json

import pytest

from src.collector.lighter_participants import (
    DayWriter, Deduper, _day_path, _normalize, read_records,
)


def wire_trade(**over):
    """A trade shaped like the real wire records captured 2026-07-28."""
    t = {
        "trade_id": 26253668583,
        "trade_id_str": "26253668583",
        "tx_hash": "00" * 40,
        "type": "trade",
        "market_id": 1,
        "size": "0.03460",
        "price": "63368.1",
        "usd_amount": "2192.53",
        "ask_id": 562952994447057,
        "ask_id_str": "562952994447057",
        "bid_id": 844421842160545,
        "bid_id_str": "844421842160545",
        "ask_client_id": 146095177394212,
        "ask_client_id_str": "146095177394212",
        "bid_client_id": 1785233908634,
        "bid_client_id_str": "1785233908634",
        "ask_account_id": 726714,
        "bid_account_id": 732643,
        "is_maker_ask": True,
        "block_height": 301540770,
        "timestamp": 1785233908902,
        "taker_position_size_before": "-4.23751",
        "taker_entry_quote_before": "269771.175264",
        "taker_initial_margin_fraction_before": 500,
        "maker_fee": 28,
        "maker_position_size_before": "0.60700",
        "maker_entry_quote_before": "38420.364869",
    }
    t.update(over)
    return t


# --- conditional fields: the measured lesson -------------------------------

def test_absent_sign_changed_is_not_invented():
    """Absent means False on the wire. Writing a default would make three
    quarters of the tape claim something the exchange never said."""
    rec = _normalize(1, wire_trade(), "trade", keep_all=False)
    assert "taker_position_sign_changed" not in rec
    assert "maker_position_sign_changed" not in rec


def test_present_sign_changed_is_preserved():
    rec = _normalize(1, wire_trade(taker_position_sign_changed=True),
                     "trade", keep_all=False)
    assert rec["taker_position_sign_changed"] is True


def test_conditional_fee_fields_survive_when_present():
    rec = _normalize(1, wire_trade(taker_fee=50, integrator_taker_fee=150),
                     "trade", keep_all=False)
    assert rec["taker_fee"] == 50
    assert rec["integrator_taker_fee"] == 150


def test_unknown_future_fields_are_kept():
    """The exchange adds fields without telling us; a whitelist would silently
    drop them and forward-only collection makes that unrecoverable."""
    rec = _normalize(1, wire_trade(some_new_field="x"), "trade", keep_all=False)
    assert rec["some_new_field"] == "x"


# --- the size decision -----------------------------------------------------

def test_redundant_fields_are_dropped():
    rec = _normalize(1, wire_trade(), "trade", keep_all=False)
    for k in ("trade_id_str", "ask_id_str", "bid_id_str",
              "ask_client_id_str", "bid_client_id_str", "usd_amount", "tx_hash"):
        assert k not in rec, f"{k} should have been dropped"
    # the int twin of every dropped _str field is still there
    assert rec["trade_id"] == 26253668583
    assert rec["ask_client_id"] == 146095177394212


def test_keep_all_stores_the_wire_record_verbatim():
    raw = wire_trade()
    rec = _normalize(1, raw, "trade", keep_all=True)
    assert rec == raw


# --- records that must not be stored ---------------------------------------

@pytest.mark.parametrize("missing", [
    "trade_id", "timestamp", "price", "size",
    "ask_account_id", "bid_account_id", "is_maker_ask",
])
def test_record_missing_a_required_field_is_dropped(missing):
    t = wire_trade()
    del t[missing]
    assert _normalize(1, t, "trade", keep_all=False) is None


def test_non_bool_is_maker_ask_is_dropped():
    """Same rule the tick collector already enforces: without it the taker side
    would be a guess, and a guess is worse than a gap."""
    assert _normalize(1, wire_trade(is_maker_ask="true"), "trade", keep_all=False) is None
    assert _normalize(1, wire_trade(is_maker_ask=None), "trade", keep_all=False) is None


# --- liquidations ----------------------------------------------------------

def test_liquidation_is_marked():
    rec = _normalize(1, wire_trade(type="liquidation"), "liq", keep_all=False)
    assert rec["kind"] == "liq"


def test_regular_trade_carries_no_kind_marker():
    """type='trade' already says it; a constant second field on 99% of records
    is pure bytes."""
    rec = _normalize(1, wire_trade(), "trade", keep_all=False)
    assert "kind" not in rec


# --- storage ---------------------------------------------------------------

def test_writer_round_trips_through_gzip(tmp_path):
    w = DayWriter(tmp_path)
    recs = [_normalize(1, wire_trade(trade_id=i), "trade", keep_all=False)
            for i in range(5)]
    for r in recs:
        w.write(1, r)
    w.close()

    path = _day_path(tmp_path, 1)
    with gzip.open(path, "rt") as f:
        back = [json.loads(line) for line in f]
    assert back == recs


def test_restart_appends_rather_than_truncating(tmp_path):
    """A redeploy mid-day must not eat the hours already on disk."""
    first = DayWriter(tmp_path)
    first.write(1, _normalize(1, wire_trade(trade_id=1), "trade", keep_all=False))
    first.close()

    second = DayWriter(tmp_path)
    second.write(1, _normalize(1, wire_trade(trade_id=2), "trade", keep_all=False))
    second.close()

    with gzip.open(_day_path(tmp_path, 1), "rt") as f:
        ids = [json.loads(line)["trade_id"] for line in f]
    assert ids == [1, 2]


def test_writer_keeps_markets_in_separate_files(tmp_path):
    w = DayWriter(tmp_path)
    w.write(1, _normalize(1, wire_trade(trade_id=11), "trade", keep_all=False))
    w.write(2, _normalize(2, wire_trade(trade_id=22, market_id=2), "trade", keep_all=False))
    w.close()
    assert _day_path(tmp_path, 1).exists()
    assert _day_path(tmp_path, 2).exists()
    with gzip.open(_day_path(tmp_path, 2), "rt") as f:
        assert json.loads(f.readline())["trade_id"] == 22


def test_write_reports_uncompressed_bytes(tmp_path):
    w = DayWriter(tmp_path)
    n = w.write(1, _normalize(1, wire_trade(), "trade", keep_all=False))
    w.close()
    assert n > 100  # the counter tracks pre-compression volume, for capacity logs


# --- reading a file that is still being written ----------------------------
#
# A gzip member gets its CRC trailer only at close, so the day currently being
# collected raises EOFError under a plain gzip.open even though every flushed
# byte is on disk. Downstream must be able to read today's data without stopping
# the service, and the same tolerance covers a SIGKILL, which no flush interval
# can protect against.

def test_read_records_reads_a_closed_file(tmp_path):
    w = DayWriter(tmp_path)
    for i in range(3):
        w.write(1, _normalize(1, wire_trade(trade_id=i), "trade", keep_all=False))
    w.close()
    got = list(read_records(_day_path(tmp_path, 1)))
    assert [r["trade_id"] for r in got] == [0, 1, 2]


def test_read_records_reads_a_file_still_open_for_writing(tmp_path):
    w = DayWriter(tmp_path)
    for i in range(5):
        w.write(1, _normalize(1, wire_trade(trade_id=i), "trade", keep_all=False))
    w._handles[1].flush()  # flushed but NOT closed — the live-service case
    try:
        got = list(read_records(_day_path(tmp_path, 1)))
        assert [r["trade_id"] for r in got] == [0, 1, 2, 3, 4]
    finally:
        w.close()


@pytest.mark.parametrize("chop, at_least", [
    # Only the CRC/size trailer is gone — every record is still recoverable,
    # which is the common case after a SIGKILL and worth pinning as such.
    (12, 4),
    # Deep into the compressed payload: some records are unrecoverable, but the
    # ones that come back must still be whole.
    (120, 0),
])
def test_read_records_survives_a_truncated_tail(tmp_path, chop, at_least):
    """What a SIGKILL leaves behind: no exception, and no half-parsed records."""
    w = DayWriter(tmp_path)
    for i in range(4):
        w.write(1, _normalize(1, wire_trade(trade_id=i), "trade", keep_all=False))
    w.close()

    path = _day_path(tmp_path, 1)
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) - chop])

    got = list(read_records(path))          # must not raise
    assert at_least <= len(got) <= 4
    assert all("trade_id" in r and "timestamp" in r for r in got)


def test_read_records_drops_a_half_written_final_line(tmp_path):
    import gzip as _gzip
    path = tmp_path / "partial.jsonl.gz"
    with _gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"trade_id": 1}) + "\n")
        f.write('{"trade_id": 2, "pri')  # writer died mid-line
    got = list(read_records(path))
    assert got == [{"trade_id": 1}]


# --- dedup -----------------------------------------------------------------

def test_deduper_rejects_a_repeat():
    d = Deduper(max_size=10)
    assert d.is_new(7) is True
    assert d.is_new(7) is False


def test_deduper_window_is_bounded_and_evicts_oldest():
    d = Deduper(max_size=3)
    for tid in (1, 2, 3):
        assert d.is_new(tid)
    d.is_new(4)                 # evicts 1
    assert d.is_new(1) is True  # forgotten, so it looks new again
    assert d.is_new(3) is False # still inside the window
