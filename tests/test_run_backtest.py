"""Tests for the runner's pure helper functions (the CLI wiring itself is
smoke-tested manually against synthetic data, not unit-testable in the
usual sense — this covers what IS pure)."""
import math
from pathlib import Path

from src.backtest.run_backtest import load_ticks, _fmt


def test_load_ticks_chains_files_in_given_order(tmp_path):
    f1 = tmp_path / "a.jsonl"
    f2 = tmp_path / "b.jsonl"
    f1.write_text('{"p": 100.0, "t": 0}\n{"p": 100.5, "t": 1000}\n')
    f2.write_text('{"p": 101.0, "t": 2000}\n')
    out = list(load_ticks([f1, f2]))
    assert out == [(100.0, 0), (100.5, 1000), (101.0, 2000)]


def test_load_ticks_respects_order_of_paths_given(tmp_path):
    # deliberately reversed vs chronological — load_ticks does NOT re-sort,
    # by design (streaming, no global sort across multi-GB inputs).
    f1 = tmp_path / "a.jsonl"
    f2 = tmp_path / "b.jsonl"
    f1.write_text('{"p": 100.0, "t": 0}\n')
    f2.write_text('{"p": 101.0, "t": 2000}\n')
    out = list(load_ticks([f2, f1]))
    assert out == [(101.0, 2000), (100.0, 0)]


def test_fmt_none_is_na():
    assert _fmt(None) == "n/a"


def test_fmt_infinite():
    assert _fmt(float("inf")) == "inf (no losing trades)"


def test_fmt_plain_number():
    assert _fmt(0.30) == "0.3000"


def test_fmt_percent():
    assert _fmt(0.4, pct=True) == "40.00%"
