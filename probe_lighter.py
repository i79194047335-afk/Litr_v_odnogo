"""
Probe script: hits live Lighter REST API, prints RAW responses.
No SDK, no keys — just `requests`. Goal: settle 3 unknowns empirically:
  1. which `resolution` strings /api/v1/candles actually accepts
  2. whether candle `t` is seconds or milliseconds
  3. whether `v`/`V` are actually populated (or silently 0/missing)

Run: python3 probe_lighter.py
"""
import json
import time
from datetime import datetime, timezone

import requests

BASE = "https://mainnet.zklighter.elliot.ai"
MARKET_ID = 1  # BTC — collector already confirms this ID and that it trades
TIMEOUT = 10


def hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def get(path: str, params: dict | None = None):
    url = BASE + path
    t0 = time.monotonic()
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"  REQUEST FAILED: {e}")
        return None
    dt = time.monotonic() - t0
    print(f"  GET {r.url}")
    print(f"  status={r.status_code}  elapsed={dt:.2f}s")
    try:
        body = r.json()
    except ValueError:
        print(f"  non-JSON body (first 300 chars): {r.text[:300]!r}")
        return None
    return body


def interpret_timestamp(t: int) -> str:
    """Print what `t` decodes to under both unit assumptions."""
    out = []
    for label, divisor in (("as SECONDS", 1), ("as MILLISECONDS", 1000)):
        try:
            dt = datetime.fromtimestamp(t / divisor, tz=timezone.utc)
            out.append(f"{label}: {dt.isoformat()}")
        except (OverflowError, OSError, ValueError):
            out.append(f"{label}: <out of range>")
    return " | ".join(out)


def probe_order_books():
    hr("1. GET /api/v1/orderBooks — market list + precision")
    body = get("/api/v1/orderBooks")
    if not body:
        return
    books = body.get("order_books", [])
    print(f"  total markets returned: {len(books)}")
    btc = next((b for b in books if b.get("market_id") == MARKET_ID), None)
    if btc:
        print("  BTC (market_id=1) raw entry:")
        print(json.dumps(btc, indent=2, ensure_ascii=False))
    else:
        print(f"  market_id={MARKET_ID} not found; first entry as sample:")
        if books:
            print(json.dumps(books[0], indent=2, ensure_ascii=False))


def probe_order_book_details():
    hr("2. GET /api/v1/orderBookDetails?market_id=1 — 24h vol/OI/funding/last price")
    body = get("/api/v1/orderBookDetails", params={"market_id": MARKET_ID})
    if not body:
        return
    print(json.dumps(body, indent=2, ensure_ascii=False))


def probe_candles():
    hr("3. GET /api/v1/candles — resolution sweep + t units + v/V presence")
    now = int(time.time())
    day_ago = now - 60 * 60 * 24
    candidates = ["1m", "5m", "15m", "1h", "4h", "1d", "1", "5", "15", "60", "1440"]

    for res in candidates:
        print(f"\n  --- resolution={res!r} ---")
        body = get(
            "/api/v1/candles",
            params={
                "market_id": MARKET_ID,
                "resolution": res,
                "start_timestamp": day_ago,
                "end_timestamp": now,
                "count_back": 5,
            },
        )
        if body is None:
            continue
        if body.get("code") != 200:
            print(f"  REJECTED: code={body.get('code')} message={body.get('message')!r}")
            continue
        candles = body.get("c", [])
        print(f"  ACCEPTED. candle count returned: {len(candles)}")
        if not candles:
            print("  (accepted but empty — inconclusive for this resolution)")
            continue
        last = candles[-1]
        print("  last candle raw:")
        print(f"    {json.dumps(last, ensure_ascii=False)}")
        t_val = last.get("t")
        if t_val is not None:
            print(f"    t={t_val} -> {interpret_timestamp(t_val)}")
        v_val, V_val = last.get("v"), last.get("V")
        print(f"    v (base vol) = {v_val!r}   V (quote vol) = {V_val!r}")
        if v_val in (None, 0) and V_val in (None, 0):
            print("    NOTE: both v and V are missing/zero on this candle")
        time.sleep(0.3)


if __name__ == "__main__":
    print(f"probing {BASE} — no auth, read-only, nothing gets modified")
    probe_order_books()
    probe_order_book_details()
    probe_candles()
    print("\ndone.")
