"""
Slice 2 of the backtester: the order / fill engine.

Resting orders live in the TICK loop (see replay.py event order: fills are
checked in on_tick, before any bar-close logic reacts). No strategy logic
here — this module only answers "given this tick, which resting orders
filled, and at what price".

FILL RULES (the honesty core — see docs/ytc_scalper_skeleton.md §4.4/§4.6):

  LIMIT orders (maker):
    A resting BUY limit at P fills when a tick trades STRICTLY BELOW P.
    A resting SELL limit at P fills when a tick trades STRICTLY ABOVE P.
    Fill price = the limit price P (you were resting at P; the market moved
    through you).

    "Through, not touch": a tick AT exactly P does NOT fill. A trade printing
    at your price does not prove your order traded — you were in a FIFO queue
    and that print may have been someone ahead of you. Requiring the market
    to trade through the level is the conservative maker approximation: it
    under-fills rather than over-fills, which is the safe direction for a
    backtest. (A configurable fill-probability haircut on top of this comes
    in a later slice — deliberately NOT baked in here, so the fill model
    stays one visible knob, not a hidden assumption.)

  STOP orders (protective, taker):
    A SELL stop at P triggers when a tick trades AT OR BELOW P.
    A BUY  stop at P triggers when a tick trades AT OR ABOVE P.
    Fill price = the TRIGGERING TICK's price, not the stop price. A stop is
    a market order once touched; if price gapped past the stop between ticks,
    you fill at the gapped price — that's real slippage-by-gap and it is NOT
    softened. (Additional fixed slippage on top comes with the costs slice.)
    Touch (not through) is correct here: it's the aggressive side, and
    assuming the worse of the two readings for OUR positions means stops
    trigger as early as reality would, or earlier — again the safe direction.

  Asymmetry is intentional: limits (in our favour) fill pessimistically,
  stops (against us) trigger pessimistically. An honest backtest errs
  against itself on both sides.

SIMPLIFICATIONS (documented, revisit in later slices):
  - Fills are all-or-nothing: no partial fills. Beggs notes part-2 orders
    often don't fill AT ALL — that is modeled by through-not-touch plus the
    future fill-rate knob, not by partials. Partial-fill modeling would need
    trade SIZE against queue depth, which tick data alone can't support
    honestly.
  - One tick can fill multiple orders; each order fills at most once and is
    removed from the book on fill.
  - No order expiry/TTL yet — the strategy slice cancels explicitly.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Literal

Side = Literal["buy", "sell"]
Kind = Literal["limit", "stop"]


@dataclass(frozen=True)
class Order:
    id: int
    side: Side
    kind: Kind
    price: float
    size: float
    tag: str = ""      # free-form label ("part1", "part2", "stop") for reports


@dataclass(frozen=True)
class Fill:
    order_id: int
    side: Side
    kind: Kind
    price: float       # execution price (limit: order price; stop: tick price)
    size: float
    ts: int
    tag: str = ""


class FillEngine:
    """Holds resting orders; on_tick() reports fills per the rules above."""

    def __init__(self):
        self._orders: dict[int, Order] = {}
        self._next_id = itertools.count(1)

    # --- order management ----------------------------------------------------

    def place(self, side: Side, kind: Kind, price: float, size: float,
              tag: str = "") -> Order:
        if side not in ("buy", "sell"):
            raise ValueError(f"bad side: {side!r}")
        if kind not in ("limit", "stop"):
            raise ValueError(f"bad kind: {kind!r}")
        if size <= 0:
            raise ValueError(f"size must be > 0, got {size}")
        if price <= 0:
            raise ValueError(f"price must be > 0, got {price}")
        order = Order(next(self._next_id), side, kind, price, size, tag)
        self._orders[order.id] = order
        return order

    def cancel(self, order_id: int) -> bool:
        """Cancel a resting order. Returns False if it wasn't resting
        (already filled or never existed) — caller decides if that matters."""
        return self._orders.pop(order_id, None) is not None

    def cancel_all(self) -> int:
        n = len(self._orders)
        self._orders.clear()
        return n

    @property
    def open_orders(self) -> list[Order]:
        return list(self._orders.values())

    # --- the fill check --------------------------------------------------------

    def on_tick(self, price: float, ts: int) -> list[Fill]:
        """Check every resting order against one tick. Returns fills
        (possibly several). Filled orders are removed from the book."""
        fills: list[Fill] = []
        for order in list(self._orders.values()):
            filled_at: float | None = None

            if order.kind == "limit":
                if order.side == "buy" and price < order.price:
                    filled_at = order.price          # rested at P, traded through
                elif order.side == "sell" and price > order.price:
                    filled_at = order.price
            else:  # stop
                if order.side == "sell" and price <= order.price:
                    filled_at = price                # market order at the tick
                elif order.side == "buy" and price >= order.price:
                    filled_at = price

            if filled_at is not None:
                del self._orders[order.id]
                fills.append(Fill(order.id, order.side, order.kind,
                                  filled_at, order.size, ts, order.tag))
        return fills
