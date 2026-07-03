"""Fill engine tests. Every expected outcome hand-reasoned from the rules:
limit = through-not-touch, fills at limit price;
stop  = touch-triggers, fills at TICK price (gap included)."""
import pytest

from src.backtest.orders import FillEngine, Order, Fill


# ---------------------------------------------------------------------------
# LIMIT: through, not touch
# ---------------------------------------------------------------------------

def test_buy_limit_touch_does_not_fill():
    e = FillEngine()
    e.place("buy", "limit", 100.0, 1.0)
    assert e.on_tick(100.0, 1) == []          # exact touch — queue ambiguity
    assert len(e.open_orders) == 1


def test_buy_limit_fills_when_traded_through_below():
    e = FillEngine()
    o = e.place("buy", "limit", 100.0, 1.0)
    fills = e.on_tick(99.9, 5)
    assert len(fills) == 1
    f = fills[0]
    assert f.order_id == o.id
    assert f.price == 100.0                   # fill at OUR limit price
    assert f.ts == 5
    assert e.open_orders == []                # removed after fill


def test_sell_limit_touch_does_not_fill_but_through_does():
    e = FillEngine()
    e.place("sell", "limit", 100.0, 1.0)
    assert e.on_tick(100.0, 1) == []
    fills = e.on_tick(100.1, 2)
    assert len(fills) == 1
    assert fills[0].price == 100.0


def test_buy_limit_above_market_does_not_fill_on_rising_price():
    # price moving UP through a buy limit must not fill it (that's the wrong
    # direction — a buy limit is below market; price above it means no touch).
    e = FillEngine()
    e.place("buy", "limit", 100.0, 1.0)
    assert e.on_tick(100.5, 1) == []
    assert e.on_tick(101.0, 2) == []
    assert len(e.open_orders) == 1


# ---------------------------------------------------------------------------
# STOP: touch triggers, fills at tick price (gap honesty)
# ---------------------------------------------------------------------------

def test_sell_stop_triggers_on_exact_touch_at_stop_price():
    e = FillEngine()
    e.place("sell", "stop", 95.0, 1.0)
    fills = e.on_tick(95.0, 3)
    assert len(fills) == 1
    assert fills[0].price == 95.0             # tick price == stop price here


def test_sell_stop_gap_through_fills_at_gapped_tick_price():
    # price gaps from 100 straight to 90: the stop at 95 fills at 90,
    # NOT at 95 — gap slippage is not softened.
    e = FillEngine()
    e.place("sell", "stop", 95.0, 1.0)
    assert e.on_tick(100.0, 1) == []
    fills = e.on_tick(90.0, 2)
    assert len(fills) == 1
    assert fills[0].price == 90.0


def test_buy_stop_triggers_at_or_above():
    e = FillEngine()
    e.place("buy", "stop", 105.0, 1.0)
    assert e.on_tick(104.9, 1) == []
    fills = e.on_tick(106.0, 2)               # gapped above
    assert fills[0].price == 106.0


# ---------------------------------------------------------------------------
# book mechanics
# ---------------------------------------------------------------------------

def test_one_tick_can_fill_multiple_orders():
    e = FillEngine()
    e.place("buy", "limit", 100.0, 1.0, tag="part1")
    e.place("buy", "limit", 99.5, 1.0, tag="part2")
    e.place("sell", "stop", 99.0, 2.0, tag="stop")
    # tick at 98: through both limits AND at/below the stop
    fills = e.on_tick(98.0, 7)
    assert len(fills) == 3
    by_tag = {f.tag: f for f in fills}
    assert by_tag["part1"].price == 100.0     # limits at their own price
    assert by_tag["part2"].price == 99.5
    assert by_tag["stop"].price == 98.0       # stop at tick price
    assert e.open_orders == []


def test_order_fills_at_most_once():
    e = FillEngine()
    e.place("buy", "limit", 100.0, 1.0)
    assert len(e.on_tick(99.0, 1)) == 1
    assert e.on_tick(98.0, 2) == []           # already gone


def test_cancel_removes_resting_order():
    e = FillEngine()
    o = e.place("buy", "limit", 100.0, 1.0)
    assert e.cancel(o.id) is True
    assert e.on_tick(99.0, 1) == []


def test_cancel_of_filled_order_returns_false():
    e = FillEngine()
    o = e.place("buy", "limit", 100.0, 1.0)
    e.on_tick(99.0, 1)
    assert e.cancel(o.id) is False            # caller learns it was too late


def test_cancel_all():
    e = FillEngine()
    e.place("buy", "limit", 100.0, 1.0)
    e.place("sell", "limit", 110.0, 1.0)
    assert e.cancel_all() == 2
    assert e.open_orders == []


def test_order_ids_are_unique_and_increasing():
    e = FillEngine()
    o1 = e.place("buy", "limit", 100.0, 1.0)
    o2 = e.place("buy", "limit", 99.0, 1.0)
    assert o2.id > o1.id


def test_validation():
    e = FillEngine()
    with pytest.raises(ValueError):
        e.place("hold", "limit", 100.0, 1.0)
    with pytest.raises(ValueError):
        e.place("buy", "market", 100.0, 1.0)
    with pytest.raises(ValueError):
        e.place("buy", "limit", 100.0, 0.0)
    with pytest.raises(ValueError):
        e.place("buy", "limit", -5.0, 1.0)
