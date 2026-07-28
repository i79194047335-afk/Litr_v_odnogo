#!/usr/bin/env python3
"""
STEP 1 of the participant-mining track. Goal is NOT copy-trading — it's to
find out whether we can RECONSTRUCT what other participants (bots + manual)
are doing, RANK them by realised PnL, and later MINE what market conditions
their entries react to (reverse-engineer their triggers into our own system).

This probe answers the go/no-go questions BEFORE we build anything, using the
real SDK methods (authorization is Optional on all three — we test whether
"optional" means "works unauthenticated for FOREIGN accounts"):

  A. OrderApi.recent_trades(market)   — public tape, no auth. Do trade
     objects carry BOTH counterparties' account_ids + position/pnl context?
     (The Trade model says yes; this confirms it on the wire.)
  B. OrderApi.trades(account_index=X) — can we pull a FOREIGN account's full
     trade history unauthenticated? This is the raw material for per-account
     action reconstruction + PnL ranking. THIS ANSWERS days-vs-weeks.
  C. AccountApi.pnl(by=index, value=X) — can we pull a foreign account's PnL
     curve directly, unauthenticated?
  D. account_index persistence: are the ids we see in the tape stable enough
     to track one trader across days?

It prints, per foreign account found in the tape, the rich per-side context
the Trade model exposes (position_before, entry_quote_before, sign_changed,
client_id, maker/taker role) so you can SEE that a participant's intent is
reconstructable, not just their fills.

Reads foreign account_ids straight from the live public tape, so it needs no
input and no auth. Run it, paste the output.

Usage (VPS):
    python probe_participants.py --market 1
    python probe_participants.py --market 1 --sample-accounts 5 --history 50
"""
from __future__ import annotations

import argparse
import os
import asyncio
import sys
import time
from collections import defaultdict

import lighter


def _g(obj, name, default=None):
    """Safe attribute read — SDK sometimes stashes fields in
    additional_properties instead of typed attrs (mark_price precedent)."""
    v = getattr(obj, name, None)
    if v is not None:
        return v
    ap = getattr(obj, "additional_properties", None)
    if isinstance(ap, dict) and name in ap:
        return ap[name]
    return default


async def main_async(market: int, n_tape: int, sample_accounts: int,
                     history: int) -> None:
    host = os.environ.get("LIGHTER_API_BASE") or "https://mainnet.zklighter.elliot.ai"
    client = lighter.ApiClient(
        configuration=lighter.Configuration(host=host))
    order_api = lighter.OrderApi(client)
    account_api = lighter.AccountApi(client)

    try:
        # ---- A. public tape, no auth --------------------------------------
        print("=" * 64)
        print("A. recent_trades — public tape, no auth")
        print("=" * 64)
        t0 = time.time()
        tape = await order_api.recent_trades(market_id=market, limit=n_tape)
        dt = (time.time() - t0) * 1000
        trades = _g(tape, "trades", []) or []
        print(f"got {len(trades)} trades in {dt:.0f} ms")
        if not trades:
            print("EMPTY tape — cannot proceed. Is market id right?")
            return

        first = trades[0]
        print("\nfirst trade — ALL fields the wire actually carries:")
        dumped = 0
        for fname in ("trade_id", "market_id", "price", "size", "timestamp",
                       "is_maker_ask", "ask_account_id", "bid_account_id",
                       "ask_account_pnl", "bid_account_pnl",
                       "taker_position_size_before", "maker_position_size_before",
                       "taker_entry_quote_before", "maker_entry_quote_before",
                       "taker_position_sign_changed", "maker_position_sign_changed",
                       "ask_client_id", "bid_client_id", "taker_fee", "maker_fee"):
            val = _g(first, fname, "<absent>")
            print(f"  {fname:32} {val}")
            dumped += 1

        has_ids = (_g(first, "ask_account_id") is not None
                   and _g(first, "bid_account_id") is not None)
        print(f"\n>>> counterparty account_ids present in PUBLIC tape: "
              f"{'YES' if has_ids else 'NO'} <<<")
        if not has_ids:
            print("If NO: participant mining from the public tape is not")
            print("possible; we'd need our own authed fills only. Stop here.")
            return

        # ---- collect foreign accounts + their activity from the tape ------
        seen = defaultdict(lambda: {"n": 0, "as_maker": 0, "vol": 0.0})
        for tr in trades:
            for role, acc_field, maker_flag in (
                ("ask", "ask_account_id", not bool(_g(tr, "is_maker_ask"))),
                ("bid", "bid_account_id", bool(_g(tr, "is_maker_ask"))),
            ):
                acc = _g(tr, acc_field)
                if acc is None:
                    continue
                seen[acc]["n"] += 1
                seen[acc]["vol"] += float(_g(tr, "size", 0) or 0)
                # maker side = the resting order; is_maker_ask True => ask was maker
                if (acc_field == "ask_account_id") == bool(_g(tr, "is_maker_ask")):
                    seen[acc]["as_maker"] += 1

        ranked = sorted(seen.items(), key=lambda kv: kv[1]["n"], reverse=True)
        print(f"\n{len(seen)} distinct accounts in last {len(trades)} trades. "
              f"Top by trade count (maker% hints bot-vs-taker):")
        print(f"  {'account_id':>12} {'trades':>6} {'maker%':>7} {'volume':>12}")
        for acc, s in ranked[:12]:
            mk = 100 * s["as_maker"] / s["n"] if s["n"] else 0
            print(f"  {acc:>12} {s['n']:>6} {mk:>6.0f}% {s['vol']:>12.3f}")

        probe_accts = [acc for acc, _ in ranked[:sample_accounts]]

        # ---- B. foreign account trade history, no auth --------------------
        print("\n" + "=" * 64)
        print("B. trades(account_index=FOREIGN) — no auth")
        print("=" * 64)
        for acc in probe_accts:
            try:
                r = await order_api.trades(
                    sort_by="timestamp", limit=history, account_index=acc)
                rows = _g(r, "trades", []) or []
                if rows:
                    span_ms = (int(_g(rows[0], "timestamp", 0))
                               - int(_g(rows[-1], "timestamp", 0)))
                    hrs = abs(span_ms) / 3.6e6
                    print(f"  acct {acc}: {len(rows)} trades OK, "
                          f"spanning ~{hrs:.1f}h  <-- foreign history READABLE")
                else:
                    print(f"  acct {acc}: returned empty")
            except lighter.ApiException as e:
                print(f"  acct {acc}: BLOCKED status={e.status} "
                      f"(likely needs auth) — {str(e.body)[:80]}")
                break

        # ---- C. foreign account PnL curve, no auth ------------------------
        print("\n" + "=" * 64)
        print("C. pnl(by=index, value=FOREIGN) — no auth")
        print("=" * 64)
        now_s = int(time.time())
        for acc in probe_accts[:3]:
            try:
                r = await account_api.pnl(
                    by="index", value=str(acc), resolution="1h",
                    start_timestamp=(now_s - 7 * 86400) * 1000,
                    end_timestamp=now_s * 1000, count_back=0)
                pts = _g(r, "pnl", []) or []
                print(f"  acct {acc}: pnl curve {len(pts)} points OK "
                      f"<-- foreign PnL READABLE")
            except lighter.ApiException as e:
                print(f"  acct {acc}: BLOCKED status={e.status} "
                      f"(likely needs auth) — {str(e.body)[:80]}")
                break

        # ---- verdict ------------------------------------------------------
        print("\n" + "=" * 64)
        print("VERDICT — read before building")
        print("=" * 64)
        print("If A=YES and B/C readable for foreign accounts:")
        print("  -> the 26 days already on disk lack account_ids (collector")
        print("     discards them), BUT we can (i) re-collect the tape WITH")
        print("     ids going forward, and (ii) backfill any foreign account's")
        print("     full history on demand via B. Ranking + action")
        print("     reconstruction start in DAYS, not weeks.")
        print("If B/C are BLOCKED (auth-only): we can still rank from the")
        print("  public tape (A) as it streams, but cannot backfill history")
        print("  per account — slower, forward-only.")
        print("\nNext after this: for the top profitable accounts, align their")
        print("entry timestamps against market state (our tape features +")
        print("later a CEX feed) and permutation-test which conditions precede")
        print("their entries vs a baseline — same stats engine as")
        print("diag_take_vs_rest, repurposed from 'our trades' to 'their")
        print("trades'. THAT is the reverse-engineering step.")
    finally:
        await client.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", type=int, default=1)
    ap.add_argument("--tape", type=int, default=100,
                     help="how many recent public trades to pull (max 100)")
    ap.add_argument("--sample-accounts", type=int, default=5,
                     help="how many top accounts to test B/C against")
    ap.add_argument("--history", type=int, default=50,
                     help="trades to request per foreign account in B (max 100)")
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args.market, min(args.tape, 100),
                                args.sample_accounts, min(args.history, 100)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
