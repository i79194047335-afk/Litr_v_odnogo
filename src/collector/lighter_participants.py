"""
Participant-mining collector: the SAME public `trade/{market}` WS channel the
tick collector uses, but persisting the account-level fields it throws away.

Separate process, separate output directory, separate systemd unit — on purpose.
`src/collector/lighter_ticks.py` has been up since 2026-07-10 feeding every
backtest in the project, and the handoff's instruction is "старый сбор НЕ
ломать". Running this alongside makes that guarantee structural instead of
careful: a bug here cannot touch the tick stream. Three simultaneous probe
connections were held open against the live collector on 2026-07-28 with no
effect, so the extra connection is not a risk worth avoiding.

Each output line (schema p1), gzipped JSONL, one file per market per UTC day:

    data/participants/trades_full_{market}_{YYYYMMDD}.jsonl.gz

WHY GZIP AND WHY NOT EVERY FIELD — measured, not assumed
--------------------------------------------------------
The wire record is 909 bytes against the tick collector's 92. At the measured
1.14M trades/day across markets 0/1/2/24 that is 31 GB/month, and the VPS has
19 GB free: uncompressed full capture fills the disk in under three weeks.
Two levers, applied in this order:

  1. DROP THE PROVABLY REDUNDANT. `*_str` fields duplicate their int twins
     exactly; `usd_amount` is price * size. Nothing is lost that cannot be
     recomputed. `tx_hash` is dropped too — 80 chars of high-entropy hex, so it
     costs the most *after* compression, and on-chain forensics is not what this
     track does. Pass --keep-all to store the wire record verbatim instead;
     collection is forward-only, so this is the one setting worth deciding
     deliberately rather than discovering later.
  2. GZIP. These records are near-identical line to line, which is the case gzip
     handles best.

CONDITIONAL FIELDS — measured 2026-07-28 over two samples (401 and 353 trades)
by `probe_trade_fields.py`, and this contradicts the handoff doc:
`taker_position_sign_changed` is present on only ~28% of trades and is `True`
on every trade that carries it, so ABSENT MEANS FALSE. Same for the maker twin.
Fees (`maker_fee`, `taker_fee`, `integrator_*`) and the `allocated_margin_*`
fields are likewise conditional. Everything here therefore stores what arrived
and never invents a default — a `.get()` at read time is correct, a
`dict[...]` is a bug.

LIQUIDATIONS come down this same channel in a separate `liquidation_trades`
array (also measured 2026-07-28 — no separate channel exists, and none needs to
be found). They are stored in the same file with `"kind": "liq"`, because
forced flow is exactly the kind of participant behaviour this track is for and
capturing it costs nothing.

DEDUP mirrors the tick collector: bounded per-market trade_id window, and the
`subscribed/trade` snapshot is skipped rather than persisted, since those trades
arrive again immediately via `update/trade`.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import signal
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import websockets
from loguru import logger
from tenacity import (retry, wait_exponential, stop_never, before_sleep_log,
                      retry_if_not_exception_type)
import logging

DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"

# Same sizing rationale as the tick collector: comfortably longer than any
# observed repeat gap, at ~15 trades/s peak this is ~55 minutes of history.
DEDUP_WINDOW_PER_MARKET = 50000

# Duplicates of an int field that is already stored. Dropping them loses nothing.
STR_TWINS = (
    "trade_id_str", "ask_id_str", "bid_id_str",
    "ask_client_id_str", "bid_client_id_str",
)

# Recomputable or not used by this track. See the module docstring — this is a
# size decision, and it is irreversible for data not yet collected.
DERIVABLE = ("usd_amount",)      # = price * size
UNUSED = ("tx_hash",)            # 80 chars of hex; costs most after compression

# Without these a record cannot be attributed to a participant at a time, which
# is the entire point of this collector. A record missing any of them is dropped
# loudly rather than stored half-formed.
REQUIRED = ("trade_id", "timestamp", "price", "size",
            "ask_account_id", "bid_account_id", "is_maker_ask")

# Flush the gzip buffer this often. A hard kill loses at most this many records;
# gzip buffers aggressively, so leaving it to the OS would lose far more.
FLUSH_EVERY = 200


def _day_path(out_dir: Path, market_id: int) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return out_dir / f"trades_full_{market_id}_{day}.jsonl.gz"


def read_records(path: Path | str, quiet: bool = False):
    """Yield records from a participant file, including one still being written.

    Use this instead of `gzip.open` for anything downstream. A gzip member is
    only terminated by its CRC trailer at close, so the day the collector is
    currently writing raises `EOFError: Compressed file ended before the
    end-of-stream marker` on a plain read — even though every flushed byte is on
    disk and intact. The same is true after a SIGKILL, which no flush interval
    can protect against, so tolerating a short tail is not a workaround for a
    missing feature: it is what reading an append-only stream means.

    Records already decompressed before the cut are yielded normally; a final
    partially-written line is dropped rather than half-parsed.
    """
    path = Path(path)
    n, truncated = 0, False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.endswith("\n"):
                    # Writer was mid-line at the cut.
                    truncated = True
                    break
                yield json.loads(line)
                n += 1
    except EOFError:
        truncated = True
    if truncated and not quiet:
        logger.debug(f"{path.name}: still being written or ended abruptly; "
                     f"{n} complete records read")


def _normalize(market_id: int, raw: dict, kind: str, keep_all: bool) -> dict | None:
    """One wire trade dict -> one storable record, or None if unusable."""
    missing = [k for k in REQUIRED if raw.get(k) is None]
    if missing:
        logger.warning(
            f"[m={market_id}] dropping {kind} record missing {missing}: "
            f"{str(raw)[:200]}"
        )
        return None
    if not isinstance(raw.get("is_maker_ask"), bool):
        logger.warning(
            f"[m={market_id}] dropping {kind} tid={raw.get('trade_id')}: "
            f"is_maker_ask={raw.get('is_maker_ask')!r} is not a bool, "
            f"taker side would be a guess"
        )
        return None

    if keep_all:
        rec = dict(raw)
    else:
        drop = set(STR_TWINS) | set(DERIVABLE) | set(UNUSED)
        rec = {k: v for k, v in raw.items() if k not in drop}

    # Liquidations arrive in TWO ways on the wire: (a) in the separate
    # `liquidation_trades` array where they are expected, and (b) in the main
    # `trades` array with `type: "liquidation"` — the real path, discovered
    # 2026-07-29 when the collector had been reporting liq=0 for 31h while 112
    # liquidations sat on disk unmarked. Check the record's own type field, not
    # just the array the caller found it in.
    if kind == "liq" or raw.get("type") == "liquidation":
        rec["kind"] = "liq"
    return rec


class Deduper:
    """Bounded per-market trade_id dedup — same design as the tick collector."""

    __slots__ = ("_seen", "_order", "_max")

    def __init__(self, max_size: int = DEDUP_WINDOW_PER_MARKET):
        self._seen: set[int] = set()
        self._order: deque[int] = deque()
        self._max = max_size

    def is_new(self, tid: int) -> bool:
        if tid in self._seen:
            return False
        self._seen.add(tid)
        self._order.append(tid)
        if len(self._order) > self._max:
            self._seen.discard(self._order.popleft())
        return True


class DayWriter:
    """One open gzip handle per market, rotated at UTC midnight.

    Reopening per batch would restart the gzip dictionary constantly and cost
    most of the compression this collector exists to get.
    """

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self._paths: dict[int, Path] = {}
        self._handles: dict[int, gzip.GzipFile] = {}
        self._since_flush: dict[int, int] = {}

    def write(self, market_id: int, rec: dict) -> int:
        path = _day_path(self.out_dir, market_id)
        if self._paths.get(market_id) != path:
            self._close(market_id)
            self._paths[market_id] = path
            # Append mode: a restart mid-day must extend the file, not truncate
            # it. Appending produces a multi-member gzip, which every standard
            # reader handles transparently.
            self._handles[market_id] = gzip.open(path, "at", encoding="utf-8")
            self._since_flush[market_id] = 0
            logger.info(f"[m={market_id}] writing {path}")

        line = json.dumps(rec, separators=(",", ":")) + "\n"
        self._handles[market_id].write(line)
        self._since_flush[market_id] += 1
        if self._since_flush[market_id] >= FLUSH_EVERY:
            self._handles[market_id].flush()
            self._since_flush[market_id] = 0
        return len(line)

    def _close(self, market_id: int) -> None:
        h = self._handles.pop(market_id, None)
        if h is not None:
            h.flush()
            h.close()

    def close(self) -> None:
        for mid in list(self._handles):
            self._close(mid)


async def _consume(ws, market_ids: list[int], writer: DayWriter, keep_all: bool):
    for mid in market_ids:
        await ws.send(json.dumps({"type": "subscribe", "channel": f"trade/{mid}"}))
        logger.info(f"subscribed trade/{mid}")

    dedupers = {mid: Deduper() for mid in market_ids}
    written = {mid: 0 for mid in market_ids}
    liq = {mid: 0 for mid in market_ids}
    dropped_dup = {mid: 0 for mid in market_ids}
    dropped_bad = {mid: 0 for mid in market_ids}
    raw_bytes = {mid: 0 for mid in market_ids}
    seen_types: set[str] = set()
    frames = 0

    async for raw in ws:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"non-JSON frame: {e}; head={raw[:120]!r}")
            continue

        mtype = msg.get("type", "")
        if mtype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if mtype == "connected":
            continue
        # The (re)connect snapshot replays ~50 trades that arrive again via
        # update/trade. Persisting it is the reconnect-boundary duplicate the
        # tick collector already learned to avoid.
        if mtype.startswith("subscribed/"):
            logger.debug(f"skipping snapshot: {mtype}")
            continue
        if not mtype.startswith("update/trade"):
            if mtype not in seen_types:
                seen_types.add(mtype)
                logger.warning(f"unhandled type: {mtype} | {str(msg)[:200]}")
            continue

        chan = msg.get("channel", "")
        try:
            mid = int(chan.split(":")[1])
        except (IndexError, ValueError):
            logger.warning(f"bad channel: {chan}")
            continue
        if mid not in dedupers:
            logger.warning(f"trades for unsubscribed market {mid}, skipping")
            continue

        dedup = dedupers[mid]
        for kind, key in (("trade", "trades"), ("liq", "liquidation_trades")):
            for t in msg.get(key) or []:
                rec = _normalize(mid, t, kind, keep_all)
                if rec is None:
                    dropped_bad[mid] += 1
                    continue
                if not dedup.is_new(int(rec["trade_id"])):
                    dropped_dup[mid] += 1
                    continue
                raw_bytes[mid] += writer.write(mid, rec)
                written[mid] += 1
                if rec.get("kind") == "liq":
                    liq[mid] += 1

        frames += 1
        if frames >= 5000:
            for m in market_ids:
                logger.info(
                    f"[m={m}] written={written[m]} (liq={liq[m]}) "
                    f"dropped_dup={dropped_dup[m]} dropped_bad={dropped_bad[m]} "
                    f"uncompressed={raw_bytes[m]/1e6:.1f}MB"
                )
            frames = 0


# Reconnect forever on network faults, but never on cancellation: without the
# exclusion, asking the service to stop makes tenacity try to reconnect during
# interpreter shutdown, which is how the first version exited 1 on every
# systemctl stop.
@retry(wait=wait_exponential(multiplier=1, min=1, max=60), stop=stop_never,
       retry=retry_if_not_exception_type(asyncio.CancelledError),
       before_sleep=before_sleep_log(logging.getLogger("tenacity"), logging.WARNING))
async def _run(ws_url: str, market_ids: list[int], writer: DayWriter, keep_all: bool):
    logger.info(f"connecting {ws_url}")
    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        await _consume(ws, market_ids, writer, keep_all)


async def _main_async(ws_url: str, market_ids: list[int], writer: DayWriter,
                      keep_all: bool) -> None:
    """Run until the collector fails or a stop signal arrives.

    Deliberately not `loop.stop()`: stopping the loop out from under
    `run_until_complete` raises "Event loop stopped before Future completed",
    exits non-zero on an ordinary `systemctl stop`, and leaves the retry
    decorator reconnecting into a closing interpreter. Cancelling the task
    instead unwinds the connection properly and exits 0.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    runner = asyncio.create_task(_run(ws_url, market_ids, writer, keep_all))
    stopper = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({runner, stopper},
                                       return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if runner in done:
        # A crash must surface as a crash; only a requested stop is success.
        runner.result()
    else:
        logger.info("stop signal received, shutting down")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--markets", type=int, nargs="+", default=[1],
                    help="market ids to collect (default: 1, BTC — the only one "
                         "the backtest track ever used, and the cheapest place "
                         "to prove the track before widening)")
    ap.add_argument("--out-dir", default="data/participants")
    ap.add_argument("--keep-all", action="store_true",
                    help="store the wire record verbatim, including the _str "
                         "twins, usd_amount and tx_hash (~40%% larger)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ws_url = os.environ.get("LIGHTER_WS_URL") or DEFAULT_WS
    writer = DayWriter(out_dir)

    try:
        asyncio.run(_main_async(ws_url, args.markets, writer, args.keep_all))
    finally:
        # Without this the tail of the gzip buffer is lost on every restart.
        writer.close()


if __name__ == "__main__":
    main()
