from src.rangebars.builder import build_from_ticks

def test_basic_close():
    bars = build_from_ticks([(100.0, 1), (100.5, 2), (101.0, 3)], range_size=1.0)
    assert len(bars) >= 1
    assert abs((bars[0].high - bars[0].low) - 1.0) < 1e-9

def test_no_premature_close():
    bars = build_from_ticks([(100.0, 1), (100.4, 2), (100.2, 3)], range_size=1.0)
    assert bars == []
