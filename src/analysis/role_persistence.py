"""Does an account keep its maker/taker role from one day to the next?

The per-day report answers what an account did on a day. That is not the same
question as whether role is a property of the participant, and the difference
is exactly what the H-002 falsifier attacked: a one-day snapshot showing pure
makers exist does not show the same accounts are pure makers tomorrow.

This runs the falsifier's kill-shot as a script so the answer lives in a file
rather than in a shell one-liner: track every account present on two closed
days with enough activity on both, and look for role flips.

Usage:
    python -m src.analysis.role_persistence --market 1 --days 20260730 20260731
    python -m src.analysis.role_persistence --market 1 --days 20260729 20260731 --min-fills 50
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from src.analysis.participant_stats import (
    AccountStats,
    accumulate,
    load_account_types,
    read_tape,
)

TAPE_DIR = Path("data/participants")
PURE_MAKER = 0.95
PURE_TAKER = 0.05


def day_stats(market: int, day: str, min_fills: int) -> dict[int, AccountStats]:
    paths = sorted(TAPE_DIR.glob(f"trades_full_{market}_{day}.jsonl.gz"))
    if not paths:
        sys.exit(f"no tape for market {market} day {day}")
    stats = accumulate(read_tape(paths))
    return {k: v for k, v in stats.items() if v.fills >= min_fills}


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den if den else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--days", nargs=2, required=True, metavar=("DAY_A", "DAY_B"))
    ap.add_argument("--min-fills", type=int, default=100,
                    help="role-participations required on BOTH days (default 100)")
    args = ap.parse_args()

    day_a, day_b = args.days
    a = day_stats(args.market, day_a, args.min_fills)
    b = day_stats(args.market, day_b, args.min_fills)
    both = sorted(set(a) & set(b))

    print(f"market {args.market}: {day_a} -> {day_b}, min {args.min_fills} fills on both days")
    print(f"accounts on day A: {len(a):,}   day B: {len(b):,}   on both: {len(both):,}")
    if not both:
        sys.exit("no accounts qualify on both days")

    flips = [
        i for i in both
        if (a[i].maker_share >= PURE_MAKER and b[i].maker_share <= PURE_TAKER)
        or (a[i].maker_share <= PURE_TAKER and b[i].maker_share >= PURE_MAKER)
    ]
    print(f"\nrole flips (>={PURE_MAKER} <-> <={PURE_TAKER}): {len(flips)}")
    for i in flips[:10]:
        print(f"  {i}: {a[i].maker_share:.3f} -> {b[i].maker_share:.3f}")

    moves = [(abs(a[i].maker_share - b[i].maker_share), i) for i in both]
    for cut in (0.1, 0.2, 0.4):
        n = sum(1 for d, _ in moves if d > cut)
        print(f"maker-share moved more than {cut:.1f}: {n:>4} of {len(both)} ({100 * n / len(both):.1f}%)")

    xs = [a[i].maker_share for i in both]
    ys = [b[i].maker_share for i in both]
    print(f"correlation of maker share across the two days: {pearson(xs, ys):.3f}")

    # Threshold sensitivity: the falsifier's second point was that the fill cutoff
    # could manufacture the bimodality by hiding the switchers.
    print("\nrole classes on day B, by activity cutoff")
    full = accumulate(read_tape(sorted(TAPE_DIR.glob(f"trades_full_{args.market}_{day_b}.jsonl.gz"))))
    two_sided = sum(s.notional for s in full.values())
    print(f"  {'cutoff':>7} {'accts':>7} {'pure mk':>9} {'pure tk':>9} {'middle':>9}  (% of two-sided notional)")
    for cut in (1, 10, 50, 100, 500):
        act = [s for s in full.values() if s.fills >= cut]
        if not act:
            continue
        pm = sum(s.notional for s in act if s.maker_share >= PURE_MAKER) / two_sided
        pt = sum(s.notional for s in act if s.maker_share <= PURE_TAKER) / two_sided
        md = sum(s.notional for s in act if PURE_TAKER < s.maker_share < PURE_MAKER) / two_sided
        n_md = sum(1 for s in act if PURE_TAKER < s.maker_share < PURE_MAKER)
        print(f"  {cut:>7} {len(act):>7} {100 * pm:>8.1f}% {100 * pt:>8.1f}% "
              f"{100 * md:>8.1f}% ({n_md} accts)")

    types = load_account_types()
    if types:
        print("\nflips by account type:", {types.get(i) for i in flips} or "n/a")
    return 0


if __name__ == "__main__":
    sys.exit(main())
