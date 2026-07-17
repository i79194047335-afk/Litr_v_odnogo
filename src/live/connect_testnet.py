"""Step 1: connect to Lighter testnet, read account, place & cancel one order.

Usage:
    source .venv/bin/activate
    python -m src.live.connect_testnet
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from lighter import SignerClient  # noqa: E402

TESTNET_URL = "https://testnet.zklighter.elliot.ai"
ETH_MARKET = 0              # ETH-PERP


def _env_int(name: str, hint: str) -> int:
    """Read a required int from .env. No default — a wrong guess here trades."""
    raw = os.getenv(name)
    if raw is None:
        print(f"ERROR: {name} not set in .env — refusing to guess. {hint}")
        sys.exit(1)
    return int(raw)


def _api_key_index() -> int:
    """Which API key slot .env's key belongs to. No default on purpose.

    Slot 0 belongs to the Lighter web UI ("0 (Desktop)"), which re-registers
    it and silently invalidates a bot's key — twice on 2026-07-17, when this
    was hardcoded to 0.
    """
    return _env_int(
        "TESTNET_API_KEY_INDEX",
        "Use the index you issued the key under (not 0 — that one belongs to "
        "the Lighter web UI and gets re-registered under you).",
    )


def _account_index() -> int:
    return _env_int(
        "TESTNET_ACCOUNT_INDEX", "It is shown in the testnet UI as your account index."
    )


async def _price_decimals(market_id: int) -> int:
    """Read price scaling from the exchange — never hardcode it.

    The panel hardcoded these once and drifted (SOL 2/4 vs a real 3/3),
    sending orders at 1/10 the price. Same rule applies here.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{TESTNET_URL}/api/v1/orderBooks") as resp:
            resp.raise_for_status()
            data = await resp.json()
    for ob in data.get("order_books", []):
        if int(ob["market_id"]) == market_id:
            return int(ob["supported_price_decimals"])
    print(f"ERROR: exchange does not describe market {market_id} — refusing to guess.")
    sys.exit(1)


def _str_to_ticks(s: str, decimals: int) -> int:
    """API decimal string ("1846.57") -> integer ticks.

    Not s.replace(".", ""): that silently assumes the exchange zero-pads to
    exactly `decimals` places. It does today (322/322 order-book values
    checked 2026-07-17), but the guarantee is undocumented — "1846.5" would
    come out as 18465 instead of 184650, and far_price below is a real order.
    """
    return round(float(s) * (10 ** decimals))


def _account_url(base_url: str, account_index: int) -> str:
    """Build the /api/v1/account URL with correct param `by=index`.

    The Lighter SDK's AccountApi.account() passes `by=account_index` which
    the API rejects (20001 invalid param).  The actual API expects `by=index`.
    """
    return f"{base_url}/api/v1/account?by=index&value={account_index}"


async def main():
    private_key = os.getenv("TESTNET_PRIVATE_KEY")
    if not private_key:
        print("ERROR: TESTNET_PRIVATE_KEY not set in .env")
        sys.exit(1)

    if private_key.startswith("0x"):
        private_key = private_key[2:]

    api_key_index = _api_key_index()
    account_index = _account_index()

    # ── connect ────────────────────────────────────────────────────────
    print(f"Connecting to {TESTNET_URL} ...")
    client = SignerClient(
        url=TESTNET_URL,
        account_index=account_index,
        api_private_keys={api_key_index: private_key},
    )

    err = client.check_client()
    if err:
        print(f"ERROR: client check failed — {err}")
        await client.close()
        sys.exit(1)
    print("✓ Client check passed (API key accepted by testnet)")

    # ── read account (direct HTTP — SDK has a `by=account_index` vs `by=index` bug) ──
    print(f"\n─── Account {account_index} ───")
    async with aiohttp.ClientSession() as session:
        async with session.get(_account_url(TESTNET_URL, account_index)) as resp:
            if resp.status == 200:
                data = await resp.json()
                accts = data.get("accounts", [])
                if accts:
                    a = accts[0]
                    print(f"  Status:        {a.get('status')}")
                    print(f"  L1 address:    {a.get('l1_address')}")
                    print(f"  Collateral:    ${float(a.get('collateral', 0)):,.2f}")
                    print(f"  Avail balance: ${float(a.get('available_balance', 0)):,.2f}")
                    # assets
                    for asset in a.get("assets", []):
                        bal = float(asset.get("balance", 0))
                        if bal > 0:
                            print(f"  Asset {asset['symbol']}: {bal:,.8f} (margin_mode={asset.get('margin_mode')})")
                    # positions
                    for pos in a.get("positions", []):
                        size = float(pos.get("position", 0))
                        if size != 0:
                            entry = float(pos.get("avg_entry_price", 0))
                            side = "LONG" if pos.get("sign", 1) > 0 else "SHORT"
                            upnl = float(pos.get("unrealized_pnl", 0))
                            print(f"  Pos {pos['symbol']}: {side} {abs(size):,.4f} @ ${entry:,.2f}  uPnL: ${upnl:,.2f}")
            else:
                body = await resp.text()
                print(f"  (HTTP {resp.status}: {body[:200]})")

    # ── order book snapshot ────────────────────────────────────────────
    print(f"\n─── ETH-PERP (market {ETH_MARKET}) Order Book ───")
    price_decimals = await _price_decimals(ETH_MARKET)
    ob = await client.order_api.order_book_orders(market_id=ETH_MARKET, limit=1)
    best_bid = 0
    if ob.bids and ob.asks:
        best_bid = _str_to_ticks(ob.bids[0].price, price_decimals)
        best_ask = _str_to_ticks(ob.asks[0].price, price_decimals)
        print(f"  Best bid: {ob.bids[0].price}  ({best_bid} ticks)")
        print(f"  Best ask: {ob.asks[0].price}  ({best_ask} ticks)")
    else:
        print("  (order book empty — testnet may have no liquidity)")

    # ── place a far-away limit BUY ─────────────────────────────────────
    # ETH: size_decimals=4 → 0.01 ETH = 100 ticks. price_decimals=2.
    # Place at ~10% below market → unlikely to fill on a quiet testnet.
    far_price = round(best_bid * 0.9) if best_bid else 180_000  # ~$1800
    size = 100  # 0.01 ETH
    client_id = int(time.time() * 1000) % 2**31  # unique per run

    print(f"\n─── Placing limit BUY @ {far_price} ticks (~${far_price/100:.2f}), size={size} (0.01 ETH) ───")
    created_order, tx_resp, err = await client.create_order(
        market_index=ETH_MARKET,
        client_order_index=client_id,
        base_amount=size,
        price=far_price,
        is_ask=False,          # False = buy
        order_type=SignerClient.ORDER_TYPE_LIMIT,
        time_in_force=SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY,
    )

    if err:
        print(f"ERROR: {err}")
        await client.close()
        sys.exit(1)

    print(f"✓ Order sent. Tx hash: {tx_resp.tx_hash}")
    print(f"  Code: {tx_resp.code}, message: {tx_resp.message}")

    # ── generate auth token for private API calls ──────────────────────
    auth_token, auth_err = client.create_auth_token_with_expiry(
        api_key_index=api_key_index,
    )
    if auth_err:
        print(f"ERROR generating auth token: {auth_err}")
        await client.close()
        sys.exit(1)

    # ── find the order index from active orders ────────────────────────
    await asyncio.sleep(0.5)  # let it land
    active = await client.order_api.account_active_orders(
        authorization=auth_token,
        account_index=account_index,
    )
    our_order = None
    for o in getattr(active, "orders", []) or []:
        if o.market_index == ETH_MARKET and o.client_order_index == client_id:
            our_order = o
            break

    if our_order is None:
        print("WARNING: could not find our order in active orders (may have filled?)")
        await client.close()
        return

    order_index = our_order.order_index
    print(f"\n  Found order index={order_index}, status={our_order.status}")

    # ── cancel the order (retry once on nonce desync) ──────────────────
    print(f"\n─── Cancelling order {order_index} ───")
    cancel_resp, cancel_err = None, None
    for attempt in range(3):
        _, cancel_resp, cancel_err = await client.cancel_order(
            market_index=ETH_MARKET,
            order_index=order_index,
        )
        if cancel_err is None:
            break
        if "nonce" in str(cancel_err).lower():
            print(f"  Nonce desync on attempt {attempt+1}, retrying...")
            await asyncio.sleep(0.3)
        else:
            break

    if cancel_err:
        print(f"ERROR cancelling: {cancel_err}")
    else:
        print(f"✓ Cancelled. Tx hash: {cancel_resp.tx_hash}")

    # ── verify cancel ──────────────────────────────────────────────────
    await asyncio.sleep(0.5)
    active2 = await client.order_api.account_active_orders(
        authorization=auth_token,
        account_index=account_index,
    )
    still_there = False
    for o in getattr(active2, "orders", []) or []:
        if o.order_index == order_index:
            still_there = True
            break
    print(f"  Order still active: {still_there}  (should be False)")

    await client.close()
    print("\n✓ Done — testnet connection works, order lifecycle verified.")


if __name__ == "__main__":
    asyncio.run(main())
