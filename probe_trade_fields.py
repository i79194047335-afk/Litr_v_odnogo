#!/usr/bin/env python3
"""
STEP 1b of the participant-mining track: census the FIELDS on Lighter's
`trade/{market}` WS channel before extending the collector's schema.

`ws_probe_book.py` shows one frame pretty, which is enough to learn the field
NAMES. It is not enough to learn which fields are ALWAYS there. That distinction
decides the storage schema: a field present on every trade can be required (and
a missing one is a bug worth dropping the record over, as `is_maker_ask` already
is); a field that appears conditionally must be optional everywhere downstream.

Getting that backwards is not hypothetical here. `docs/START_participant_mining.md`
records `taker_position_sign_changed` as a confirmed field and builds its worked
example of position reconstruction on it — but the first live `update/trade`
frame captured 2026-07-28 does not carry it at all, while the liquidation
snapshot entries do. Either it is emitted only when the sign really changed
(absent == False), or it is liquidation-only. Those two readings produce
different reconstruction code, and only counting tells them apart.

Reports, per field: how many trades carry it, the JSON types seen, and a sample
value. Also splits regular trades from liquidations, because the first frame
showed they arrive on the same channel in a separate `liquidation_trades` array
— which is itself a finding the book track had been planning a separate probe
for.

Stores nothing. Read-only, no auth, public channel.

Usage:
    python3 probe_trade_fields.py --market 1 --seconds 120
    python3 probe_trade_fields.py --market 1 --trades 500
    python3 probe_trade_fields.py --markets 1 0 2 --seconds 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict

import websockets

DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"

# Two id shapes mean two kinds of participant, not a parsing problem — a
# distinction an earlier session collapsed into one bucket and had to undo.
#
# MEASURED 2026-07-28, and it corrects the handoff doc: the masked ids are NOT
# "2**48 + offset". Every one sampled sits just BELOW 2**48 —
# 281474976511094 = 2**48 - 199562, 281474976623090 = 2**48 - 87566. Whatever
# the encoding is, an exact `>= 2**48` test classifies all of them as plain.
# Split on magnitude instead: observed plain ids are ~10**5-10**6, masked ones
# ~2.8*10**14, so any threshold in between is safe and none is load-bearing.
MASKED_ID_MIN = 10 ** 12


class Census:
    """Field presence / type / sample census over a stream of trade dicts."""

    def __init__(self) -> None:
        self.n = 0
        self.present: Counter = Counter()
        self.types: dict[str, Counter] = defaultdict(Counter)
        self.sample: dict[str, object] = {}
        self.values: dict[str, Counter] = defaultdict(Counter)

    def add(self, t: dict) -> None:
        self.n += 1
        for k, v in t.items():
            self.present[k] += 1
            self.types[k][type(v).__name__] += 1
            self.sample.setdefault(k, v)
            # Booleans and small enums are worth tabulating outright: that is
            # what answers "is absent the same as False?".
            if isinstance(v, bool) or (isinstance(v, str) and len(v) < 12 and k == "type"):
                self.values[k][str(v)] += 1

    def report(self, label: str) -> None:
        print(f"\n===== {label}: {self.n} record(s) =====")
        if not self.n:
            print("  (none seen)")
            return
        print(f"  {'field':<42} {'seen':>6} {'%':>6}  {'types':<14} sample")
        for k, c in sorted(self.present.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = 100.0 * c / self.n
            types = ",".join(sorted(self.types[k]))
            s = repr(self.sample[k])
            if len(s) > 30:
                s = s[:27] + "..."
            flag = "" if c == self.n else "   <-- CONDITIONAL"
            print(f"  {k:<42} {c:>6} {pct:>5.1f}%  {types:<14} {s}{flag}")
        # Always print, even for a single observed value: "sign_changed was
        # True on all 109 trades that carried it" is precisely the evidence that
        # absent means False, and suppressing it as uninteresting hides that.
        for k, counts in sorted(self.values.items()):
            seen = sum(counts.values())
            print(f"  values of {k}: {dict(counts)}  (on {seen}/{self.n} records)")


def classify_accounts(cens_trades: list[dict]) -> None:
    """Short ids vs 2**48-masked sub-account ids, both sides."""
    shape: Counter = Counter()
    accounts: Counter = Counter()
    for t in cens_trades:
        for side in ("ask_account_id", "bid_account_id"):
            v = t.get(side)
            if v is None:
                shape[f"{side}: absent"] += 1
                continue
            kind = "masked(~2^48)" if int(v) >= MASKED_ID_MIN else "plain"
            shape[f"{side}: {kind}"] += 1
            accounts[int(v)] += 1
    print("\n===== account id shapes =====")
    for k, c in sorted(shape.items()):
        print(f"  {k:<38} {c:>6}")
    print(f"\n  distinct accounts seen: {len(accounts)}")
    print("  most active (id, appearances):")
    for acc, c in accounts.most_common(10):
        kind = "msk" if acc >= MASKED_ID_MIN else "   "
        print(f"    {kind} {acc:<20} {c}")


async def probe(ws_url: str, markets: list[int], seconds: int, want_trades: int) -> None:
    print(f"connecting {ws_url}  markets={markets}")
    trades_cens, liq_cens = Census(), Census()
    raw_trades: list[dict] = []
    frames = 0
    per_frame: Counter = Counter()

    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        for m in markets:
            await ws.send(json.dumps({"type": "subscribe", "channel": f"trade/{m}"}))
            print(f"-> subscribe trade/{m}")

        t0 = time.time()
        while time.time() - t0 < seconds and trades_cens.n < want_trades:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
            except asyncio.TimeoutError:
                print(f"[{time.time()-t0:5.1f}s] (no frame in 10s)")
                continue
            except json.JSONDecodeError as e:
                print(f"non-JSON frame: {e}")
                continue

            mtype = msg.get("type", "")
            if mtype == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
            # The (re)connect snapshot replays ~50 old trades. Counting them
            # would mix a historical sample into a live one, and the collector
            # deliberately does not persist it either.
            if mtype.startswith("subscribed/"):
                snap = msg.get("liquidation_trades") or []
                print(f"snapshot: {len(msg.get('trades') or [])} trades, "
                      f"{len(snap)} liquidations (not counted)")
                continue
            if not mtype.startswith("update/trade"):
                continue

            frames += 1
            ts = msg.get("trades") or []
            lqs = msg.get("liquidation_trades") or []
            per_frame[len(ts)] += 1
            for t in ts:
                trades_cens.add(t)
                raw_trades.append(t)
            for t in lqs:
                liq_cens.add(t)

    dur = time.time() - t0
    print(f"\ncollected in {dur:.0f}s: {frames} frames, "
          f"{trades_cens.n} trades, {liq_cens.n} liquidations")
    print(f"frame-level keys seen: {sorted(msg.keys())}")
    print(f"trades per frame: {dict(sorted(per_frame.items()))}")

    trades_cens.report("update/trade -> trades[]")
    liq_cens.report("update/trade -> liquidation_trades[]")
    classify_accounts(raw_trades)

    missing = [k for k, c in trades_cens.present.items() if c != trades_cens.n]
    print("\n===== schema conclusion =====")
    print(f"  always present ({trades_cens.n} trades): "
          f"{len([k for k in trades_cens.present if k not in missing])} fields")
    if missing:
        print("  CONDITIONAL — must be optional in storage and downstream:")
        for k in sorted(missing):
            print(f"    {k}  ({trades_cens.present[k]}/{trades_cens.n})")
    else:
        print("  no conditional fields in this sample")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", type=int, default=1)
    ap.add_argument("--markets", type=int, nargs="+", default=None,
                    help="probe several markets at once (overrides --market)")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--trades", type=int, default=10**9,
                    help="stop early once this many trades are censused")
    args = ap.parse_args()

    markets = args.markets or [args.market]
    ws_url = os.environ.get("LIGHTER_WS_URL") or DEFAULT_WS
    try:
        asyncio.run(probe(ws_url, markets, args.seconds, args.trades))
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
