"""
Lighter public trade collector → JSONL per UTC day in data/ticks/.

Talks to wss://mainnet.zklighter.elliot.ai/stream directly because the
SDK's WsClient only supports order_book/account channels.

No API key, no signing — public market data only.

Each output line: {"m": market_id, "p": price, "s": size, "t": ts_ms, "side": "buy"|"sell"}
Field names defensive: we log any unknown message type instead of crashing,
because Lighter's docs are still moving.
"""
from __future__ import annotations
import asyncio, json, os, signal
from datetime import datetime, timezone
from pathlib import Path

import websockets
from loguru import logger
from tenacity import retry, wait_exponential, stop_never, before_sleep_log
import logging
import yaml


DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"


def _day_path(out_dir: Path, market_id: int) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return out_dir / f"trades_{market_id}_{day}.jsonl"


def _normalize_trade(market_id: int, raw: dict) -> dict | None:
    """Accepts one trade dict from Lighter. Returns flat record or None."""
    try:
        price = float(raw.get("price") or raw["px"])
        size  = float(raw.get("size")  or raw.get("base_amount") or raw["sz"])
        ts    = int(raw.get("timestamp") or raw.get("ts") or raw["time"])
        # 'is_ask' true → maker was seller → aggressor bought, or vice-versa.
        # Lighter's exact convention varies; we just record what we see.
        side = "sell" if raw.get("is_ask") else "buy"
        return {"m": market_id, "p": price, "s": size, "t": ts, "side": side}
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"unparsable trade for market {market_id}: {raw} ({e})")
        return None


async def _consume(ws, market_ids: list[int], out_dir: Path):
    # subscribe
    for mid in market_ids:
        await ws.send(json.dumps({"type": "subscribe", "channel": f"trade/{mid}"}))
        logger.info(f"subscribed trade/{mid}")

    handles: dict[int, "Path"] = {}
    seen_types: set[str] = set()

    async for raw in ws:
        msg = json.loads(raw)
        mtype = msg.get("type", "")

        if mtype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if mtype == "connected":
            continue
        if mtype.startswith("subscribed/"):
            logger.debug(f"ack: {mtype}")
            continue

        if mtype.startswith("update/trade") or mtype.startswith("subscribed/trade"):
            # channel looks like "trade:{market_id}"
            chan = msg.get("channel", "")
            try:
                mid = int(chan.split(":")[1])
            except (IndexError, ValueError):
                logger.warning(f"bad channel: {chan}")
                continue
            trades = msg.get("trades") or msg.get("data") or []
            if not isinstance(trades, list):
                trades = [trades]
            path = handles.get(mid)
            today_path = _day_path(out_dir, mid)
            if path != today_path:
                handles[mid] = today_path
                path = today_path
            with path.open("a") as f:
                for t in trades:
                    rec = _normalize_trade(mid, t)
                    if rec:
                        f.write(json.dumps(rec) + "\n")
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
    cfg = yaml.safe_load(open("config.yaml"))
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
