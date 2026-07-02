"""
Lighter public trade collector → JSONL per UTC day in data/ticks/.

Talks to wss://mainnet.zklighter.elliot.ai/stream directly because the
SDK's WsClient only supports order_book/account channels.

No API key, no signing — public market data only.

Each output line (schema v2):
    {"m": market_id, "p": price, "s": size, "t": ts_ms,
     "side": "buy"|"sell", "tid": trade_id}

`tid` is Lighter's unique trade_id and is the anchor for deduplication.
Older files without `tid` (schema v1) remain readable — anything downstream
must treat `tid` as optional when loading historical data.

DEDUP:
  Lighter's WebSocket occasionally repeats the same trade multiple times
  within one `update/trade` batch (measured: ~17.5% of raw rows are
  duplicates of a trade already delivered a few frames earlier). We track
  recently-seen trade_ids in a bounded set and drop repeats.

SNAPSHOT HANDLING:
  On (re)connect the server sends one `subscribed/trade` frame containing
  the last ~50 trades as a snapshot. These trades will also arrive via
  `update/trade` immediately after, so we DO NOT persist the snapshot —
  only `update/trade`. This eliminates reconnect-boundary duplicates.
"""
from __future__ import annotations
import asyncio
import json
import os
import signal
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import websockets
from loguru import logger
from tenacity import retry, wait_exponential, stop_never, before_sleep_log
import logging
import yaml


DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"

# How many recent trade_ids to remember per market for deduplication.
# At ~15 trades/s peak on BTC, 50000 IDs ≈ 55 min of history — plenty of
# margin over the observed max in-batch repeat gap.
DEDUP_WINDOW_PER_MARKET = 50000


def _day_path(out_dir: Path, market_id: int) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return out_dir / f"trades_{market_id}_{day}.jsonl"


def _get_or_none(raw: dict, *keys: str):
    """Return first value that is not None across candidate keys, else None.

    NOTE: uses `is not None` (not truthiness) — a legitimate price/size/ts
    of 0 must not fall through to the next key. Trades with 0 shouldn't
    exist on Lighter, but the safe-check costs nothing.
    """
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


def _normalize_trade(market_id: int, raw: dict) -> dict | None:
    """One WS trade dict → one flat JSONL record, or None if unparsable.

    Returns None (and warns) if any critical field is missing. Never crashes.
    """
    tid_v = _get_or_none(raw, "trade_id", "tid", "id")
    price_v = _get_or_none(raw, "price", "px")
    size_v = _get_or_none(raw, "size", "base_amount", "sz")
    ts_v = _get_or_none(raw, "timestamp", "ts", "time")

    if tid_v is None or price_v is None or size_v is None or ts_v is None:
        logger.warning(
            f"trade missing required field for market {market_id}: "
            f"tid={tid_v} price={price_v} size={size_v} ts={ts_v} raw={raw}"
        )
        return None

    # is_maker_ask must be an actual bool — if it's missing we cannot
    # infer taker side and dropping the trade is safer than picking a
    # default (previous behaviour silently defaulted to "sell").
    ima = raw.get("is_maker_ask")
    if not isinstance(ima, bool):
        logger.warning(
            f"trade missing is_maker_ask (got {ima!r}) for market {market_id}, "
            f"tid={tid_v}: dropping — cannot determine side"
        )
        return None
    side = "buy" if ima else "sell"

    try:
        return {
            "m": market_id,
            "p": float(price_v),
            "s": float(size_v),
            "t": int(ts_v),
            "side": side,
            "tid": int(tid_v),
        }
    except (TypeError, ValueError) as e:
        logger.warning(f"trade cast failed for market {market_id}: {raw} ({e})")
        return None


class Deduper:
    """Bounded per-market trade_id dedup.

    Uses a deque as a FIFO window plus a set for O(1) membership. When the
    window fills, oldest tids are evicted. Sized to comfortably exceed the
    observed max repeat gap.
    """

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
            evicted = self._order.popleft()
            self._seen.discard(evicted)
        return True


async def _consume(ws, market_ids: list[int], out_dir: Path):
    for mid in market_ids:
        await ws.send(json.dumps({"type": "subscribe", "channel": f"trade/{mid}"}))
        logger.info(f"subscribed trade/{mid}")

    handles: dict[int, Path] = {}
    seen_types: set[str] = set()
    dedupers: dict[int, Deduper] = {mid: Deduper() for mid in market_ids}
    # per-market counters, logged periodically
    stats_written: dict[int, int] = {mid: 0 for mid in market_ids}
    stats_dropped_dup: dict[int, int] = {mid: 0 for mid in market_ids}
    stats_dropped_bad: dict[int, int] = {mid: 0 for mid in market_ids}
    frames_since_log = 0

    async for raw in ws:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"non-JSON frame from Lighter WS: {e}; head={raw[:120]!r}")
            continue

        mtype = msg.get("type", "")

        if mtype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if mtype == "connected":
            continue
        if mtype.startswith("subscribed/") and not mtype.startswith("subscribed/trade"):
            logger.debug(f"ack: {mtype}")
            continue

        # subscribed/trade sends a snapshot of the last ~50 trades on
        # (re)connect. Those trades will also arrive via update/trade, so
        # we deliberately skip snapshots to avoid reconnect-boundary dupes.
        if mtype.startswith("subscribed/trade"):
            logger.debug(f"skipping snapshot: {mtype}")
            continue

        if mtype.startswith("update/trade"):
            chan = msg.get("channel", "")
            try:
                mid = int(chan.split(":")[1])
            except (IndexError, ValueError):
                logger.warning(f"bad channel: {chan}")
                continue
            if mid not in dedupers:
                logger.warning(f"trades for unsubscribed market {mid}, skipping")
                continue

            trades = msg.get("trades") or msg.get("data") or []
            if not isinstance(trades, list):
                trades = [trades]

            # rotate file handle at UTC midnight
            today_path = _day_path(out_dir, mid)
            if handles.get(mid) != today_path:
                handles[mid] = today_path

            path = handles[mid]
            dedup = dedupers[mid]
            with path.open("a") as f:
                for t in trades:
                    rec = _normalize_trade(mid, t)
                    if rec is None:
                        stats_dropped_bad[mid] += 1
                        continue
                    if not dedup.is_new(rec["tid"]):
                        stats_dropped_dup[mid] += 1
                        continue
                    f.write(json.dumps(rec) + "\n")
                    stats_written[mid] += 1

            frames_since_log += 1
            if frames_since_log >= 5000:
                for m in market_ids:
                    logger.info(
                        f"[m={m}] written={stats_written[m]} "
                        f"dropped_dup={stats_dropped_dup[m]} "
                        f"dropped_bad={stats_dropped_bad[m]}"
                    )
                frames_since_log = 0
            continue

        if mtype not in seen_types:
            seen_types.add(mtype)
            logger.warning(f"unhandled message type: {mtype} | sample: {str(msg)[:200]}")


@retry(wait=wait_exponential(multiplier=1, min=1, max=60),
       stop=stop_never,
       before_sleep=before_sleep_log(logging.getLogger("tenacity"), logging.WARNING))
async def _run(ws_url: str, market_ids: list[int], out_dir: Path):
    logger.info(f"connecting {ws_url}")
    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        await _consume(ws, market_ids, out_dir)


def main():
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config.yaml not found in working directory")
        raise SystemExit(2)
    except yaml.YAMLError as e:
        logger.error(f"config.yaml is invalid YAML: {e}")
        raise SystemExit(2)

    ws_url = os.environ.get("LIGHTER_WS_URL") or DEFAULT_WS
    market_ids = cfg["collector"]["market_ids"]
    out_dir = Path(cfg["collector"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(_run(ws_url, market_ids, out_dir))
    finally:
        loop.close()


if __name__ == "__main__":
    main()