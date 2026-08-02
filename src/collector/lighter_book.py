"""L2 order-book collector: `order_book/{market}`, stored verbatim and gzipped.

Why this exists: three negative results in a row (ranking by PnL, longer
horizons, behavioural features) were all obtained from the trade tape, which
is the *output* of the book. `MICROSTRUCTURE_PIPELINE.md` predicted this in
July — "we were analysing the shadow". A taker decides by looking at the book;
the tape only records the moment they hit it. Whether book-derived features
predict anything is a question that has never been asked here, and it cannot
be asked until the book has been on disk for days.

Separate process, separate directory, separate unit — same guarantee as
`lighter_participants.py`: a bug here cannot touch the tape or the ticks.

    data/book/book_{market}_{YYYYMMDD}.jsonl.gz

STORED VERBATIM — decided deliberately, 2026-08-03
--------------------------------------------------
A compact format was written and proven reversible first
(`src/collector/book_format.py`, 11 tests: raw -> book and raw -> compact ->
book compared frame by frame over 2209 live frames, identical at every step).
It is *not* used here, because the honest size comparison undercut its reason
to exist:

    raw JSON, gzipped      0.089 GB/day/market   ->  10.7 GB per month, 4 markets
    compact, gzipped       0.068 GB/day/market   ->   8.2 GB per month, 4 markets

Gzip does the work (8x); the format change adds only 1.32x on top. An earlier
"25x" figure was wrong — it compared uncompressed raw against compressed
compact. Trading 2.5 GB a month for the risk of discarding a field nobody
thought to keep is a bad trade in a project whose worst incidents are exactly
that: `liquidation_trades` cost 31 hours of unlabelled capture because we
guessed at a schema instead of storing what arrived. Collection is
forward-only. What is not written now cannot be recovered later.

`book_format.py` remains useful as a second pass over accumulated data, once
it is known which fields matter.

WHAT MUST NOT BE DROPPED — measured, not assumed
-------------------------------------------------
`subscribed/order_book` carries the full book (1878 asks + 2201 bids on market
1, up to 151 KB in one frame). The tape collector deliberately skips its
snapshot because those trades arrive again as updates; **the opposite is true
here.** Deltas are meaningless without the snapshot they apply to, and a
reconnect mid-day starts a fresh one. Skipping it would produce a file that
cannot be replayed.

`nonce` / `begin_nonce` are the venue's documented continuity check:
`begin_nonce` of each frame equals the previous frame's `nonce`, verified over
2209 frames with zero gaps. They are the only way to distinguish "the book did
not change" from "we lost a chunk", so they are never dropped.

`size == 0` means the level is removed — 25.9% of level updates. It arrives as
both "0" and "0.00000", so any reader must compare numerically. Nothing is
interpreted at this layer; that is the replayer's job.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

import websockets
from tenacity import (before_sleep_log, retry, retry_if_not_exception_type,
                      stop_never, wait_exponential)

DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"
FLUSH_EVERY = 200

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lighter_book")


def _day_path(out_dir: Path, market_id: int) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return out_dir / f"book_{market_id}_{day}.jsonl.gz"


class DayWriter:
    """One gzipped file per market per UTC day, rotated on the day boundary."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self._paths: dict[int, Path] = {}
        self._handles: dict[int, gzip.GzipFile] = {}
        self._since_flush: dict[int, int] = {}

    def write(self, market_id: int, line: str) -> int:
        path = _day_path(self.out_dir, market_id)
        if self._paths.get(market_id) != path:
            self._close(market_id)
            self._paths[market_id] = path
            # Append, never truncate: a restart mid-day must extend the file.
            # This yields a multi-member gzip, which standard readers handle.
            self._handles[market_id] = gzip.open(path, "at", encoding="utf-8")
            self._since_flush[market_id] = 0
            logger.info(f"[m={market_id}] writing {path}")

        self._handles[market_id].write(line)
        self._since_flush[market_id] += 1
        if self._since_flush[market_id] >= FLUSH_EVERY:
            self._handles[market_id].flush()
            self._since_flush[market_id] = 0
        return len(line)

    def _close(self, market_id: int) -> None:
        handle = self._handles.pop(market_id, None)
        if handle is not None:
            handle.flush()
            handle.close()

    def close(self) -> None:
        for market_id in list(self._handles):
            self._close(market_id)


def market_of(msg: dict) -> int | None:
    """Market id from the channel string, e.g. 'order_book:1' -> 1."""
    channel = msg.get("channel") or ""
    _, _, tail = channel.partition(":")
    try:
        return int(tail)
    except ValueError:
        return None


async def _consume(ws, market_ids: list[int], writer: DayWriter) -> None:
    for market_id in market_ids:
        await ws.send(json.dumps({"type": "subscribe",
                                  "channel": f"order_book/{market_id}"}))
        logger.info(f"subscribed order_book/{market_id}")

    written = {m: 0 for m in market_ids}
    snapshots = {m: 0 for m in market_ids}
    levels = {m: 0 for m in market_ids}
    raw_bytes = {m: 0 for m in market_ids}
    last_nonce: dict[int, int] = {}
    gaps = {m: 0 for m in market_ids}
    seen_types: set[str] = set()
    frames = 0

    async for raw in ws:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"non-JSON frame: {exc}; head={raw[:120]!r}")
            continue

        mtype = msg.get("type") or ""
        if mtype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if mtype == "connected":
            continue
        if "order_book" not in mtype:
            # An unknown enum value is the documented trigger for rebuilding
            # the API digest (WORKFLOW.md §5). Logging it loudly turns "we
            # corrupted data silently" into "we saw it the same day".
            if mtype not in seen_types:
                seen_types.add(mtype)
                logger.warning(f"unhandled type: {mtype} | {str(msg)[:200]}")
            continue

        market_id = market_of(msg)
        if market_id is None or market_id not in written:
            logger.warning(f"book frame for unexpected channel: {msg.get('channel')!r}")
            continue

        book = msg.get("order_book") or {}
        is_snapshot = mtype.startswith("subscribed/")
        if is_snapshot:
            snapshots[market_id] += 1
            logger.info(f"[m={market_id}] snapshot: "
                        f"{len(book.get('asks') or [])} asks, "
                        f"{len(book.get('bids') or [])} bids")

        # Continuity is checked live rather than only at analysis time, so a
        # gap is visible in the journal on the day it happens. A reconnect
        # legitimately breaks the chain, and the fresh snapshot that follows
        # re-establishes it — so a gap is counted, not treated as an error.
        nonce = book.get("nonce")
        begin = book.get("begin_nonce")
        previous = last_nonce.get(market_id)
        if previous is not None and begin and begin != previous and not is_snapshot:
            gaps[market_id] += 1
            logger.warning(f"[m={market_id}] nonce gap: expected {previous}, "
                           f"frame begins at {begin}")
        if nonce:
            last_nonce[market_id] = nonce

        # Stored verbatim: the whole point of this collector is to not decide
        # today which fields matter later.
        raw_bytes[market_id] += writer.write(
            market_id, json.dumps(msg, separators=(",", ":")) + "\n")
        written[market_id] += 1
        levels[market_id] += len(book.get("asks") or []) + len(book.get("bids") or [])

        frames += 1
        if frames >= 20000:
            for m in market_ids:
                logger.info(
                    f"[m={m}] frames={written[m]} snapshots={snapshots[m]} "
                    f"levels={levels[m]} nonce_gaps={gaps[m]} "
                    f"uncompressed={raw_bytes[m]/1e6:.1f}MB")
            frames = 0


# Reconnect forever on network faults, never on cancellation: without the
# exclusion, a systemd stop makes tenacity reconnect during interpreter
# shutdown and the unit exits non-zero on an ordinary restart.
@retry(wait=wait_exponential(multiplier=1, min=1, max=60), stop=stop_never,
       retry=retry_if_not_exception_type(asyncio.CancelledError),
       before_sleep=before_sleep_log(logging.getLogger("tenacity"), logging.WARNING))
async def _run(ws_url: str, market_ids: list[int], writer: DayWriter) -> None:
    logger.info(f"connecting {ws_url}")
    # max_size=None: the subscription snapshot exceeded 150 KB on market 1 and
    # the default 1 MB cap is close enough to matter on a busier market.
    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20,
                                  max_size=None) as ws:
        await _consume(ws, market_ids, writer)


async def _main_async(ws_url: str, market_ids: list[int], writer: DayWriter) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    runner = asyncio.create_task(_run(ws_url, market_ids, writer))
    stopper = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({runner, stopper},
                                       return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if runner in done:
        runner.result()      # a crash must surface as a crash
    else:
        logger.info("stop signal received, shutting down")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--markets", type=int, nargs="+", default=[0, 1, 2, 24],
                    help="market ids (default: the four the tape already covers, "
                         "so book and tape can be joined)")
    ap.add_argument("--out-dir", default="data/book")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ws_url = os.environ.get("LIGHTER_WS_URL") or DEFAULT_WS
    writer = DayWriter(out_dir)

    try:
        asyncio.run(_main_async(ws_url, args.markets, writer))
    finally:
        # Without this the tail of the gzip buffer is lost on every restart.
        writer.close()


if __name__ == "__main__":
    main()
