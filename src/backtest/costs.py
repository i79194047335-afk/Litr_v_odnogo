"""
Slice 4a of the backtester: costs (fees + funding).

Pure post-processing over the Trade list Slice 3 already produces — does not
touch fill mechanics. Answers "what did this trade actually net after costs",
separately from "did it fill and at what price" (Slice 2) and "what's the
raw price PnL" (Slice 3's r_multiple, which is cost-free by design so its
contribution can be isolated from cost's contribution).

FEES: maker on resting fills, taker on market fills.
    Every entry (part1/part2) is a resting limit order -> maker on entry,
    always.
    Exit depends on HOW the trade closed:
        "take"      -> resting limit order       -> maker
        "stop"      -> stop-triggered market fill -> taker
        "reversal"  -> strategy-issued market exit at bar close -> taker
    fee = price * size * (bps / 10_000), summed over entry + exit.
    Lighter's confirmed fee schedule is maker=taker=0.0000 on the markets
    checked (see CONTEXT.md), so this function is built for completeness
    and for markets/venues where that might not hold — at bps=0 it is a
    no-op and every number below is unchanged from Slice 3.

FUNDING: hourly settlement, Lighter's actual mechanism.
    Per docs.lighter.xyz/trading/funding: funding is settled once per hour,
    to whichever account holds a position AT the settlement instant — not
    prorated by how long the position was held within that hour. A trade
    that opens and closes entirely between two hourly boundaries owes (or
    is owed) NOTHING, regardless of duration.
    Consequence for THIS strategy: range-bar scalp trades live minutes
    (see Slice 3 test scenarios — single-digit ticks from entry to exit).
    The overwhelming majority will never straddle an hourly boundary, so
    funding cost will be exactly 0 for almost every trade. It only fires
    for the rare trade still open when an hour rolls over.
    Sign convention (matches Lighter's formula, funding = -position*mark*rate):
        rate > 0 -> longs pay shorts   -> cost is POSITIVE for a long
        rate < 0 -> shorts pay longs   -> cost is POSITIVE for a short
    DATA GAP, named not hidden: we do not collect Lighter's historical
    hourly funding rate. `hourly_rate` defaults to 0.0 (not modeled) until
    that data exists. Building the mechanism now costs nothing; assuming a
    rate we haven't measured would be worse than the honest gap. Price used
    for the funding calc is the trade's entry price (a simplification —
    the true calc uses mark price at each settlement instant, which would
    require a separate mark-price series we don't collect either).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.backtest.strategy import Trade

HOUR_MS = 3_600_000


def hourly_boundaries_crossed(entry_ts: int, exit_ts: int) -> int:
    """Count hourly settlement instants the position was open across.

    Uses the same "bucket index" logic as the 1-minute candle builder and
    the range-bar calibration script: an hour bucket is ts // HOUR_MS.
    Each increase in bucket index between entry and exit is one settlement
    the position was alive for.
    """
    if exit_ts < entry_ts:
        raise ValueError(f"exit_ts {exit_ts} before entry_ts {entry_ts}")
    return (exit_ts // HOUR_MS) - (entry_ts // HOUR_MS)


def funding_cost(side: str, size: float, price: float,
                  entry_ts: int, exit_ts: int, hourly_rate: float = 0.0) -> float:
    """Funding cost in quote currency. Positive = cost to us, negative = income.

    hourly_rate=0.0 (default) -> always 0.0, regardless of duration: funding
    is not modeled until real Lighter funding-rate history is collected.
    """
    if hourly_rate == 0.0:
        return 0.0
    n = hourly_boundaries_crossed(entry_ts, exit_ts)
    if n == 0:
        return 0.0
    sign = 1.0 if side == "long" else -1.0
    return sign * size * price * hourly_rate * n


def fee_cost(trade: Trade, maker_bps: float = 0.0, taker_bps: float = 0.0) -> float:
    """Total fee for one Trade: maker on entry (always), maker or taker on
    exit depending on exit_reason (see module docstring)."""
    entry_fee = trade.entry_price * trade.size * (maker_bps / 10_000)
    exit_bps = maker_bps if trade.exit_reason == "take" else taker_bps
    exit_fee = trade.exit_price * trade.size * (exit_bps / 10_000)
    return entry_fee + exit_fee


@dataclass(frozen=True)
class CostBreakdown:
    trade: Trade
    gross_pnl: float        # price PnL in quote currency (Slice 3's r_multiple,
                             # scaled by size, before any costs)
    fees: float              # total fee cost (positive = cost)
    funding: float           # funding cost (positive = cost, negative = income)
    net_pnl: float           # gross_pnl - fees - funding, TOTAL (size-scaled)
    net_r_multiple: float    # net_pnl / (size * risk) ; 0.0 if size/risk == 0


def apply_costs(trade: Trade, maker_bps: float = 0.0, taker_bps: float = 0.0,
                 hourly_rate: float = 0.0) -> CostBreakdown:
    """Compute the full cost breakdown for one Trade. Pure — does not mutate
    the Trade (Trade is frozen; CostBreakdown wraps it).

    NOTE on net_r_multiple's denominator: Trade.r_multiple (Slice 3) is
    PER-UNIT and size-invariant by construction — it's
    price_pnl_per_unit / price_risk_per_unit, with size cancelled out.
    gross_pnl/net_pnl here are TOTAL dollar amounts (size-scaled), so to
    keep net_r_multiple comparable to r_multiple, it must be normalized by
    size too: net_pnl / (size * risk), not just net_pnl / risk. Verified by
    a test asserting the two are equal when costs are zero.
    """
    if trade.side == "long":
        gross = (trade.exit_price - trade.entry_price) * trade.size
    else:
        gross = (trade.entry_price - trade.exit_price) * trade.size

    fees = fee_cost(trade, maker_bps, taker_bps)
    funding = funding_cost(trade.side, trade.size, trade.entry_price,
                           trade.entry_ts, trade.exit_ts, hourly_rate)
    net = gross - fees - funding
    denom = trade.size * trade.risk
    net_r = net / denom if denom > 0 else 0.0

    return CostBreakdown(trade=trade, gross_pnl=gross, fees=fees,
                         funding=funding, net_pnl=net, net_r_multiple=net_r)


def apply_costs_to_trades(trades: list[Trade], maker_bps: float = 0.0,
                           taker_bps: float = 0.0,
                           hourly_rate: float = 0.0) -> list[CostBreakdown]:
    """Batch convenience. Full aggregation (expectancy, profit factor, max DD
    across many trades) is Slice 5's job, not this module's — this only
    prices each individual trade."""
    return [apply_costs(t, maker_bps, taker_bps, hourly_rate) for t in trades]
