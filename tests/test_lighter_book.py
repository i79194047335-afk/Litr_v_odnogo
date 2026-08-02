"""Tests for the L2 book collector.

Collection is forward-only: a bug here is not a wrong number, it is data that
never existed. So these test the ways capture goes wrong *silently* — a
truncating restart, a dropped snapshot, an unparseable channel — rather than
the happy path, which shows up immediately as an empty directory.
"""

from __future__ import annotations

import gzip
import json

from src.collector.lighter_book import DayWriter, market_of


# --- channel parsing --------------------------------------------------------

def test_market_is_parsed_from_the_channel_string():
    """The venue answers on 'order_book:1' though subscription used a slash."""
    assert market_of({"channel": "order_book:1"}) == 1
    assert market_of({"channel": "order_book:24"}) == 24


def test_unparseable_channel_returns_none_rather_than_a_default():
    """Guessing a market would file frames under the wrong instrument.

    Returning 0 here would be worse than failing: market 0 is a real market,
    and its file would quietly fill with another market's book.
    """
    assert market_of({"channel": "order_book:"}) is None
    assert market_of({"channel": "garbage"}) is None
    assert market_of({}) is None


# --- the writer must never lose what was already captured ------------------

def test_reopening_the_same_day_appends_rather_than_truncates(tmp_path):
    """A restart mid-day must extend the file.

    Truncating would silently discard every frame collected before the
    restart, and the file would still look healthy.
    """
    first = DayWriter(tmp_path)
    first.write(1, '{"a":1}\n')
    first.close()

    second = DayWriter(tmp_path)
    second.write(1, '{"a":2}\n')
    second.close()

    path = next(tmp_path.glob("book_1_*.jsonl.gz"))
    with gzip.open(path, "rt") as fh:
        lines = [json.loads(l) for l in fh if l.strip()]

    assert lines == [{"a": 1}, {"a": 2}]


def test_each_market_gets_its_own_file(tmp_path):
    writer = DayWriter(tmp_path)
    writer.write(1, '{"m":1}\n')
    writer.write(24, '{"m":24}\n')
    writer.close()

    assert len(list(tmp_path.glob("book_1_*.jsonl.gz"))) == 1
    assert len(list(tmp_path.glob("book_24_*.jsonl.gz"))) == 1


def test_close_flushes_the_gzip_tail(tmp_path):
    """Without an explicit close the buffered tail is lost on every restart.

    Fewer than FLUSH_EVERY lines never reach disk on their own, so a collector
    that skipped close() would lose the end of every file it ever wrote.
    """
    writer = DayWriter(tmp_path)
    writer.write(1, '{"only":"line"}\n')
    writer.close()

    path = next(tmp_path.glob("book_1_*.jsonl.gz"))
    with gzip.open(path, "rt") as fh:
        assert json.loads(fh.read()) == {"only": "line"}


def test_bytes_written_are_reported_for_size_accounting(tmp_path):
    writer = DayWriter(tmp_path)
    line = '{"x":1}\n'

    assert writer.write(1, line) == len(line)

    writer.close()


# --- what the format layer must preserve about a snapshot ------------------

def test_snapshot_frames_are_storable_verbatim(tmp_path):
    """Deltas are meaningless without the snapshot they apply to.

    The tape collector deliberately drops its subscription snapshot because
    those trades arrive again as updates. For the book the opposite holds: a
    file whose snapshot was dropped cannot be replayed at all. This pins the
    writer's ability to carry a large one.
    """
    snapshot = {"type": "subscribed/order_book", "channel": "order_book:1",
                "order_book": {"nonce": 1, "begin_nonce": 0,
                               "asks": [{"price": str(p), "size": "1"}
                                        for p in range(2000)],
                               "bids": []}}
    writer = DayWriter(tmp_path)
    writer.write(1, json.dumps(snapshot, separators=(",", ":")) + "\n")
    writer.close()

    path = next(tmp_path.glob("book_1_*.jsonl.gz"))
    with gzip.open(path, "rt") as fh:
        restored = json.loads(fh.read())

    assert restored == snapshot
    assert len(restored["order_book"]["asks"]) == 2000
