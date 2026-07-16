"""Minimal trading panel for Lighter testnet — Streamlit.

Usage:
    source .venv/bin/activate
    streamlit run src/live/panel.py --server.port 8501
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
API_KEY_INDEX = 0
ETH_MARKET = 0  # ETH-PERP
BTC_MARKET = 1  # BTC-PERP

st.set_page_config(page_title="Lighter Panel", page_icon="📊", layout="wide")


# ═══════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════


def _run_async(coro):
    """Run an async coroutine and return its result (blocking)."""
    return asyncio.run(coro)


@st.cache_resource
def _get_client() -> SignerClient:
    """Create and cache the SignerClient — survives Streamlit reruns."""
    pk = os.getenv("TESTNET_PRIVATE_KEY", "")
    if pk.startswith("0x"):
        pk = pk[2:]
    client = SignerClient(
        url=TESTNET_URL,
        account_index=ACCOUNT_INDEX,
        api_private_keys={API_KEY_INDEX: pk},
    )
    err = client.check_client()
    if err:
        st.error(f"Client check failed: {err}")
        st.stop()
    return client


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


def _market_symbol(market_id: int) -> str:
    return {0: "ETH", 1: "BTC", 2: "SOL", 24: "HYPE", 92: "XAU"}.get(
        market_id, str(market_id)
    )


def _price_decimals(market_id: int) -> int:
    return {0: 2, 1: 1, 2: 2, 24: 3, 92: 1}.get(market_id, 2)


def _size_decimals(market_id: int) -> int:
    return {0: 4, 1: 5, 2: 4, 24: 4, 92: 4}.get(market_id, 4)


def _ticks_to_price(ticks: int, market_id: int) -> float:
    return ticks / (10 ** _price_decimals(market_id))


def _price_to_ticks(price: float, market_id: int) -> int:
    return int(price * (10 ** _price_decimals(market_id)))


def _ticks_to_size(ticks: int, market_id: int) -> float:
    return ticks / (10 ** _size_decimals(market_id))


def _size_to_ticks(size: float, market_id: int) -> int:
    return int(size * (10 ** _size_decimals(market_id)))


# ═══════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════

st.title("📊 Lighter Testnet Panel")
st.caption(f"Account {ACCOUNT_INDEX}  ·  {TESTNET_URL}")

client = _get_client()

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
                    best = _run_async(
                        client.order_api.order_book_orders(
                            market_id=market_id, limit=1
                        )
                    )
                    ref_price = int(
                        best.bids[0].price.replace(".", "")
                        if is_ask
                        else best.asks[0].price.replace(".", "")
                    )
                    _, tx_resp, err = _run_async(
                        client.create_market_order(
                            market_index=market_id,
                            client_order_index=int(time.time() * 1000) % 2**31,
                            base_amount=size_ticks,
                            avg_execution_price=ref_price,
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
                pos_market = p.get("market_id", 0)
                pos_symbol = p.get("symbol", str(pos_market))
                pos_sign = p.get("sign", 1)
                close_side = pos_sign < 0  # opposite of position sign: long→sell, short→buy

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
                                client.create_market_order(
                                    market_index=pos_market,
                                    client_order_index=int(time.time() * 1000)
                                    % 2**31,
                                    base_amount=abs(
                                        _size_to_ticks(pos_size, pos_market)
                                    ),
                                    avg_execution_price=0,  # will be overridden
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
                price = _ticks_to_price(o.price, o.market_index)
                size = _ticks_to_size(o.remaining_base_amount, o.market_index)
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
