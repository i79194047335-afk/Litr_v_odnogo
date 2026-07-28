#!/usr/bin/env python3
"""
STEP 1 of the microstructure pipeline: SEE what Lighter's WS actually sends
on the book / liquidation channels, before writing a single line of capture
code. Per the project rule — live API probing precedes schema decisions;
the SDK's Pydantic models have lied before, so we trust the wire, not them.

This does NOT store anything for research. It connects, subscribes, prints
the first frame of each channel PRETTY (so you read the real field names),
then prints a compact one-line summary per frame for a while so you can see
the shape and CADENCE of updates (snapshot vs deltas, how often, how deep).

Run it, watch ~60 seconds, and paste me the output. From the real frames we
design the capture format — not from guesses.

What we're trying to learn, concretely:
  - order_book: is the first frame a full snapshot? are later frames DELTAS
    (only changed levels) or full snapshots each time? how many price levels
    per side? what are the exact field names for price and size? is there a
    sequence number (needed to detect gaps on reconnect)?
  - is there a separate liquidation channel/type, or do liquidations just
    appear as trades? (affects whether we can study forced flow at all.)
  - does the book carry a server timestamp per update? (needed to align with
    trades and to measure our capture latency.)

Usage (VPS):
    python ws_probe_book.py --market 1 --seconds 60
    python ws_probe_book.py --market 1 --channels order_book trade --seconds 30
    python ws_probe_book.py --market 1 --raw          # dump raw JSON, no summarizing

Channels attempted (we don't know which exist — the probe reports which the
server accepts vs rejects, which is itself a finding):
    order_book/{m}   trade/{m}   (and any extra names you pass with --try)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter

import websockets

DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"


def _depth(levels) -> int:
    try:
        return len(levels)
    except TypeError:
        return -1


def _summarize_book(msg: dict) -> str:
    """One-line shape summary of a book frame. Field names are GUESSED here
    only for the summary line; the pretty first-frame dump shows ground
    truth. If these guesses miss, the summary will say 'depth=-1' and you'll
    see the real names in the first-frame dump."""
    bids = msg.get("bids") or msg.get("asks") is not None and msg.get("bids") or []
    b = msg.get("bids", [])
    a = msg.get("asks", [])
    off = msg.get("offset") or msg.get("sequence") or msg.get("seq") or "?"
    return f"bids={_depth(b):>3} asks={_depth(a):>3} seq={off}"


async def probe(ws_url: str, market: int, channels: list[str],
                seconds: int, raw: bool) -> None:
    print(f"connecting {ws_url}")
    async with websockets.connect(ws_url, ping_interval=20,
                                   ping_timeout=20) as ws:
        for ch in channels:
            sub = {"type": "subscribe", "channel": f"{ch}/{market}"}
            await ws.send(json.dumps(sub))
            print(f"-> subscribe {ch}/{market}")

        first_seen: set[str] = set()
        type_counts: Counter = Counter()
        rejected: list[str] = []
        t0 = time.time()
        last_summary = t0

        while time.time() - t0 < seconds:
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                print(f"[{time.time()-t0:5.1f}s] (no frame in 5s)")
                continue

            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                print(f"non-JSON frame: {raw_msg[:120]!r}")
                continue

            mtype = msg.get("type", "?")
            type_counts[mtype] += 1

            if mtype == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
            if mtype == "connected":
                print(f"[{time.time()-t0:5.1f}s] connected frame: "
                      f"{json.dumps(msg)[:200]}")
                continue

            # a rejected subscription is a real finding — record it
            if "error" in mtype.lower() or msg.get("error"):
                line = json.dumps(msg)[:200]
                if line not in rejected:
                    rejected.append(line)
                    print(f"[{time.time()-t0:5.1f}s] REJECTED/ERROR: {line}")
                continue

            if raw:
                print(f"[{time.time()-t0:5.1f}s] {mtype}: {json.dumps(msg)}")
                continue

            # FIRST frame of each type: pretty dump so real field names show
            if mtype not in first_seen:
                first_seen.add(mtype)
                print(f"\n===== FIRST '{mtype}' frame (full, pretty) =====")
                print(json.dumps(msg, indent=2)[:4000])
                print("===== (end) =====\n")
                continue

            # subsequent book frames: compact cadence line, ~2/sec max
            now = time.time()
            if now - last_summary >= 0.5:
                last_summary = now
                extra = _summarize_book(msg) if "order_book" in mtype else ""
                print(f"[{now-t0:5.1f}s] {mtype:<24} {extra}")

        print("\n================ SUMMARY ================")
        print(f"duration: {seconds}s")
        print("frame type counts:")
        for t, c in type_counts.most_common():
            print(f"  {t:<28} {c:>6}  ({c/seconds:.1f}/s)")
        if rejected:
            print("rejected/error subscriptions:")
            for r in rejected:
                print(f"  {r}")
        print("\nNext: paste this whole output back. We read the real field")
        print("names + cadence off it and design the capture format from")
        print("ground truth, not from SDK models.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", type=int, default=1)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--channels", nargs="+",
                     default=["order_book", "trade"],
                     help="channel prefixes to subscribe (server may reject "
                          "unknown ones — that's a finding, not an error)")
    ap.add_argument("--try", dest="extra", nargs="*", default=[],
                     help="extra channel names to probe for liquidations etc, "
                          "e.g. --try liquidation liquidations account_all")
    ap.add_argument("--raw", action="store_true",
                     help="dump every frame's raw JSON, no summarizing")
    args = ap.parse_args()

    ws_url = os.environ.get("LIGHTER_WS_URL") or DEFAULT_WS
    channels = args.channels + args.extra
    try:
        asyncio.run(probe(ws_url, args.market, channels, args.seconds, args.raw))
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
