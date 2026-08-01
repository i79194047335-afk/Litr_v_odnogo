"""Print what the participant tape says about who trades on Lighter.

Reads whole closed days only — the collector holds the current UTC day's file
open, and a partial day quietly deflates every per-day figure.

Usage:
    python -m src.analysis.report_participants --market 1 --day 20260731
    python -m src.analysis.report_participants --market 1     # all closed days
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.analysis.participant_stats import (
    accumulate,
    load_account_types,
    read_tape,
)

TAPE_DIR = Path("data/participants")
NAME_RE = re.compile(r"trades_full_(\d+)_(\d{8})\.jsonl\.gz$")

MARKET_NAMES = {0: "ETH", 1: "BTC", 2: "SOL", 24: "HYPE"}
TYPE_NAMES = {0: "standard", 1: "sub-account", None: "unknown"}


def closed_days(market: int | None) -> list[Path]:
    """Tape files for days that are over. Today's file is still being written."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = []
    for path in sorted(TAPE_DIR.glob("*.jsonl.gz")):
        m = NAME_RE.search(path.name)
        if not m:
            continue
        mkt, day = int(m.group(1)), m.group(2)
        if day >= today:
            continue
        if market is not None and mkt != market:
            continue
        out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, help="market id; omit for all")
    ap.add_argument("--day", help="YYYYMMDD; omit for every closed day")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    paths = closed_days(args.market)
    if args.day:
        paths = [p for p in paths if args.day in p.name]
    if not paths:
        sys.exit("no closed-day tape files match")

    print(f"files: {len(paths)}")
    for p in paths:
        print(f"  {p.name}")

    types = load_account_types()
    if not types:
        print("\nWARNING: data/account_index.json missing — account types unknown.")
        print("Run: python -m src.analysis.build_account_index --ids-from data/participants/")

    stats = accumulate(read_tape(paths))
    if not stats:
        sys.exit("no records read")

    two_sided = sum(s.notional for s in stats.values())
    one_sided = two_sided / 2
    fills = sum(s.fills for s in stats.values()) // 2
    liqs = sum(s.liquidations for s in stats.values()) // 2

    print(f"\n{'=' * 62}")
    print(f"fills            {fills:>14,}")
    print(f"turnover         ${one_sided:>13,.0f}   (one-sided)")
    print(f"average fill     ${one_sided / fills:>13,.0f}")
    print(f"participants     {len(stats):>14,}")
    print(f"liquidations     {liqs:>14,}   ({100 * liqs / fills:.3f}% of fills)")

    ranked = sorted(stats.values(), key=lambda s: s.notional, reverse=True)

    print(f"\nconcentration (share of two-sided notional)")
    for n in (10, 50, 200):
        share = sum(s.notional for s in ranked[:n]) / two_sided
        print(f"  top-{n:<4} {100 * share:5.1f}%")

    print(f"\nby account type")
    by_type: dict[int | None, list] = {}
    for s in ranked:
        by_type.setdefault(types.get(s.account_id), []).append(s)
    for t, group in sorted(by_type.items(), key=lambda kv: -sum(s.notional for s in kv[1])):
        vol = sum(s.notional for s in group)
        mk = sum(s.maker_fills for s in group)
        tk = sum(s.taker_fills for s in group)
        print(f"  {TYPE_NAMES.get(t, t):<12} {len(group):>5} accounts  "
              f"{100 * vol / two_sided:5.1f}% volume  "
              f"maker {100 * mk / (mk + tk) if mk + tk else 0:4.1f}% of its fills")

    print(f"\ntop {args.top} by notional")
    print(f"  {'account':>16} {'type':<12} {'notional':>14} {'share':>6} "
          f"{'fills':>8} {'maker%':>7} {'liq':>5}")
    for s in ranked[: args.top]:
        print(f"  {s.account_id:>16} {TYPE_NAMES.get(types.get(s.account_id), '?'):<12} "
              f"${s.notional:>13,.0f} {100 * s.notional / two_sided:5.2f}% "
              f"{s.fills:>8,} {100 * s.maker_share:6.1f}% {s.liquidations:>5}")

    pure_makers = [s for s in ranked if s.fills >= 100 and s.maker_share >= 0.95]
    pure_takers = [s for s in ranked if s.fills >= 100 and s.maker_share <= 0.05]
    print(f"\nrole purity (accounts with >=100 fills)")
    print(f"  >=95% maker  {len(pure_makers):>4}  "
          f"{100 * sum(s.notional for s in pure_makers) / two_sided:5.1f}% of volume")
    print(f"  <=5%  maker  {len(pure_takers):>4}  "
          f"{100 * sum(s.notional for s in pure_takers) / two_sided:5.1f}% of volume")

    return 0


if __name__ == "__main__":
    sys.exit(main())
