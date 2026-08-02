"""Compact on-disk format for L2 order-book deltas, and the reader that undoes it.

Measured on `order_book/1` (2026-08-02): the raw WS JSON runs 0.75-1.15 GB per
market per day, which fills the box in under four days across four markets.
Gzipped JSON is 8x smaller; dropping the JSON envelope and gzipping is 25x,
about 0.03 GB per market per day. That is the difference between three days of
capture and four months.

**The format is only worth anything if it is reversible.** A month of data
nothing can be reconstructed from is worse than no data, because it looks like
an asset. So this module ships with `replay()`, and the test suite's real job
is proving that replaying the compact form reproduces the same book states as
replaying raw JSON — not that the compact form parses.

Line format, one level update per line:

    seq,ts,nonce,side,price,size

  seq    monotonically increasing message counter, so levels belonging to one
         WS frame stay grouped after the fact
  ts     the frame's `timestamp`
  nonce  the frame's `order_book.nonce`, kept because the venue documents
         continuity checking through it — `begin_nonce` of a frame must match
         the previous frame's `nonce`. Dropping it to save bytes would make a
         gap in the capture indistinguishable from a quiet book, which is the
         `liquidation_trades` mistake in another costume.
  side   'a' or 'b'
  price  decimal string, verbatim from the venue
  size   decimal string, verbatim. **"0" means the level is removed.**

Prices and sizes stay strings on purpose. They arrive as strings, and routing
them through float would quietly round a venue-exact decimal; the tape module
learned this already (MINING.md §3).

Snapshot frames are marked by a `snapshot` line so a reader knows to clear the
book rather than merge — without that, a mid-file resubscribe would silently
append a second book on top of the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SNAPSHOT_MARK = "S"
DELTA_MARK = "D"


@dataclass
class Frame:
    """One WS message, decoded to what the book needs."""

    seq: int
    ts: int
    nonce: int
    begin_nonce: int
    is_snapshot: bool
    asks: list[tuple[str, str]] = field(default_factory=list)
    bids: list[tuple[str, str]] = field(default_factory=list)


def encode_frame(seq: int, msg: dict) -> list[str]:
    """Turn one raw WS message into compact lines. Empty list if it carries no book.

    A frame with no level changes still emits its header line: the nonce chain
    has to stay unbroken or continuity checking becomes impossible.
    """
    kind = msg.get("type") or ""
    if "order_book" not in kind:
        return []
    book = msg.get("order_book") or {}
    is_snapshot = kind.endswith("subscribed/order_book") or kind == "subscribed/order_book"
    ts = int(msg.get("timestamp") or 0)
    nonce = int(book.get("nonce") or 0)
    begin = int(book.get("begin_nonce") or 0)

    mark = SNAPSHOT_MARK if is_snapshot else DELTA_MARK
    out = [f"{seq},{ts},{nonce},{begin},{mark}"]
    for side, levels in (("a", book.get("asks") or []), ("b", book.get("bids") or [])):
        for lvl in levels:
            price = lvl.get("price")
            size = lvl.get("size")
            if price is None or size is None:
                continue
            out.append(f"{seq},{side},{price},{size}")
    return out


def decode(lines) -> list[Frame]:
    """Rebuild frames from compact lines. Inverse of `encode_frame`."""
    frames: dict[int, Frame] = {}
    order: list[int] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) == 5 and parts[4] in (SNAPSHOT_MARK, DELTA_MARK):
            seq = int(parts[0])
            frames[seq] = Frame(seq=seq, ts=int(parts[1]), nonce=int(parts[2]),
                                begin_nonce=int(parts[3]),
                                is_snapshot=parts[4] == SNAPSHOT_MARK)
            order.append(seq)
        elif len(parts) == 4:
            seq = int(parts[0])
            frame = frames.get(seq)
            if frame is None:
                # A level line without its header: the file is truncated or
                # interleaved. Losing it silently would corrupt the book, so
                # it is skipped and the gap shows up in continuity checking.
                continue
            (frame.asks if parts[1] == "a" else frame.bids).append((parts[2], parts[3]))
    return [frames[s] for s in order]


def apply_frame(book: dict[str, dict[str, str]], frame: Frame) -> None:
    """Advance a book by one frame. `size == "0"` removes the level."""
    if frame.is_snapshot:
        book["a"].clear()
        book["b"].clear()
    for side, levels in (("a", frame.asks), ("b", frame.bids)):
        for price, size in levels:
            if _is_zero(size):
                book[side].pop(price, None)
            else:
                book[side][price] = size


def _is_zero(size: str) -> bool:
    """'0', '0.0', '0.00000' all mean removal. Compared numerically, not by text."""
    try:
        return float(size) == 0.0
    except (TypeError, ValueError):
        return False


def new_book() -> dict[str, dict[str, str]]:
    return {"a": {}, "b": {}}


def replay(frames) -> dict[str, dict[str, str]]:
    """Fold frames into a final book state."""
    book = new_book()
    for frame in frames:
        apply_frame(book, frame)
    return book


def frames_from_raw(messages) -> list[Frame]:
    """Decode raw WS messages directly, bypassing the compact form.

    This exists so the tests can compare two independent paths to the same
    book: raw -> book, and raw -> compact -> book. Without it, a bug shared by
    both paths would pass unnoticed.
    """
    out = []
    for seq, msg in enumerate(messages):
        kind = msg.get("type") or ""
        if "order_book" not in kind:
            continue
        book = msg.get("order_book") or {}
        out.append(Frame(
            seq=seq,
            ts=int(msg.get("timestamp") or 0),
            nonce=int(book.get("nonce") or 0),
            begin_nonce=int(book.get("begin_nonce") or 0),
            is_snapshot=kind.endswith("subscribed/order_book"),
            asks=[(l["price"], l["size"]) for l in (book.get("asks") or [])
                  if l.get("price") is not None and l.get("size") is not None],
            bids=[(l["price"], l["size"]) for l in (book.get("bids") or [])
                  if l.get("price") is not None and l.get("size") is not None],
        ))
    return out


def continuity_gaps(frames) -> list[tuple[int, int, int]]:
    """Frames whose `begin_nonce` does not match the previous frame's `nonce`.

    Returns (seq, expected, found). The venue documents this as the way to
    verify an unbroken stream, so a capture that cannot answer it is a capture
    that cannot be trusted — which is the whole reason nonce is stored.
    """
    gaps = []
    prev = None
    for frame in frames:
        if prev is not None and frame.begin_nonce and frame.begin_nonce != prev:
            gaps.append((frame.seq, prev, frame.begin_nonce))
        if frame.nonce:
            prev = frame.nonce
    return gaps
