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
    fill-probability knob below, not by partials. Partial-fill modeling
    would need trade SIZE against queue depth, which tick data alone can't
    support honestly.
  - One tick can fill multiple orders; each order fills at most once and is
    removed from the book on fill.
  - No order expiry/TTL yet — the strategy slice cancels explicitly.

SLICE 4 ADDITIONS — slippage and fill-probability, both default to neutral:

  SLIPPAGE (stops only): `slippage_ticks` * `tick_size` added in the adverse
    direction on top of the tick price a stop already fills at. Applies
    ONLY to stops. A maker limit fill gets exactly the resting price by
    definition — that's what "maker" means; slippage as a concept doesn't
    apply there, and "through, not touch" already does the realistic job
    for queue uncertainty on that side. This stacks with gap slippage
    (already unmitigated): a gapped stop fill is adjusted by slippage_ticks
    on top of the gapped tick price. slippage_ticks=0 (default) -> byte-
    identical to Slice 2. Setting slippage_ticks > 0 requires tick_size > 0
    (raises otherwise) — a silent no-op from a forgotten tick_size would be
    worse than an explicit error.

  FILL PROBABILITY (limits only): models queue position without needing
    order-book depth data. Each tick that satisfies "price traded through
    my limit" rolls once against `fill_probability`. Fail the roll -> the
    order stays resting (NOT cancelled) and is re-rolled on the next
    qualifying tick. This compounds the way queue intuition suggests it
    should: the longer price stays through your level, the more likely you
    eventually fill. fill_probability=1.0 (default) skips the roll
    entirely — no RNG call happens, so behavior and RNG state are both
    byte-identical to Slice 2. Does NOT apply to stops: touching a stop is
    a deterministic trigger (it becomes a market order), no queue involved
    — only slippage models a stop's imperfect execution.
"""
from __future__ import annotations

import itertools
import random
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

    def __init__(self, slippage_ticks: float = 0.0, tick_size: float = 0.0,
                fill_probability: float = 1.0,
                rng: "random.Random | None" = None):
        if slippage_ticks < 0:
            raise ValueError(f"slippage_ticks must be >= 0, got {slippage_ticks}")
        if slippage_ticks > 0 and tick_size <= 0:
            raise ValueError(
                "tick_size must be > 0 when slippage_ticks > 0 "
                "(a silent no-op here would be worse than this error)"
            )
        if not (0.0 < fill_probability <= 1.0):
            raise ValueError(
                f"fill_probability must be in (0, 1], got {fill_probability}"
            )
        self.slippage_ticks = slippage_ticks
        self.tick_size = tick_size
        self.fill_probability = fill_probability
        self._rng = rng if rng is not None else random.Random()
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
        (possibly several). Filled orders are removed from the book.

        A limit order that qualifies (through-not-touch) but loses its
        fill_probability roll stays resting — it is NOT removed, and will
        be re-rolled on the next qualifying tick (see module docstring)."""
        fills: list[Fill] = []
        for order in list(self._orders.values()):
            filled_at: float | None = None

            if order.kind == "limit":
                through = (order.side == "buy" and price < order.price) or \
                         (order.side == "sell" and price > order.price)
                if not through:
                    continue
                if self.fill_probability < 1.0 and \
                        self._rng.random() >= self.fill_probability:
                    continue          # lost the queue roll — stays resting
                filled_at = order.price      # rested at P, traded through
            else:  # stop
                touched = (order.side == "sell" and price <= order.price) or \
                         (order.side == "buy" and price >= order.price)
                if not touched:
                    continue
                slip = self.slippage_ticks * self.tick_size
                filled_at = price - slip if order.side == "sell" else price + slip

            del self._orders[order.id]
            fills.append(Fill(order.id, order.side, order.kind,
                              filled_at, order.size, ts, order.tag))
        return fills
