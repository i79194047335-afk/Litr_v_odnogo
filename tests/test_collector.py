"""Unit tests for lighter_ticks collector logic.

Focus: parsing correctness on edge cases (fixes DeepSeek audit points 2 & 5),
and dedup semantics on the trade_id window.
"""
from src.collector.lighter_ticks import _normalize_trade, Deduper, _get_or_none


# ---------------------------------------------------------------------------
# _normalize_trade
# ---------------------------------------------------------------------------

def test_normalize_happy_path():
    raw = {
        "trade_id": 24404885931,
        "price": "60746.8",
        "size": "0.00021",
        "timestamp": 1782864000213,
        "is_maker_ask": True,
    }
    rec = _normalize_trade(market_id=1, raw=raw)
    assert rec == {
        "m": 1, "p": 60746.8, "s": 0.00021,
        "t": 1782864000213, "side": "buy", "tid": 24404885931,
    }


def test_normalize_side_from_is_maker_ask():
    """is_maker_ask=True → taker bought; False → taker sold."""
    common = {"trade_id": 1, "price": "1", "size": "1", "timestamp": 1}
    assert _normalize_trade(0, {**common, "is_maker_ask": True})["side"] == "buy"
    assert _normalize_trade(0, {**common, "is_maker_ask": False})["side"] == "sell"


def test_normalize_missing_is_maker_ask_returns_none():
    """Previous code silently defaulted to 'sell' — that skewed the side stats."""
    raw = {"trade_id": 1, "price": "1", "size": "1", "timestamp": 1}
    assert _normalize_trade(0, raw) is None


def test_normalize_is_maker_ask_not_bool_returns_none():
    raw = {"trade_id": 1, "price": "1", "size": "1", "timestamp": 1,
           "is_maker_ask": "true"}
    assert _normalize_trade(0, raw) is None


def test_normalize_zero_price_does_not_fallthrough():
    """DeepSeek audit #2: raw.get('price') or raw['px'] breaks when price is 0.

    A price of 0 is unrealistic but the parser must not KeyError on it —
    it should either accept 0.0 or warn+return None, never crash.
    """
    raw = {"trade_id": 1, "price": 0, "size": "1", "timestamp": 1,
           "is_maker_ask": True}
    # no 'px' key present; old code would KeyError, new code must not
    rec = _normalize_trade(0, raw)
    assert rec is not None
    assert rec["p"] == 0.0


def test_normalize_missing_trade_id_returns_none():
    raw = {"price": "1", "size": "1", "timestamp": 1, "is_maker_ask": True}
    assert _normalize_trade(0, raw) is None


def test_normalize_alt_field_names_accepted():
    """Field-name variants seen in different SDK versions/docs."""
    raw = {"tid": 42, "px": "1.5", "sz": "0.1", "time": 999, "is_maker_ask": False}
    rec = _normalize_trade(2, raw)
    assert rec == {"m": 2, "p": 1.5, "s": 0.1, "t": 999, "side": "sell", "tid": 42}


# ---------------------------------------------------------------------------
# _get_or_none
# ---------------------------------------------------------------------------

def test_get_or_none_zero_is_returned():
    """The whole point of _get_or_none over `a or b` — 0 is a real value."""
    assert _get_or_none({"price": 0}, "price", "px") == 0


def test_get_or_none_skips_explicit_none():
    assert _get_or_none({"price": None, "px": 5}, "price", "px") == 5


def test_get_or_none_all_missing():
    assert _get_or_none({}, "price", "px") is None


# ---------------------------------------------------------------------------
# Deduper
# ---------------------------------------------------------------------------

def test_deduper_first_seen_is_new():
    d = Deduper(max_size=10)
    assert d.is_new(100) is True


def test_deduper_repeat_is_not_new():
    d = Deduper(max_size=10)
    d.is_new(100)
    assert d.is_new(100) is False


def test_deduper_evicts_oldest_beyond_window():
    """Strict FIFO: every insertion beyond max_size evicts the oldest."""
    d = Deduper(max_size=3)
    for tid in [1, 2, 3]:
        assert d.is_new(tid) is True
    # window: [1,2,3]. Inserting 4 evicts 1.
    assert d.is_new(4) is True
    # window: [2,3,4]. tid=1 no longer remembered — treated as new.
    assert d.is_new(1) is True
    # After inserting 1, window is [3,4,1] — 2 is now evicted too.
    assert d.is_new(2) is True
    # But 3 was still in window when we last checked.
    # Actually 3 got evicted when we inserted 2 above.
    # Safer to test what's clearly IN the window:
    d2 = Deduper(max_size=3)
    for tid in [10, 20, 30]:
        d2.is_new(tid)
    assert d2.is_new(20) is False  # still in window
    assert d2.is_new(30) is False  # still in window


def test_deduper_realistic_burst_pattern():
    """Simulates observed pattern: same tid repeats within a few frames."""
    d = Deduper(max_size=100)
    frames = [
        [1001, 1002, 1003],       # 3 new
        [1002, 1004],              # 1 dup (1002), 1 new (1004)
        [1005, 1005, 1005],        # in-batch triple repeat
        [1006],
    ]
    kept: list[int] = []
    for frame in frames:
        for tid in frame:
            if d.is_new(tid):
                kept.append(tid)
    assert kept == [1001, 1002, 1003, 1004, 1005, 1006]