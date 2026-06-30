"""
Cross-check two JSONL trade sources for the SAME market and UTC day:
our live WebSocket collector vs the 0xArchive historical backfill.

Both files use the schema:
    {"m": int, "p": float, "s": float, "t": ms, "side": "buy"|"sell"}

Goal — answer three questions, in order of importance:
  1. Is the 0xArchive A/B → buy/sell mapping correct?
     (the backfill assumes A=buy, B=sell — this verifies it)
  2. Do the two sources see a comparable stream of trades, or is one
     dropping a meaningful fraction?
  3. When the same trade appears in both, do price/size agree?

The script makes NO network calls. It only reads two files you point it at.
It prints numbers and a plain-language read at the end. It does NOT modify
any files or auto-fix anything; if the mapping turns out flipped, it tells
you the one-line change to make.

Usage:
    python -m src.collector.compare_sources \
        --live data/ticks/trades_1_20260701.jsonl \
        --backfill data/ticks_backfill/trades_1_20260701.jsonl

Optional:
    --time-bucket-ms N   Round timestamps into N-ms buckets before matching
                         trades (default 0 = exact ms). Use if the two sources
                         round milliseconds differently and exact matching
                         shows suspiciously low overlap. Try 10, then 100.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _load(path: Path) -> list[dict]:
    """Read a JSONL trade file into a list of dicts. Skips blank/bad lines."""
    rows: list[dict] = []
    bad = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"  warning: {bad} unparsable line(s) in {path.name}")
    return rows


def _fmt_ts(ms: int) -> str:
    """ms epoch → HH:MM:SS.mmm UTC (time only, for compact display)."""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return dt.strftime("%H:%M:%S.") + f"{ms % 1000:03d}"


def _side_counts(rows: list[dict]) -> tuple[int, int]:
    """Return (n_buy, n_sell)."""
    c = Counter(r["side"] for r in rows)
    return c.get("buy", 0), c.get("sell", 0)


def _key(r: dict, bucket_ms: int):
    """Identity key for one trade: (timestamp, price, size).

    Side is deliberately EXCLUDED so we can test the mapping separately —
    if a trade matches on (t, p, s) but disagrees on side, that tells us
    something about the side convention rather than hiding it.
    """
    t = r["t"]
    if bucket_ms > 0:
        t = t // bucket_ms
    # round price/size to avoid float noise differences
    return (t, round(r["p"], 6), round(r["s"], 8))


def _section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> None:
    ap = argparse.ArgumentParser(description="Cross-check live vs backfill JSONL")
    ap.add_argument("--live", required=True, type=Path)
    ap.add_argument("--backfill", required=True, type=Path)
    ap.add_argument("--time-bucket-ms", type=int, default=0)
    args = ap.parse_args()

    for p in (args.live, args.backfill):
        if not p.exists():
            print(f"ERROR: file not found: {p}")
            sys.exit(1)

    print(f"LIVE     file: {args.live}")
    print(f"BACKFILL file: {args.backfill}")
    print(f"time bucket: {args.time_bucket_ms} ms"
          + (" (exact match)" if args.time_bucket_ms == 0 else ""))

    live = _load(args.live)
    back = _load(args.backfill)

    # ---- Metric 1: volumes -------------------------------------------------
    _section("1. Volume")
    lb, ls = _side_counts(live)
    bb, bs = _side_counts(back)
    n_live, n_back = len(live), len(back)
    sum_live = sum(r["s"] for r in live)
    sum_back = sum(r["s"] for r in back)

    print(f"  LIVE     {n_live:>10,} trades   sum_size={sum_live:,.4f}")
    print(f"  BACKFILL {n_back:>10,} trades   sum_size={sum_back:,.4f}")
    if n_live and n_back:
        cnt_diff = n_back - n_live
        cnt_pct = 100.0 * cnt_diff / n_live
        sz_diff = sum_back - sum_live
        sz_pct = 100.0 * sz_diff / sum_live if sum_live else 0.0
        print(f"  count diff (back−live): {cnt_diff:+,} ({cnt_pct:+.2f}%)")
        print(f"  size  diff (back−live): {sz_diff:+,.4f} ({sz_pct:+.2f}%)")

    # time spans
    if live:
        print(f"  LIVE     span: {_fmt_ts(min(r['t'] for r in live))}"
              f" → {_fmt_ts(max(r['t'] for r in live))}")
    if back:
        print(f"  BACKFILL span: {_fmt_ts(min(r['t'] for r in back))}"
              f" → {_fmt_ts(max(r['t'] for r in back))}")

    # ---- Metric 2: side distribution ---------------------------------------
    _section("2. Side distribution")
    def _pct(a, b):
        tot = a + b
        return (100.0 * a / tot, 100.0 * b / tot) if tot else (0.0, 0.0)
    lbp, lsp = _pct(lb, ls)
    bbp, bsp = _pct(bb, bs)
    print(f"  LIVE     buy={lb:>10,} ({lbp:5.1f}%)   sell={ls:>10,} ({lsp:5.1f}%)")
    print(f"  BACKFILL buy={bb:>10,} ({bbp:5.1f}%)   sell={bs:>10,} ({bsp:5.1f}%)")
    print(f"  buy-share delta (back−live): {bbp - lbp:+.1f} pp")

    # ---- Metric 3: trade-level overlap, BOTH mappings ----------------------
    _section("3. Trade overlap & mapping test")
    bucket = args.time_bucket_ms

    # index by identity key (t, p, s) → list of sides for that key
    def index(rows):
        d: dict = {}
        for r in rows:
            d.setdefault(_key(r, bucket), []).append(r["side"])
        return d

    li = index(live)
    bi = index(back)
    live_keys = set(li)
    back_keys = set(bi)

    matched = live_keys & back_keys
    live_only = live_keys - back_keys
    back_only = back_keys - live_keys

    n_match = len(matched)
    print(f"  identity = (timestamp, price, size)"
          + (f", bucketed to {bucket}ms" if bucket else ""))
    print(f"  matched keys : {n_match:>10,}"
          + (f"  ({100.0*n_match/len(live_keys):.2f}% of live)" if live_keys else ""))
    print(f"  live-only    : {len(live_only):>10,}"
          + (f"  ({100.0*len(live_only)/len(live_keys):.2f}% of live)" if live_keys else ""))
    print(f"  backfill-only: {len(back_only):>10,}"
          + (f"  ({100.0*len(back_only)/len(back_keys):.2f}% of backfill)" if back_keys else ""))

    # Among matched keys, do the SIDES agree?
    # current mapping: backfill already applied A→buy / B→sell.
    # We test: for matched trades, how often does live.side == backfill.side?
    # If it agrees almost always → mapping correct.
    # If it disagrees almost always → mapping flipped.
    agree = 0
    disagree = 0
    ambiguous = 0  # same (t,p,s) had mixed sides — can't tell
    for k in matched:
        lsides = set(li[k])
        bsides = set(bi[k])
        if len(lsides) == 1 and len(bsides) == 1:
            if lsides == bsides:
                agree += 1
            else:
                disagree += 1
        else:
            ambiguous += 1

    decisive = agree + disagree
    print()
    print(f"  side agreement on matched trades (excludes {ambiguous:,} ambiguous):")
    if decisive:
        print(f"    agree (live.side == backfill.side): {agree:>10,} ({100.0*agree/decisive:.2f}%)")
        print(f"    disagree                          : {disagree:>10,} ({100.0*disagree/decisive:.2f}%)")
    else:
        print("    no decisive matched trades to compare")

    # ---- Plain-language read -----------------------------------------------
    _section("READ (plain language)")

    # mapping verdict
    if decisive:
        agree_pct = 100.0 * agree / decisive
        if agree_pct >= 95:
            print("  MAPPING: ✓ looks CORRECT.")
            print("    On matched trades the sides agree almost always, so the")
            print("    backfill's A→buy / B→sell assumption holds. No change needed.")
        elif agree_pct <= 5:
            print("  MAPPING: ✗ looks FLIPPED.")
            print("    On matched trades the sides DISAGREE almost always. The")
            print("    A/B convention is the opposite of what we assumed.")
            print("    FIX: in src/collector/oxarchive_backfill.py, function")
            print('    _side_to_str, swap the returns so "A" → "sell" and "B" → "buy",')
            print("    then re-run the backfill for the affected days.")
        else:
            print(f"  MAPPING: ? INCONCLUSIVE ({agree_pct:.1f}% agree).")
            print("    Neither clearly correct nor clearly flipped. This usually")
            print("    means the (t,p,s) matching is catching different trades by")
            print("    coincidence. Try re-running with --time-bucket-ms 10, then")
            print("    100, and see if agreement sharpens toward 0% or 100%.")
    else:
        print("  MAPPING: could not test — no decisive matched trades.")
        print("    Likely the two files don't actually overlap in time, or")
        print("    timestamps are rounded differently. Check the spans in")
        print("    section 1, and try --time-bucket-ms 10.")

    # overlap verdict
    print()
    if live_keys and back_keys:
        match_pct = 100.0 * n_match / max(len(live_keys), len(back_keys))
        if match_pct >= 98:
            print("  OVERLAP: ✓ excellent. The two sources see essentially the")
            print("    same trades. Both the live collector and the backfill look")
            print("    trustworthy on this day.")
        elif match_pct >= 90:
            print(f"  OVERLAP: ~ good ({match_pct:.1f}%). Minor differences, likely")
            print("    reconnect gaps or boundary milliseconds. Probably fine, but")
            print("    glance at the live-only / backfill-only counts above.")
        else:
            print(f"  OVERLAP: ⚠ low ({match_pct:.1f}%). The sources disagree on a lot")
            print("    of trades. Before trusting either, try --time-bucket-ms 10/100")
            print("    (rounding differences inflate the gap). If it stays low, one")
            print("    source is dropping data — investigate before backtesting.")

    print()


if __name__ == "__main__":
    main()
