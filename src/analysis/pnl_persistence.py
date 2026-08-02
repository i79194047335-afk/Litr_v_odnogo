"""Does a profitable account stay profitable? Rank stability across days.

H-003 claim B asks whether reconstructed PnL selects anything that repeats. It
does not answer "is the arithmetic right" — `pnl_probe` does that — but
"granted the arithmetic, does yesterday's ranking predict today's".

Written because the first attempt at claim B was produced by scratch scripts.
The falsifier's round-1 verdict on it was UNTESTABLE, and correctly: nine
numbers had no runnable source, so nothing could be recomputed. That is the
project's own ghost rule (CLAUDE.md §2) applied to my own output. This module
exists so every figure in the claim comes from a committed tool with tests.

Three measures, weakest to strongest, because they fail differently:

  overlap    — how many of day A's top-N appear in day B's top-N. Intuitive,
               but sensitive to who is *eligible*: `min_fills` is applied per
               day, so an account can leave the top by trading less rather
               than by earning less. Reported both ways, and the gap between
               them is itself the diagnostic.

  sign       — did an account that made money yesterday make money today?
               Immune to the eligibility artifact when restricted to accounts
               present both days, and immune to outliers. A coin flip is 50%.

  spearman   — rank correlation over the whole common population, not just
               its head. Catches persistence that a top-N cut would miss.

Each is reported against a permutation baseline: the same statistic computed
after shuffling one day's PnL across the same accounts, which destroys any
real association while preserving both distributions. The baseline is what
makes a number like "8 of 20" mean something.

Usage:
    python -m src.analysis.pnl_persistence --market 24 --days 20260730 20260731
    python -m src.analysis.pnl_persistence --market 0 --days 20260729 20260730 20260731
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from src.analysis.participant_pnl import accumulate_pnl
from src.analysis.participant_stats import read_tape

TAPE_DIR = Path("data/participants")
DRAWS = 2000
SEED = 7


@dataclass
class Stability:
    """One day-pair, three measures, each beside its permutation baseline."""

    day_a: str
    day_b: str
    eligible_a: int
    eligible_b: int
    common: int
    overlap_raw: int
    overlap_common: int
    overlap_null_mean: float
    overlap_null_p95: int
    sign_agree: int
    sign_null_mean: float
    spearman: float
    spearman_null_p95: float
    top_n: int

    @property
    def sign_rate(self) -> float:
        return self.sign_agree / self.common if self.common else 0.0


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation. Ties get averaged ranks, as the definition requires."""
    if len(xs) < 2:
        return float("nan")

    def ranks(vs: list[float]) -> list[float]:
        order = sorted(range(len(vs)), key=lambda i: vs[i])
        out = [0.0] * len(vs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            shared = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")


def day_pnl(market: int, day: str, min_fills: int) -> dict[int, float]:
    path = TAPE_DIR / f"trades_full_{market}_{day}.jsonl.gz"
    if not path.exists():
        sys.exit(f"no tape at {path}")
    rows = accumulate_pnl(read_tape([path]))
    return {r.account_id: r.realised for r in rows.values() if r.fills >= min_fills}


def compare(a: dict[int, float], b: dict[int, float], day_a: str, day_b: str,
            top_n: int = 20, draws: int = DRAWS, seed: int = SEED) -> Stability:
    """Compare two days' PnL maps. Pure — no file access, so it is testable."""
    rng = random.Random(seed)
    common = sorted(set(a) & set(b))

    def top(d: dict[int, float], pool=None) -> list[int]:
        keys = sorted(pool if pool is not None else d, key=lambda k: d[k], reverse=True)
        return keys[:top_n]

    overlap_raw = len(set(top(a)) & set(top(b)))
    overlap_common = len(set(top(a, common)) & set(top(b, common)))

    # Null for overlap: two independent draws of top_n from the common pool.
    null_overlaps = []
    if len(common) >= 2:
        size = min(top_n, len(common))
        for _ in range(draws):
            sa = set(rng.sample(common, size))
            sb = set(rng.sample(common, size))
            null_overlaps.append(len(sa & sb))
    null_overlaps.sort()

    sign_agree = sum(1 for k in common if (a[k] > 0) == (b[k] > 0))

    # Null for sign: shuffle day B's values across the same accounts. This
    # preserves both days' distributions and destroys only the pairing.
    vals_b = [b[k] for k in common]
    null_signs = []
    for _ in range(draws if common else 0):
        rng.shuffle(vals_b)
        null_signs.append(sum(1 for k, v in zip(common, vals_b) if (a[k] > 0) == (v > 0)))

    xs = [a[k] for k in common]
    ys = [b[k] for k in common]
    rho = spearman(xs, ys)

    null_rhos = []
    shuffled = list(ys)
    for _ in range(draws if len(common) >= 2 else 0):
        rng.shuffle(shuffled)
        null_rhos.append(spearman(xs, shuffled))
    null_rhos.sort()

    def p95(v: list[float], default: float) -> float:
        return v[int(0.95 * len(v))] if v else default

    return Stability(
        day_a=day_a, day_b=day_b,
        eligible_a=len(a), eligible_b=len(b), common=len(common),
        overlap_raw=overlap_raw, overlap_common=overlap_common,
        overlap_null_mean=statistics.mean(null_overlaps) if null_overlaps else 0.0,
        overlap_null_p95=int(p95(null_overlaps, 0)),
        sign_agree=sign_agree,
        sign_null_mean=statistics.mean(null_signs) if null_signs else 0.0,
        spearman=rho,
        spearman_null_p95=p95(null_rhos, float("nan")),
        top_n=top_n,
    )


def report(s: Stability) -> None:
    print(f"{s.day_a} -> {s.day_b}")
    print(f"  eligible: {s.eligible_a} / {s.eligible_b}, present both days: {s.common}")
    print(f"  top-{s.top_n} overlap, as-ranked:     {s.overlap_raw}/{s.top_n}")
    print(f"  top-{s.top_n} overlap, common pool:   {s.overlap_common}/{s.top_n}"
          f"   (null mean {s.overlap_null_mean:.2f}, p95 {s.overlap_null_p95})")
    print(f"  sign of PnL repeats:            {s.sign_agree}/{s.common} "
          f"= {100*s.sign_rate:.1f}%   (null mean "
          f"{100*s.sign_null_mean/s.common if s.common else 0:.1f}%)")
    print(f"  spearman rank correlation:      {s.spearman:+.4f}"
          f"   (null p95 {s.spearman_null_p95:+.4f})")
    verdict = ("above noise" if s.spearman > s.spearman_null_p95 else "within noise")
    print(f"  -> rank persistence: {verdict}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--days", nargs="+", required=True, help="two or more YYYYMMDD")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min-fills", type=int, default=20)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    if len(args.days) < 2:
        ap.error("need at least two days")

    pnl = {d: day_pnl(args.market, d, args.min_fills) for d in args.days}
    print(f"market {args.market}, min_fills={args.min_fills}, "
          f"top={args.top}, draws={args.draws}, seed={args.seed}\n")
    for x, y in zip(args.days, args.days[1:]):
        report(compare(pnl[x], pnl[y], x, y, args.top, args.draws, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
