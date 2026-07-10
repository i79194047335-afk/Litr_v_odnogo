"""
Duplicate diagnostic for tick JSONL — and the reason the legacy retro-dedup
was CANCELLED rather than executed (2026-07-10).

THE TRAP THIS TOOL EXISTS TO DOCUMENT
--------------------------------------
Legacy v1 files have no `tid`, so the obvious dedup key is
`(t, p, s, side)`. Applying it to v1 BTC/ETH files reports 16-22% "duplicate"
rows, and that number was recorded in CONTEXT.md as a measured duplicate rate
for two sessions.

It is not a duplicate rate. It is a COLLISION rate, and a same-millisecond
collision is exactly what a real market event looks like: one aggressive order
sweeping several resting orders prints several fills sharing the same
timestamp, price, size and taker side. The key cannot tell those apart from a
re-delivered trade.

Proof, run on this repo's own data:

  1. Apply the key to CLEAN v2 files (files whose `tid` set proves 0.00%
     duplicates). They report 9.4-17.1% "duplicates" anyway. Every one of
     those is a FALSE collapse. ETH 2026-07-03 (v2, clean) collides at
     16.82%; ETH 2026-06-29 (v1) collides at 21.03%. The v1 number sits
     inside the natural range of the artifact.

  2. Look for the signature the duplication story predicts. A re-delivered
     WS batch repeats a VARIED sequence of trades (A,B,C ... A,B,C). Clean
     v2 shows such 3-row repeats at 0.197% of rows; dirty v1 at 0.230% —
     no excess. An immediately-repeated single trade would make identical-row
     run lengths turn even (A,A). Odd run lengths dominate both files.

  3. The live collector counts duplicates by `tid`, the unambiguous key.
     Its own logs report `dropped_dup=0` on every market, continuously.
     The WS does not re-deliver trades on `update/trade` at all.

Conclusion: the v1 files are not meaningfully duplicated, `(t,p,s,side)`
dedup would have DELETED roughly 10-17% of genuine BTC/ETH trades, and the
"0.0% after the fix" comparison in CONTEXT.md was measuring `tid` collisions
against `(t,p,s,side)` collisions — two different quantities.

WHAT THIS MEANS FOR A v2 FILE
-----------------------------
`tid` is unambiguous. `tid_duplicates` is a real duplicate count. The
`key_collisions` number on the same file is a direct measurement of the
artifact, and `false_collapses = key_collisions - tid_duplicates` is how many
real trades a key-based dedup would have destroyed.

Usage:
    python -m src.collector.dupe_diagnostic data/ticks/trades_1_20260701.jsonl
    python -m src.collector.dupe_diagnostic data/ticks/trades_*_2026070*.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


# --- pure computation (unit-tested against hand-derived values) ---------------

def legacy_key(rec: dict) -> tuple:
    """The dedup key a v1 file forces on us: everything except identity."""
    return (rec["t"], rec["p"], rec["s"], rec["side"])


@dataclass(frozen=True)
class DupeReport:
    n_rows: int
    schema: str                  # "v1" | "v2" | "mixed"
    n_with_tid: int
    tid_duplicates: int | None   # None when no row carries a tid
    key_collisions: int
    false_collapses: int | None  # key_collisions - tid_duplicates, when known

    @property
    def key_collision_pct(self) -> float:
        return 100.0 * self.key_collisions / self.n_rows if self.n_rows else 0.0

    @property
    def false_collapse_pct(self) -> float | None:
        if self.false_collapses is None or not self.n_rows:
            return None
        return 100.0 * self.false_collapses / self.n_rows


def analyze(records: Iterable[dict]) -> DupeReport:
    """Count real duplicates (by tid, when present) and key collisions.

    `false_collapses` is the honest headline for a v2 file: genuine, distinct
    trades that a `(t,p,s,side)` dedup would have merged and destroyed.
    """
    n_rows = 0
    n_with_tid = 0
    tids: list[int] = []
    keys: list[tuple] = []

    for rec in records:
        n_rows += 1
        keys.append(legacy_key(rec))
        if "tid" in rec:
            n_with_tid += 1
            tids.append(rec["tid"])

    if n_rows == 0:
        return DupeReport(0, "v1", 0, None, 0, None)

    if n_with_tid == 0:
        schema = "v1"
    elif n_with_tid == n_rows:
        schema = "v2"
    else:
        schema = "mixed"

    key_collisions = _count_duplicates(keys)

    if n_with_tid == n_rows:
        tid_duplicates = _count_duplicates(tids)
        false_collapses = key_collisions - tid_duplicates
    else:
        # A mixed or v1 file cannot separate the two — saying "unknown" is the
        # whole point of this module.
        tid_duplicates = None
        false_collapses = None

    return DupeReport(n_rows, schema, n_with_tid, tid_duplicates,
                      key_collisions, false_collapses)


def _count_duplicates(values: list) -> int:
    """Rows that are a repeat of an earlier row: sum(count - 1) over repeats."""
    counts = Counter(values)
    return sum(c - 1 for c in counts.values() if c > 1)


# --- IO -----------------------------------------------------------------------

def iter_records(path: Path) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Report real duplicates vs key-collision artifact in tick JSONL")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    print(f"{'file':34} {'schema':7} {'rows':>10} {'tid dupes':>10} "
          f"{'key coll.':>10} {'FALSE':>10}")
    print("-" * 86)

    for spec in args.files:
        path = Path(spec)
        if not path.exists():
            print(f"{path.name:34} MISSING", file=sys.stderr)
            continue
        rep = analyze(iter_records(path))
        tid = "n/a" if rep.tid_duplicates is None else f"{rep.tid_duplicates:,}"
        if rep.false_collapses is None:
            false = "unknowable"
        else:
            false = f"{rep.false_collapses:,} ({rep.false_collapse_pct:.2f}%)"
        print(f"{path.name:34} {rep.schema:7} {rep.n_rows:>10,} {tid:>10} "
              f"{rep.key_collisions:>10,} {false:>10}")

    print()
    print("key coll. = rows sharing (t,p,s,side) with an earlier row.")
    print("On a v2 file every key collision beyond `tid dupes` is a FALSE")
    print("collapse: a genuine, distinct trade a legacy dedup would delete.")
    print("On a v1 file the two are indistinguishable — which is why the")
    print("legacy retro-dedup was cancelled. See this module's docstring.")


if __name__ == "__main__":
    main()
