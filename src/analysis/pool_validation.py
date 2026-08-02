"""Check reconstructed PnL against Lighter's published pool returns.

Until this existed, realised PnL had no external referent. The WS feed carries
no PnL field, and REST PnL for a foreign account returns 400, so every check
was internal — two branches of our own arithmetic, which can be wrong together.

Public Pools is the venue's copy-trading product: an operator trades, others
deposit, the operator takes a cut of the profit. The showcase publishes each
operator's `account_index` beside an annual return the venue computed itself.
That is a number this code did not produce, about accounts this code can
identify — which is exactly what an external check requires.

**The quantities are not commensurable and this tool never pretends otherwise.**
Published APR is annual, over pool lifetimes of 150-562 days, net of operator
fee, with deposits and withdrawals moving the base. Ours is a few days, gross,
realised-only, on 4 of ~90 markets, counting only fills where the pool is one
side. Comparing magnitudes would be meaningless. Sign and rank order are the
only things asserted, and the binomial p-value is reported so "9 of 11" is
readable as evidence rather than as a number that merely sounds good.

Enumeration quirk, measured: `index` is an account_index to start from and the
listing runs **downward**. `index=0` returns an empty list — not because there
are no pools, but because none sit below zero. Start above the masked range.

Usage:
    python -m src.analysis.pool_validation --days 20260728 20260729 20260730
    python -m src.analysis.pool_validation --days 20260731 --markets 0 1 --min-fills 20
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from src.analysis.participant_pnl import accumulate_pnl
from src.analysis.pnl_persistence import spearman

API = "https://mainnet.zklighter.elliot.ai/api/v1/publicPoolsMetadata"
TAPE_DIR = Path("data/participants")
# Above the top of the masked account range, so the descending sweep starts
# past every pool that exists.
SWEEP_FROM = 281474976800000
PAGE = 100
TIMEOUT = 25


@dataclass
class PoolRow:
    account_index: int
    name: str
    apr: float
    sharpe: float
    status: int
    our_pnl: float = 0.0
    fills: int = 0
    notional: float = 0.0
    maker_fills: int = 0
    markets: set[int] = field(default_factory=set)

    @property
    def agrees(self) -> bool:
        return (self.apr > 0) == (self.our_pnl > 0)

    @property
    def maker_share(self) -> float:
        return self.maker_fills / self.fills if self.fills else 0.0


def binomial_tail(k: int, n: int) -> float:
    """P(at least k of n agree | fair coin). One-sided, the honest direction."""
    if n <= 0:
        return float("nan")
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def fetch_page(index: int) -> list[dict]:
    url = f"{API}?index={index}&limit={PAGE}&filter=all"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                return json.loads(resp.read()).get("public_pools") or []
        except Exception:
            time.sleep(2 * (attempt + 1))
    return []


def enumerate_pools(sweep_from: int = SWEEP_FROM, max_pages: int = 200) -> dict[int, dict]:
    """Walk the showcase downward until a page adds nothing new."""
    pools: dict[int, dict] = {}
    index = sweep_from
    for _ in range(max_pages):
        page = fetch_page(index)
        if not page:
            break
        before = len(pools)
        for p in page:
            pools[p["account_index"]] = p
        if len(pools) == before:
            break
        index = min(p["account_index"] for p in page) - 1
        time.sleep(0.25)
    return pools


def fills_touching(path: Path, wanted: set[int]) -> list[dict]:
    """Only the fills where a wanted account is one side.

    Filtering before parsing the whole day keeps memory bounded: a market-1
    day is ~1.6M records and sorting all of them to find a handful of pools
    exhausts the box.
    """
    out: list[dict] = []
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("ask_account_id") in wanted or rec.get("bid_account_id") in wanted:
                    out.append(rec)
    except (EOFError, OSError):
        # Current-day file still being appended to; its gzip trailer is absent.
        pass
    return out


def collect(pools: dict[int, dict], days: list[str], markets: list[int]) -> dict[int, PoolRow]:
    rows = {
        idx: PoolRow(account_index=idx, name=p.get("name") or "",
                     apr=p.get("annual_percentage_yield") or 0.0,
                     sharpe=p.get("sharpe_ratio") or 0.0,
                     status=p.get("status", -1))
        for idx, p in pools.items()
    }
    wanted = set(rows)
    for market in markets:
        for day in days:
            path = TAPE_DIR / f"trades_full_{market}_{day}.jsonl.gz"
            if not path.exists():
                continue
            recs = fills_touching(path, wanted)
            if not recs:
                continue
            per_account = accumulate_pnl(recs)
            for idx, acct in per_account.items():
                row = rows.get(idx)
                if row is None:
                    continue
                row.our_pnl += acct.realised
                row.fills += acct.fills
                row.notional += acct.notional
                row.maker_fills += acct.maker_fills
                row.markets.add(market)
    return rows


def score(rows: list[PoolRow]) -> dict:
    """Sign agreement, its p-value, and rank correlation. Pure — testable."""
    traded = [r for r in rows if r.fills > 0]
    if not traded:
        return {"n": 0, "agree": 0, "p": float("nan"), "rho": float("nan")}
    agree = sum(1 for r in traded if r.agrees)
    return {
        "n": len(traded),
        "agree": agree,
        "p": binomial_tail(agree, len(traded)),
        "rho": spearman([r.apr for r in traded], [r.our_pnl for r in traded]),
    }


def report(rows: dict[int, PoolRow], min_fills: int) -> None:
    traded = sorted((r for r in rows.values() if r.fills > 0),
                    key=lambda r: -r.fills)
    print(f"pools enumerated: {len(rows)} "
          f"({sum(1 for r in rows.values() if r.status == 1)} active)")
    print(f"pools present in the tape: {len(traded)}\n")

    print(f"{'name':<34}{'showcase APR':>13}{'our PnL':>13}{'fills':>8}"
          f"{'mkr%':>6}  agree")
    for r in traded:
        print(f"{r.name[:33]:<34}{r.apr:>12.2f}%{r.our_pnl:>+13.2f}{r.fills:>8}"
              f"{100*r.maker_share:>5.0f}%  {'yes' if r.agrees else 'NO'}")

    for label, subset in (("all traded pools", traded),
                          (f"pools with >={min_fills} fills",
                           [r for r in traded if r.fills >= min_fills])):
        s = score(subset)
        if not s["n"]:
            continue
        print(f"\n{label}: {s['agree']}/{s['n']} agree in sign, "
              f"P = {s['p']:.4f}, Spearman = {s['rho']:+.4f}")

    print("\nNOTE: APR is annual, net of operator fee, over the pool's lifetime "
          "and\nall ~90 markets. Ours is these days, gross, realised-only, on the "
          "markets\nlisted. Sign and order only — magnitudes are not comparable.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", required=True, help="YYYYMMDD")
    ap.add_argument("--markets", type=int, nargs="+", default=[0, 1, 2, 24])
    ap.add_argument("--min-fills", type=int, default=20)
    ap.add_argument("--cache", type=Path,
                    help="read/write the enumeration here instead of refetching")
    args = ap.parse_args()

    if args.cache and args.cache.exists():
        pools = {int(k): v for k, v in json.loads(args.cache.read_text()).items()}
        print(f"pools from cache {args.cache}")
    else:
        pools = enumerate_pools()
        if not pools:
            sys.exit("enumerated zero pools — the endpoint changed shape or is unreachable")
        if args.cache:
            args.cache.write_text(json.dumps({str(k): v for k, v in pools.items()}))

    rows = collect(pools, args.days, args.markets)
    report(rows, args.min_fills)
    return 0


if __name__ == "__main__":
    sys.exit(main())
