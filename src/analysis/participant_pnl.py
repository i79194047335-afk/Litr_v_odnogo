"""Reconstruct realised PnL per account from the public trade tape.

MINING.md step 2. The venue publishes no PnL in the WS feed — `ask_account_pnl`
and `bid_account_pnl` are documented but never arrive (API_DIGEST.md, measured:
0 of 300 000). So PnL is computed, not read, and there is nothing to check the
total against directly. That is what makes H-003 a real question.

What makes the computation trustworthy despite that: the position arithmetic
underneath it is verified against the venue on every fill. `pnl_probe` predicts
each account's next reported `*_position_size_before` and matches 99.96-99.99%
across 4 markets x 2 days. This module reuses `apply_fill` unchanged, so the
book-keeping is the same code that was checked; only the accumulation is new.

Two properties of the tape shape the design:

  Anchoring. Each fill carries the venue's own view of the position *before*
  it. Rather than carrying our own state forward, every fill re-anchors on
  that reading and advances it once. A gap in the tape (reconnect, restart)
  therefore costs the PnL of the fills we missed, not the correctness of
  everything after them.

  Opening balance. An account may hold a position from before collection
  started. Its cost basis is unknown to us — but not to the venue, which
  reports it in `entry_quote_before`. Closing such a position realises PnL
  against the venue's basis, which is right. What we cannot know is PnL
  realised before the tape began, and the totals here never claim to.

Fees are excluded: the unit of `maker_fee`/`taker_fee` is undocumented and not
established by measurement (API_DIGEST.md). Assuming cents implies fees up to
50% of trade size, which is false, so no assumption is made. **These figures
are gross.** For makers, whose median implied fee is not negligible, gross and
net ranking can differ.

**Realised PnL does not sum to zero over a window, and should not be read as
if it did.** Derivatives are zero-sum, but only over closed positions. Within
one observed day the sum was -11,679 (market 24, 20260730) and +41,378 the
next — the imbalance changes sign, so it is not a bookkeeping bias. It is
carried inventory: the venue's own reported positions do not net to zero
either (+91,057 on 20260731), because participants hold positions opened
before the window or still open after it.

Restricting to accounts that end the window flat brings the sum to -1,216 on
74M of notional (0.16 bps), which is the residue of positions carried *in*
rather than out. Use `open_size` to tell "made money" from "still holding
it": an account with a large open position has an unrealised result this
module deliberately does not estimate, since marking it requires a reference
price the tape does not carry.

Usage:
    python -m src.analysis.participant_pnl --market 1 --day 20260731
    python -m src.analysis.participant_pnl --market 1 --days 20260730 20260731 --top 30
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.analysis.participant_stats import read_tape, sides_of
from src.analysis.pnl_probe import Position, apply_fill, in_time_order

TAPE_DIR = Path("data/participants")


@dataclass
class AccountPnL:
    """Realised PnL and the traffic that produced it, for one account."""

    account_id: int
    realised: float = 0.0
    fills: int = 0
    notional: float = 0.0
    maker_fills: int = 0
    closing_fills: int = 0
    liquidation_fills: int = 0
    markets: set[int] = field(default_factory=set)
    # Carried so a caller can tell "made money" from "still holding it".
    open_size: float = 0.0
    open_entry_quote: float = 0.0

    @property
    def maker_share(self) -> float:
        return self.maker_fills / self.fills if self.fills else 0.0

    @property
    def pnl_per_notional_bps(self) -> float:
        """Realised PnL as basis points of the notional this account traded."""
        return 1e4 * self.realised / self.notional if self.notional else 0.0


def accumulate_pnl(records) -> dict[int, AccountPnL]:
    """Walk the tape in time order, crediting realised PnL to both sides.

    Every fill has two participants and each carries its own reported state,
    so both are advanced. Positions are tracked per (account, market): a
    position is per-instrument, and merging markets would corrupt the basis
    for anyone trading more than one.
    """
    out: dict[int, AccountPnL] = {}

    for rec in in_time_order(records):
        market = rec.get("market_id")
        if market is None:
            continue
        try:
            sides = sides_of(rec)
            size = float(rec["size"])
            price = float(rec["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if size <= 0 or price <= 0:
            continue

        is_liq = rec.get("type") == "liquidation"
        taker_qty = size if sides.taker_is_buyer else -size

        for account, role, qty in ((sides.taker, "taker", taker_qty),
                                   (sides.maker, "maker", -taker_qty)):
            raw_size = rec.get(f"{role}_position_size_before")
            raw_quote = rec.get(f"{role}_entry_quote_before")
            if raw_size is None or raw_quote is None:
                # Without the venue's own reading there is no anchor, and
                # guessing the basis would fabricate PnL. Skip the leg.
                continue
            try:
                before = Position(float(raw_size), float(raw_quote))
            except (TypeError, ValueError):
                continue

            entry = out.get(account)
            if entry is None:
                entry = out[account] = AccountPnL(account_id=account)

            after, realised = apply_fill(before, qty, price)

            entry.realised += realised
            entry.fills += 1
            entry.notional += size * price
            entry.markets.add(market)
            if role == "maker":
                entry.maker_fills += 1
            if realised != 0.0:
                entry.closing_fills += 1
            if is_liq:
                entry.liquidation_fills += 1
            entry.open_size = after.size
            entry.open_entry_quote = after.entry_quote

    return out


def report(rows: dict[int, AccountPnL], top: int, min_fills: int) -> None:
    eligible = [r for r in rows.values() if r.fills >= min_fills]
    eligible.sort(key=lambda r: r.realised, reverse=True)

    total_realised = sum(r.realised for r in rows.values())
    total_notional = sum(r.notional for r in rows.values())
    print(f"accounts: {len(rows)}  (with >={min_fills} fills: {len(eligible)})")
    print(f"summed realised PnL over all accounts: {total_realised:,.2f}")
    print(f"two-sided notional: {total_notional:,.2f}")
    print("\nNOTE: gross of fees — fee units are undocumented and unmeasured "
          "(API_DIGEST.md).\n")

    def show(title, items):
        print(title)
        print(f"  {'account':>16} {'realised':>14} {'notional':>16} "
              f"{'bps':>8} {'fills':>7} {'mkr%':>6} {'open':>12}")
        for r in items:
            print(f"  {r.account_id:>16} {r.realised:>14,.2f} {r.notional:>16,.0f} "
                  f"{r.pnl_per_notional_bps:>8.2f} {r.fills:>7} "
                  f"{100*r.maker_share:>5.0f}% {r.open_size:>12.4f}")
        print()

    show(f"top {top} by realised PnL:", eligible[:top])
    show(f"bottom {top} by realised PnL:", eligible[-top:][::-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--day", help="YYYYMMDD")
    ap.add_argument("--days", nargs="+", help="several YYYYMMDD")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min-fills", type=int, default=20,
                    help="ignore accounts below this many fills (default 20)")
    args = ap.parse_args()

    days = args.days or ([args.day] if args.day else None)
    if not days:
        ap.error("need --day or --days")

    paths = []
    for day in days:
        path = TAPE_DIR / f"trades_full_{args.market}_{day}.jsonl.gz"
        if not path.exists():
            sys.exit(f"no tape at {path}")
        paths.append(path)

    rows = accumulate_pnl(read_tape(paths))
    if not rows:
        sys.exit("no account state could be reconstructed — tape empty or malformed")
    report(rows, args.top, args.min_fills)
    return 0


if __name__ == "__main__":
    sys.exit(main())
