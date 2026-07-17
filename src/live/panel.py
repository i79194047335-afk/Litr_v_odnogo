"""Minimal trading panel for Lighter testnet — Streamlit.

This panel has NO authentication and places real orders. `.streamlit/
config.toml` binds it to loopback for that reason; reach it over an SSH
tunnel, never by exposing the port.

Usage — on the VPS:
    source .venv/bin/activate
    streamlit run src/live/panel.py

Usage — from your laptop, in another terminal:
    ssh -N -L 8501:127.0.0.1:8501 root@<vps>
    open http://localhost:8501
"""

import asyncio
import json
import os
import time

import aiohttp
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from lighter import SignerClient  # noqa: E402

# ── config ────────────────────────────────────────────────────────────────
TESTNET_URL = "https://testnet.zklighter.elliot.ai"
ACCOUNT_INDEX = 306
ETH_MARKET = 0  # ETH-PERP
BTC_MARKET = 1  # BTC-PERP


def _api_key_index() -> int:
    """Which API key slot .env's key belongs to. No default on purpose.

    Slot 0 belongs to the Lighter web UI ("0 (Desktop)"), which re-registers
    it and silently invalidates a bot's key. Defaulting to 0 is how this
    file lost its key to the browser twice on 2026-07-17.

    Raises rather than calling st.error: this runs before set_page_config,
    where Streamlit commands are not allowed yet.
    """
    raw = os.getenv("TESTNET_API_KEY_INDEX")
    if raw is None:
        raise RuntimeError(
            "TESTNET_API_KEY_INDEX not set in .env — refusing to guess a slot. "
            "Use the index you issued the key under (not 0 — that one belongs "
            "to the Lighter web UI and gets re-registered under you)."
        )
    return int(raw)


API_KEY_INDEX = _api_key_index()

# Taker orders are IOC: priced exactly at top-of-book they simply don't fill
# if the market moves a tick.  This is the acceptable-price buffer.
MAX_SLIPPAGE = 0.005  # 0.5%

st.set_page_config(page_title="Lighter Panel", page_icon="📊", layout="wide")


# ═══════════════════════════════════════════════════════════════════════════
# async plumbing
# ═══════════════════════════════════════════════════════════════════════════

# Two constraints pull in opposite directions here.
#
# 1. Streamlit runs its script synchronously — there is NO running event loop
#    during module execution or widget rendering.  SignerClient.__init__
#    builds an aiohttp TCPConnector, which needs one.  So we must own a loop
#    and drive every async call through run_until_complete().
#
# 2. Streamlit re-executes this file top-to-bottom on EVERY rerun (any click).
#    A module-level asyncio.new_event_loop() therefore makes a *new* loop per
#    click — while @st.cache_resource keeps the SignerClient from the first
#    render, whose aiohttp session is bound to the loop it was built on.
#    aiohttp's timer then calls current_task() against its original loop, gets
#    None, and the second click dies with "Timeout context manager should be
#    used inside a task".  That is exactly what happened on 2026-07-17.
#
# So the loop must be cached the same way the client is: same lifetime, same
# loop, or the client outlives the loop it belongs to.
#
# Known limit: @st.cache_resource is shared across browser sessions, and a
# loop is not safe to drive from several threads at once.  Fine for a
# single-operator panel; revisit if this is ever opened to two users.


@st.cache_resource
def _get_loop() -> asyncio.AbstractEventLoop:
    """One event loop for the whole Streamlit process — see above."""
    return asyncio.new_event_loop()


def _run_async(coro):
    """Run an async coroutine on the cached loop (blocking)."""
    return _get_loop().run_until_complete(coro)


# ═══════════════════════════════════════════════════════════════════════════
# client
# ═══════════════════════════════════════════════════════════════════════════


@st.cache_resource
def _get_client() -> SignerClient:
    """Create and cache the SignerClient on the persistent loop."""
    pk = os.getenv("TESTNET_PRIVATE_KEY", "")
    if pk.startswith("0x"):
        pk = pk[2:]

    async def _construct():
        return SignerClient(
            url=TESTNET_URL,
            account_index=ACCOUNT_INDEX,
            api_private_keys={API_KEY_INDEX: pk},
        )

    client = _run_async(_construct())
    err = client.check_client()
    if err:
        st.error(f"Client check failed: {err}")
        st.stop()
    return client


_client: SignerClient | None = None


def get_client() -> SignerClient:
    global _client
    if _client is None:
        _client = _get_client()
    return _client


def _get_auth_token(client: SignerClient) -> str:
    """Get a fresh auth token for private API calls."""
    token, err = client.create_auth_token_with_expiry(api_key_index=API_KEY_INDEX)
    if err:
        st.error(f"Auth token error: {err}")
        st.stop()
    return token


# ── raw HTTP for public endpoints (bypassing SDK param-name bugs) ─────────


async def _fetch_account() -> dict:
    """Fetch account info via public REST API."""
    url = f"{TESTNET_URL}/api/v1/account?by=index&value={ACCOUNT_INDEX}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                accts = data.get("accounts", [])
                return accts[0] if accts else {}
            return {}


def fetch_account() -> dict:
    return _run_async(_fetch_account())


# ── order helpers ─────────────────────────────────────────────────────────


async def _fetch_market_meta() -> dict:
    url = f"{TESTNET_URL}/api/v1/orderBooks"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()


@st.cache_data(ttl=3600)
def _market_meta() -> dict:
    """Per-market decimals, read from the exchange.

    These were hardcoded once and drifted: SOL was 2/4 against a real 3/3,
    which sends SOL orders at 1/10 the price and 10x the size.  Scaling is
    the exchange's fact to state, not ours to remember.
    """
    meta = {}
    for ob in _run_async(_fetch_market_meta()).get("order_books", []):
        meta[int(ob["market_id"])] = {
            "symbol": ob.get("symbol") or str(ob["market_id"]),
            "price_decimals": int(ob["supported_price_decimals"]),
            "size_decimals": int(ob["supported_size_decimals"]),
        }
    if not meta:
        st.error("Could not read market metadata — refusing to guess scaling.")
        st.stop()
    return meta


def _market_field(market_id: int, field: str):
    try:
        return _market_meta()[market_id][field]
    except KeyError:
        # Guessing a default here is what caused the SOL 10x bug.
        st.error(f"No {field} for market {market_id} — refusing to guess.")
        st.stop()


def _position_field(position: dict, field: str):
    """Read a position field that decides what order goes out. Never guess.

    A missing field means we do not know the position — the honest move is
    to stop, not to pick a plausible value and trade on it.
    """
    if field not in position:
        st.error(f"Position is missing {field!r} — refusing to guess: {position}")
        st.stop()
    return position[field]


def _market_symbol(market_id: int) -> str:
    return _market_field(market_id, "symbol")


def _price_decimals(market_id: int) -> int:
    return _market_field(market_id, "price_decimals")


def _size_decimals(market_id: int) -> int:
    return _market_field(market_id, "size_decimals")


def _ticks_to_price(ticks: int, market_id: int) -> float:
    return ticks / (10 ** _price_decimals(market_id))


def _price_to_ticks(price: float, market_id: int) -> int:
    # round(), never int(): scaling a float lands just under the integer
    # (0.29 * 100 == 28.999999999999996), and int() truncates that to 28.
    return round(price * (10 ** _price_decimals(market_id)))


def _ticks_to_size(ticks: int, market_id: int) -> float:
    return ticks / (10 ** _size_decimals(market_id))


def _size_to_ticks(size: float, market_id: int) -> int:
    # See _price_to_ticks. Truncating here under-closes a position and
    # leaves dust behind: 0.29 would go out as 0.28.
    return round(size * (10 ** _size_decimals(market_id)))


# ═══════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════

st.title("📊 Lighter Testnet Panel")
st.caption(f"Account {ACCOUNT_INDEX}  ·  {TESTNET_URL}")

# ── refresh trigger ───────────────────────────────────────────────────────

if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = 0.0

now = time.time()
auto_refresh = now - st.session_state.last_refresh > 5.0  # auto every 5 s


def do_refresh():
    st.session_state.last_refresh = time.time()


# ── sidebar: account ──────────────────────────────────────────────────────

with st.sidebar:
    st.header("Account")
    if st.button("🔄 Refresh", use_container_width=True):
        do_refresh()

    account = fetch_account()

    collat = float(account.get("collateral", 0))
    avail = float(account.get("available_balance", 0))
    st.metric("Collateral", f"${collat:,.2f}")
    st.metric("Available", f"${avail:,.2f}")

    st.divider()
    st.subheader("Assets")
    for a in account.get("assets", []):
        bal = float(a.get("balance", 0))
        if bal > 0:
            st.text(f"{a['symbol']}:  {bal:,.6f}")

    st.divider()
    st.subheader("Positions")
    positions = account.get("positions", [])
    has_position = False
    for p in positions:
        size = float(p.get("position", 0))
        if size != 0:
            has_position = True
            sign = p.get("sign", 1)
            side = "🟢 LONG" if sign > 0 else "🔴 SHORT"
            entry = float(p.get("avg_entry_price", 0))
            upnl = float(p.get("unrealized_pnl", 0))
            upnl_color = "green" if upnl >= 0 else "red"
            st.text(f"{p['symbol']} {side}")
            st.text(f"  Size: {abs(size):,.4f}  @ ${entry:,.2f}")
            st.markdown(f"  uPnL: :{upnl_color}[${upnl:,.2f}]")
    if not has_position:
        st.text("(no open positions)")

# Init client now — Streamlit's event loop is running.
client = get_client()

# ── main panel ────────────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs(["📈 Trading", "📋 Orders", "📖 Order Book"])

# ── Tab 1: Trading ────────────────────────────────────────────────────────

with tab1:
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Place Order")

        market_id = st.selectbox(
            "Market",
            options=[0, 1, 2],
            format_func=lambda m: _market_symbol(m),
        )
        side = st.radio("Side", ["BUY", "SELL"], horizontal=True)
        is_ask = side == "SELL"

        price_mode = st.radio(
            "Price mode", ["Market", "Limit"], horizontal=True, key="price_mode"
        )

        if price_mode == "Limit":
            limit_price = st.number_input(
                "Limit price ($)",
                min_value=0.01,
                value=1800.0,
                step=1.0,
                format="%.2f",
            )
            price_ticks = _price_to_ticks(limit_price, market_id)
            st.caption(f"= {price_ticks} ticks")
        else:
            st.caption("Market order — fills at best available price")
            limit_price = None
            price_ticks = None

        size_eth = st.number_input(
            "Size (base units)",
            min_value=0.001,
            value=0.01,
            step=0.01,
            format="%.4f",
        )
        size_ticks = _size_to_ticks(size_eth, market_id)
        st.caption(
            f"= {size_ticks} ticks  ·  {_size_decimals(market_id)} decimals"
        )

        if st.button(
            f"{'🔴' if is_ask else '🟢'} {side} {_market_symbol(market_id)}",
            use_container_width=True,
            type="primary",
        ):
            do_refresh()
            if price_mode == "Limit":
                with st.spinner("Placing limit order..."):
                    _, tx_resp, err = _run_async(
                        client.create_order(
                            market_index=market_id,
                            client_order_index=int(time.time() * 1000) % 2**31,
                            base_amount=size_ticks,
                            price=price_ticks,
                            is_ask=is_ask,
                            order_type=SignerClient.ORDER_TYPE_LIMIT,
                            time_in_force=SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY,
                        )
                    )
            else:
                with st.spinner("Placing market order..."):
                    _, tx_resp, err = _run_async(
                        client.create_market_order_limited_slippage(
                            market_index=market_id,
                            client_order_index=int(time.time() * 1000) % 2**31,
                            base_amount=size_ticks,
                            max_slippage=MAX_SLIPPAGE,
                            is_ask=is_ask,
                        )
                    )

            if err:
                st.error(f"Order failed: {err}")
            else:
                st.success(f"✓ Sent!  Tx: `{tx_resp.tx_hash}`")
                st.info(f"Response: {tx_resp.message}")

    with col2:
        st.subheader("Close Position")
        open_positions = [
            p
            for p in account.get("positions", [])
            if float(p.get("position", 0)) != 0
        ]
        if not open_positions:
            st.text("(no open positions)")
        else:
            for p in open_positions:
                pos_size = float(p.get("position", 0))
                # market_id and sign decide which market and which side the
                # close order goes to. A default here is not a fallback, it
                # is a wrong order: market_id 0 would aim at ETH, sign 1
                # would sell a short. Same rule as the decimals.
                pos_market = _position_field(p, "market_id")
                pos_sign = _position_field(p, "sign")
                pos_symbol = p.get("symbol", str(pos_market))
                # Close = trade against the position: long (sign > 0) → sell.
                close_side = pos_sign > 0

                with st.container(border=True):
                    st.markdown(
                        f"**{pos_symbol}**  {'🔴 SHORT' if pos_sign < 0 else '🟢 LONG'}  `{abs(pos_size):,.4f}`"
                    )
                    if st.button(
                        f"Close {pos_symbol}",
                        key=f"close_{pos_market}",
                        use_container_width=True,
                    ):
                        do_refresh()
                        with st.spinner(f"Closing {pos_symbol}..."):
                            _, tx_resp, err = _run_async(
                                client.create_market_order_limited_slippage(
                                    market_index=pos_market,
                                    client_order_index=int(time.time() * 1000)
                                    % 2**31,
                                    base_amount=abs(
                                        _size_to_ticks(pos_size, pos_market)
                                    ),
                                    max_slippage=MAX_SLIPPAGE,
                                    is_ask=close_side,
                                    reduce_only=True,
                                )
                            )
                        if err:
                            st.error(f"Close failed: {err}")
                        else:
                            st.success(f"✓ Close sent: `{tx_resp.tx_hash}`")

# ── Tab 2: Active Orders ──────────────────────────────────────────────────

with tab2:
    st.subheader("Active Orders")

    if st.button("🔄 Load Orders") or auto_refresh:
        do_refresh()
        auth = _get_auth_token(client)
        active = _run_async(
            client.order_api.account_active_orders(
                authorization=auth,
                account_index=ACCOUNT_INDEX,
            )
        )
        orders = getattr(active, "orders", []) or []
        if orders:
            rows = []
            for o in orders:
                # SDK types these as strings ("1800.50"), already scaled.
                price = float(o.price)
                size = float(o.remaining_base_amount)
                side = "SELL" if o.is_ask else "BUY"
                rows.append(
                    {
                        "Order ID": o.order_index,
                        "Market": _market_symbol(o.market_index),
                        "Side": side,
                        "Price": f"${price:,.2f}",
                        "Size": f"{size:,.4f}",
                        "Status": o.status,
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # cancel button
            st.divider()
            cancel_id = st.number_input(
                "Order ID to cancel", min_value=0, step=1, value=0
            )
            cancel_mkt = st.selectbox(
                "Market for cancel",
                options=[0, 1, 2],
                format_func=lambda m: _market_symbol(m),
                key="cancel_mkt",
            )
            if st.button("❌ Cancel Order") and cancel_id > 0:
                do_refresh()
                _, cancel_resp, cancel_err = _run_async(
                    client.cancel_order(cancel_mkt, cancel_id)
                )
                if cancel_err:
                    if "nonce" in str(cancel_err).lower():
                        st.warning("Nonce desync — retrying once...")
                        _, cancel_resp, cancel_err = _run_async(
                            client.cancel_order(cancel_mkt, cancel_id)
                        )
                if cancel_err:
                    st.error(f"Cancel failed: {cancel_err}")
                else:
                    st.success(f"✓ Cancelled: `{cancel_resp.tx_hash}`")
        else:
            st.text("(no active orders)")

# ── Tab 3: Order Book ─────────────────────────────────────────────────────

with tab3:
    st.subheader("Order Book")

    ob_market = st.selectbox(
        "Market",
        options=[0, 1, 2],
        format_func=lambda m: _market_symbol(m),
        key="ob_market",
    )
    ob_depth = st.slider("Depth", 1, 20, 5, key="ob_depth")

    if st.button("📖 Show Order Book") or auto_refresh:
        do_refresh()
        ob = _run_async(
            client.order_api.order_book_orders(
                market_id=ob_market, limit=ob_depth
            )
        )

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Asks (Sells)**")
            ask_rows = []
            for o in ob.asks[:ob_depth]:
                price = _ticks_to_price(
                    int(o.price.replace(".", "")), ob_market
                )
                size = _ticks_to_size(
                    int(o.remaining_base_amount.replace(".", "")), ob_market
                )
                ask_rows.append(
                    {"Price": f"${price:,.2f}", "Size": f"{size:,.4f}"}
                )
            if ask_rows:
                st.dataframe(ask_rows, use_container_width=True, hide_index=True)
            else:
                st.text("(empty)")

        with col_b:
            st.markdown("**Bids (Buys)**")
            bid_rows = []
            for o in ob.bids[:ob_depth]:
                price = _ticks_to_price(
                    int(o.price.replace(".", "")), ob_market
                )
                size = _ticks_to_size(
                    int(o.remaining_base_amount.replace(".", "")), ob_market
                )
                bid_rows.append(
                    {"Price": f"${price:,.2f}", "Size": f"{size:,.4f}"}
                )
            if bid_rows:
                st.dataframe(bid_rows, use_container_width=True, hide_index=True)
            else:
                st.text("(empty)")

        spread_pct = 0
        if ob.bids and ob.asks:
            best_bid = _ticks_to_price(
                int(ob.bids[0].price.replace(".", "")), ob_market
            )
            best_ask = _ticks_to_price(
                int(ob.asks[0].price.replace(".", "")), ob_market
            )
            spread_pct = ((best_ask - best_bid) / best_bid) * 100
            st.caption(
                f"Spread: ${best_ask - best_bid:,.2f}  ({spread_pct:.4f}%)"
            )

# ── footer ─────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    f"Last refresh: {time.strftime('%H:%M:%S', time.localtime(st.session_state.last_refresh))}  ·  "
    "Auto-refresh on interaction"
)
