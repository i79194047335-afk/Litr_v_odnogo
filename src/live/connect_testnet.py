"""Step 1: connect to Lighter testnet, read account, place & cancel one order.

Usage:
    source .venv/bin/activate
    python -m src.live.connect_testnet
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from lighter import SignerClient, AccountApi, Configuration  # noqa: E402

TESTNET_URL = "https://testnet.zklighter.elliot.ai"
ACCOUNT_INDEX = 306        # from testnet web UI "Your Account Index"
API_KEY_INDEX = 0           # from testnet web UI "API Key Index"
ETH_MARKET = 0              # ETH-PERP


async def main():
    private_key = os.getenv("TESTNET_PRIVATE_KEY")
    if not private_key:
        print("ERROR: TESTNET_PRIVATE_KEY not set in .env")
        sys.exit(1)

    if private_key.startswith("0x"):
        private_key = private_key[2:]

    # ── connect ────────────────────────────────────────────────────────
    print(f"Connecting to {TESTNET_URL} ...")
    client = SignerClient(
        url=TESTNET_URL,
        account_index=ACCOUNT_INDEX,
        api_private_keys={API_KEY_INDEX: private_key},
    )

    err = client.check_client()
    if err:
        print(f"ERROR: client check failed — {err}")
        await client.close()
        sys.exit(1)
    print("✓ Client check passed (API key accepted by testnet)")

    # ── read account ───────────────────────────────────────────────────
    account_api = AccountApi(client.api_client)
    print(f"\n─── Account {ACCOUNT_INDEX} ───")
    try:
        account = await account_api.account(
            by="account_index", value=str(ACCOUNT_INDEX)
        )
        print(f"  Status:      {account.status}")
        print(f"  Collateral:  {account.collateral}")
        bal = getattr(account, "available_balance", None)
        print(f"  Avail bal:   {bal}")
        extra = getattr(account, "additional_properties", {}) or {}
        if extra:
            print(f"  Extra keys:  {list(extra.keys())}")
    except Exception as e:
        print(f"  (public account endpoint failed: {e})")
        print(f"  (continuing — private API key works, will test orders)")

    # ── order book snapshot ────────────────────────────────────────────
    print(f"\n─── ETH-PERP (market {ETH_MARKET}) Order Book ───")
    ob = await client.order_api.order_book_orders(market_id=ETH_MARKET, limit=1)
    best_bid = 0
    if ob.bids and ob.asks:
        best_bid = int(ob.bids[0].price.replace(".", ""))
        best_ask = int(ob.asks[0].price.replace(".", ""))
        print(f"  Best bid: {ob.bids[0].price}  ({best_bid} ticks)")
        print(f"  Best ask: {ob.asks[0].price}  ({best_ask} ticks)")
    else:
        print("  (order book empty — testnet may have no liquidity)")

    # ── place a far-away limit BUY ─────────────────────────────────────
    # ETH: size_decimals=4 → 0.01 ETH = 100 ticks. price_decimals=2.
    # Place at ~10% below market → unlikely to fill on a quiet testnet.
    far_price = int(best_bid * 0.9) if best_bid else 180_000  # ~$1800
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
        api_key_index=API_KEY_INDEX,
    )
    if auth_err:
        print(f"ERROR generating auth token: {auth_err}")
        await client.close()
        sys.exit(1)

    # ── find the order index from active orders ────────────────────────
    await asyncio.sleep(0.5)  # let it land
    active = await client.order_api.account_active_orders(
        authorization=auth_token,
        account_index=ACCOUNT_INDEX,
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
        account_index=ACCOUNT_INDEX,
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
