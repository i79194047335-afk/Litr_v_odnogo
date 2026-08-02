"""Behavioural fingerprint of an account, and whether it survives across windows.

Ranking by realised PnL was rejected as a selection mechanism: the head of the
ranking does not repeat at any horizon tested (MINING.md — top-5 overlap 0/5,
1/5, 0/5 between two-day windows). That is not a defect in the PnL arithmetic,
which H-004 checked against the venue's own published returns. It says that
*how much an account made over a short window* is not a property of the
account.

H-002 found the opposite for one behavioural feature: maker share flipped on
zero of 742 accounts between days, correlation 0.985. So the question here is
whether that generalises — whether a set of behavioural features is stable
where outcome is not.

**Every feature is deliberately outcome-free.** Nothing here reads realised
PnL, entry quote, or anything derived from whether the account made money.
Including an outcome-shaped feature would smuggle the instability back in and
the comparison would answer nothing.

The features, and why each is a plausible fingerprint rather than noise:

  maker_share       — does the account rest or cross. Structural: it follows
                      from whether they run a quoting system or a signal.
  fills_per_hour    — activity rate, which follows from their infrastructure.
  size_median       — typical clip, which follows from capital and risk limits.
  size_cv           — do they always trade the same size (a bot with a fixed
                      clip) or vary it (discretionary, or a sizing model).
  markets_traded    — breadth. A single-market account is a different animal
                      from one quoting four.
  flip_rate         — how often a fill reverses their position sign, from the
                      venue's own `*_position_sign_changed`. Distinguishes
                      inventory-cycling market makers from directional holders.
  hour_concentration— share of fills in the busiest hour of the window. A
                      always-on bot spreads flat; a person or a scheduled
                      strategy concentrates.

Usage:
    python -m src.analysis.behaviour_profile --market 24 \\
        --window-a 20260729 20260730 --window-b 20260731 20260801
"""

from __future__ import annotations

import argparse
import bisect
import gc
import random
import statistics as st
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.analysis.participant_stats import read_tape, sides_of
from src.analysis.pnl_persistence import spearman

TAPE_DIR = Path("data/participants")
FEATURES = ("maker_share", "fills_per_hour", "size_median", "size_cv",
            "markets_traded", "flip_rate", "hour_concentration")


def perm_test(a: list[float], b: list[float], n_perm: int,
              rng: random.Random) -> tuple[float, float]:
    """Two-sided permutation test on the difference of medians.

    Assumption-free: it asks how often a random relabelling of the *same*
    numbers produces a gap this large. Lifted from `diag_take_vs_rest.py`,
    which carried the inference that closed a whole track while having no
    tests — MINING.md §5 required covering it before reuse.
    """
    obs = st.median(a) - st.median(b)
    pool = a + b
    na = len(a)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        d = st.median(pool[:na]) - st.median(pool[na:])
        if abs(d) >= abs(obs) - 1e-15:
            hits += 1
    # +1 on both sides: a p-value of exactly zero is not a thing a finite
    # permutation test can observe, and reporting one would overstate.
    return obs, (hits + 1) / (n_perm + 1)


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Effect size in [-1, 1]: P(a > b) - P(a < b).

    Interpretable regardless of n, unlike a p-value: with enough samples any
    difference is 'significant', but delta stays small when the distributions
    mostly overlap. |d| < 0.15 negligible, < 0.33 small, < 0.47 medium.
    """
    sb = sorted(b)
    n = len(sb)
    gt = lt = 0
    for x in a:
        gt += bisect.bisect_left(sb, x)
        lt += n - bisect.bisect_right(sb, x)
    tot = len(a) * n
    return (gt - lt) / tot if tot else 0.0


@dataclass
class Behaviour:
    """Outcome-free description of how one account traded over a window."""

    account_id: int
    fills: int = 0
    maker_fills: int = 0
    sizes: list[float] = field(default_factory=list)
    markets: set[int] = field(default_factory=set)
    flips: int = 0
    hour_counts: dict[int, int] = field(default_factory=dict)
    # None rather than 0.0: a timestamp of zero is a legitimate value, and
    # `x or default` would silently discard it. That exact bug was caught by
    # test_fills_per_hour_uses_the_observed_span.
    first_ts: float | None = None
    last_ts: float | None = None

    @property
    def maker_share(self) -> float:
        return self.maker_fills / self.fills if self.fills else 0.0

    @property
    def span_hours(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 1 / 60.0
        return max((self.last_ts - self.first_ts) / 3600.0, 1 / 60.0)

    @property
    def fills_per_hour(self) -> float:
        return self.fills / self.span_hours

    @property
    def size_median(self) -> float:
        return st.median(self.sizes) if self.sizes else 0.0

    @property
    def size_cv(self) -> float:
        """Coefficient of variation: spread relative to level, so it is
        comparable between an account trading 0.001 BTC and one trading 40."""
        if len(self.sizes) < 2:
            return 0.0
        mean = st.mean(self.sizes)
        return st.stdev(self.sizes) / mean if mean else 0.0

    @property
    def markets_traded(self) -> int:
        return len(self.markets)

    @property
    def flip_rate(self) -> float:
        return self.flips / self.fills if self.fills else 0.0

    @property
    def hour_concentration(self) -> float:
        """Share of fills landing in the account's single busiest hour."""
        if not self.hour_counts or not self.fills:
            return 0.0
        return max(self.hour_counts.values()) / self.fills

    def value(self, feature: str) -> float:
        return float(getattr(self, feature))


def accumulate_behaviour(records) -> dict[int, Behaviour]:
    """Roll fills into per-account behaviour. Both sides of a fill count."""
    out: dict[int, Behaviour] = {}
    for rec in records:
        try:
            sides = sides_of(rec)
            size = float(rec["size"])
            ts = float(rec.get("transaction_time") or 0) / 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if size <= 0:
            continue
        market = rec.get("market_id")
        hour = int(ts // 3600)

        for account, role in ((sides.maker, "maker"), (sides.taker, "taker")):
            entry = out.get(account)
            if entry is None:
                entry = out[account] = Behaviour(account_id=account,
                                                 first_ts=ts, last_ts=ts)
            entry.fills += 1
            entry.sizes.append(size)
            if role == "maker":
                entry.maker_fills += 1
            if market is not None:
                entry.markets.add(market)
            if rec.get(f"{role}_position_sign_changed"):
                entry.flips += 1
            entry.hour_counts[hour] = entry.hour_counts.get(hour, 0) + 1
            entry.first_ts = ts if entry.first_ts is None else min(entry.first_ts, ts)
            entry.last_ts = ts if entry.last_ts is None else max(entry.last_ts, ts)
    return out


def window_behaviour(markets: list[int], days: list[str],
                     min_fills: int) -> dict[int, Behaviour]:
    """Behaviour over a multi-day, multi-market window.

    Accounts are merged across markets on purpose: `markets_traded` is one of
    the features, and it cannot be measured one market at a time.
    """
    merged: dict[int, Behaviour] = {}
    for market in markets:
        for day in days:
            path = TAPE_DIR / f"trades_full_{market}_{day}.jsonl.gz"
            if not path.exists():
                continue
            for acct, b in accumulate_behaviour(read_tape([path])).items():
                cur = merged.get(acct)
                if cur is None:
                    merged[acct] = b
                    continue
                cur.fills += b.fills
                cur.maker_fills += b.maker_fills
                cur.sizes.extend(b.sizes)
                cur.markets |= b.markets
                cur.flips += b.flips
                for h, c in b.hour_counts.items():
                    cur.hour_counts[h] = cur.hour_counts.get(h, 0) + c
                if b.first_ts is not None:
                    cur.first_ts = (b.first_ts if cur.first_ts is None
                                    else min(cur.first_ts, b.first_ts))
                if b.last_ts is not None:
                    cur.last_ts = (b.last_ts if cur.last_ts is None
                                   else max(cur.last_ts, b.last_ts))
            gc.collect()
    return {a: b for a, b in merged.items() if b.fills >= min_fills}


def stability(a: dict[int, Behaviour], b: dict[int, Behaviour],
              feature: str) -> dict:
    """How well a feature measured in window A predicts the same in window B.

    Reported against a permutation null: the same correlation after shuffling
    window B across accounts, which destroys the pairing while preserving both
    distributions.
    """
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return {"feature": feature, "n": len(common), "rho": float("nan"),
                "null_p95": float("nan")}
    xs = [a[k].value(feature) for k in common]
    ys = [b[k].value(feature) for k in common]
    rho = spearman(xs, ys)

    rng = random.Random(7)
    shuffled = list(ys)
    nulls = []
    for _ in range(500):
        rng.shuffle(shuffled)
        nulls.append(spearman(xs, shuffled))
    nulls.sort()
    return {"feature": feature, "n": len(common), "rho": rho,
            "null_p95": nulls[int(0.95 * len(nulls))]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", type=int, nargs="+", default=[0, 1, 2, 24])
    ap.add_argument("--window-a", nargs="+", required=True, metavar="YYYYMMDD")
    ap.add_argument("--window-b", nargs="+", required=True, metavar="YYYYMMDD")
    ap.add_argument("--min-fills", type=int, default=20)
    args = ap.parse_args()

    a = window_behaviour(args.markets, args.window_a, args.min_fills)
    b = window_behaviour(args.markets, args.window_b, args.min_fills)
    common = set(a) & set(b)
    if len(common) < 3:
        sys.exit("fewer than 3 accounts present in both windows — nothing to compare")

    print(f"markets {args.markets}, min_fills={args.min_fills}")
    print(f"window A: {'+'.join(args.window_a)}  ({len(a)} accounts)")
    print(f"window B: {'+'.join(args.window_b)}  ({len(b)} accounts)")
    print(f"present in both: {len(common)}\n")
    print(f"{'feature':<22}{'rho':>9}{'null p95':>11}  verdict")
    for feature in FEATURES:
        s = stability(a, b, feature)
        verdict = "stable" if s["rho"] > s["null_p95"] else "within noise"
        print(f"{feature:<22}{s['rho']:>+9.4f}{s['null_p95']:>+11.4f}  {verdict}")
    print("\nAll features are outcome-free: none reads PnL or anything derived "
          "from it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
