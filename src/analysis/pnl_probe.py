"""Probe: does position/PnL state reconstruct from the tape at all?

This is the cheap gate in front of H-003 (MINING.md §5, step 3). Before
building a tested PnL module, establish whether the arithmetic closes on
real data. If it does not, the track closes here.

The trap this avoids is trivial agreement. Reconstructing state and then
checking it against itself proves nothing — any self-consistent formula
passes. The venue is used as the oracle instead:

    every fill carries `*_position_size_before` and `*_entry_quote_before`.
    Apply position accounting to fill N, and the result must equal what the
    venue reports as `before` on fill N+1 for the same account and market.

The venue computed its number independently, on its own books. Agreement is
a real prediction being confirmed; disagreement localises the broken rule.

Conventions, measured on market 1 (2026-07-28), not assumed:

  - `position_size_before` is signed; negative is short.
  - `entry_quote_before` is the magnitude of the cost basis, unsigned.
    Record one: size -0.87950, entry quote +55820.258514, price ~63262.
    A signed basis would be negative there.
  - sizes and prices arrive as strings, so every comparison is in floats
    with an explicit tolerance rather than exact equality.

Realised PnL is computed only on the closing part of a fill. Adding to a
position realises nothing; reducing or flipping realises the difference
between the entry price and the fill price on the closed quantity.

Usage:
    python -m src.analysis.pnl_probe --market 1 --day 20260728
    python -m src.analysis.pnl_probe --market 1 --day 20260728 --max-accounts 500
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from src.analysis.participant_stats import read_tape, sides_of

TAPE_DIR = Path("data/participants")

# Sizes carry 5 decimals on BTC and quotes 6, so exact float equality is the
# wrong test. This tolerance is relative to position magnitude, with a floor
# for positions near zero.
REL_TOL = 1e-6
ABS_TOL = 1e-9


@dataclass
class Position:
    """Signed size and the unsigned cost basis of the open position."""

    size: float = 0.0
    entry_quote: float = 0.0

    @property
    def entry_price(self) -> float:
        return self.entry_quote / abs(self.size) if self.size else 0.0


def apply_fill(pos: Position, signed_qty: float, price: float) -> tuple[Position, float]:
    """Advance a position by one fill. Returns the new position and realised PnL.

    `signed_qty` is positive when the account bought, negative when it sold.
    The three cases are distinct and must not be collapsed:

      opening/adding  — same sign (or from flat): basis grows, nothing realised.
      reducing        — opposite sign, |qty| <= |size|: basis shrinks
                        proportionally, PnL realised on the closed quantity.
      flipping        — opposite sign, |qty| > |size|: the old position closes
                        entirely (realising on all of it) and a new one opens
                        in the other direction at the fill price.
    """
    if signed_qty == 0:
        return Position(pos.size, pos.entry_quote), 0.0

    # From flat, or adding in the same direction.
    if pos.size == 0 or (pos.size > 0) == (signed_qty > 0):
        return Position(pos.size + signed_qty,
                        pos.entry_quote + abs(signed_qty) * price), 0.0

    closing = min(abs(signed_qty), abs(pos.size))
    entry_price = pos.entry_price
    # Long closed above entry, or short closed below entry, is a gain.
    direction = 1.0 if pos.size > 0 else -1.0
    realised = closing * (price - entry_price) * direction

    remaining = abs(pos.size) - closing
    if remaining > 0:                      # reduced, same direction as before
        new_size = remaining * (1.0 if pos.size > 0 else -1.0)
        # Basis shrinks proportionally: the entry price of what is left is
        # unchanged by a partial close.
        return Position(new_size, remaining * entry_price), realised

    flipped = abs(signed_qty) - closing    # zero when it closed exactly flat
    if flipped == 0:
        return Position(0.0, 0.0), realised
    new_size = flipped * (1.0 if signed_qty > 0 else -1.0)
    return Position(new_size, flipped * price), realised


def close_enough(predicted: float, reported: float) -> bool:
    return abs(predicted - reported) <= max(ABS_TOL, REL_TOL * max(abs(reported), 1.0))


@dataclass
class ProbeResult:
    checks: int = 0
    size_ok: int = 0
    quote_ok: int = 0
    both_ok: int = 0
    sign_flips_seen: int = 0
    first_failures: list = None

    def __post_init__(self):
        if self.first_failures is None:
            self.first_failures = []

    @property
    def size_rate(self) -> float:
        return self.size_ok / self.checks if self.checks else 0.0

    @property
    def quote_rate(self) -> float:
        return self.quote_ok / self.checks if self.checks else 0.0

    @property
    def both_rate(self) -> float:
        return self.both_ok / self.checks if self.checks else 0.0


def in_time_order(records) -> list[dict]:
    """Sort fills chronologically. The tape is not stored in that order.

    Measured on market 1 (2026-07-28): the collector appends each WS frame as
    it arrives, and fills *within* a frame are ordered newest-first, while the
    frames themselves advance. So trade ids run 26254026021, 26254026019, then
    26254026472, 26254026471, 26254026470 — descending inside each frame.

    Feeding that order to a sequential position check scores 41.8%. The same
    data sorted scores 99.99%. The arithmetic was never the problem, and a
    probe that reported the unsorted number would have condemned a working
    reconstruction.
    """
    return sorted(records, key=lambda r: (r.get("transaction_time") or 0,
                                          r.get("trade_id") or 0))


def probe(records, max_accounts: int | None = None, assume_sorted: bool = False) -> ProbeResult:
    """Predict each account's next reported state and score the predictions.

    State is tracked per (account, market): a position is per-instrument, and
    merging markets would make every prediction wrong for anyone trading two.

    Records are put in time order first (see `in_time_order`). `assume_sorted`
    skips that only for tests that construct their own sequence.
    """
    if not assume_sorted:
        records = in_time_order(records)
    result = ProbeResult()
    # (account, market) -> what the venue said before this account's last fill,
    # advanced by that fill = our prediction for its next `before`.
    predicted: dict[tuple[int, int], Position] = {}
    seen_accounts: set[int] = set()

    for rec in records:
        market = rec.get("market_id")
        if market is None:
            continue
        try:
            sides = sides_of(rec)
            size = float(rec["size"])
            price = float(rec["price"])
        except (KeyError, TypeError, ValueError):
            continue

        # The taker crossing upward is buying; the maker on the other side sells.
        taker_qty = size if sides.taker_is_buyer else -size
        for account, role, qty in ((sides.taker, "taker", taker_qty),
                                   (sides.maker, "maker", -taker_qty)):
            if max_accounts is not None and account not in seen_accounts:
                if len(seen_accounts) >= max_accounts:
                    continue
                seen_accounts.add(account)

            raw_size = rec.get(f"{role}_position_size_before")
            raw_quote = rec.get(f"{role}_entry_quote_before")
            if raw_size is None or raw_quote is None:
                continue
            reported = Position(float(raw_size), float(raw_quote))

            key = (account, market)
            prediction = predicted.get(key)
            if prediction is not None:
                result.checks += 1
                ok_size = close_enough(prediction.size, reported.size)
                ok_quote = close_enough(prediction.entry_quote, reported.entry_quote)
                result.size_ok += ok_size
                result.quote_ok += ok_quote
                result.both_ok += ok_size and ok_quote
                if not (ok_size and ok_quote) and len(result.first_failures) < 10:
                    result.first_failures.append({
                        "account": account, "market": market, "role": role,
                        "trade_id": rec.get("trade_id"),
                        "predicted_size": prediction.size,
                        "reported_size": reported.size,
                        "predicted_quote": prediction.entry_quote,
                        "reported_quote": reported.entry_quote,
                    })

            if rec.get(f"{role}_position_sign_changed"):
                result.sign_flips_seen += 1

            # Always re-anchor on what the venue reported, then advance. Chaining
            # our own output would compound one error into every later check and
            # measure drift instead of correctness.
            advanced, _ = apply_fill(reported, qty, price)
            predicted[key] = advanced

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--day", required=True, help="YYYYMMDD")
    ap.add_argument("--max-accounts", type=int, help="limit distinct accounts tracked")
    args = ap.parse_args()

    path = TAPE_DIR / f"trades_full_{args.market}_{args.day}.jsonl.gz"
    if not path.exists():
        sys.exit(f"no tape at {path}")

    result = probe(read_tape([path]), args.max_accounts)
    if result.checks == 0:
        sys.exit("no account appeared twice — nothing was predicted, nothing measured")

    print(f"predictions checked: {result.checks}")
    print(f"  size matched:      {result.size_ok} ({result.size_rate:.4%})")
    print(f"  entry quote:       {result.quote_ok} ({result.quote_rate:.4%})")
    print(f"  both:              {result.both_ok} ({result.both_rate:.4%})")
    print(f"  sign flips seen:   {result.sign_flips_seen}")
    if result.first_failures:
        print("\nfirst disagreements:")
        for f in result.first_failures:
            print(f"  acct {f['account']} m{f['market']} {f['role']} trade {f['trade_id']}")
            print(f"    size  predicted {f['predicted_size']:.8f} vs reported {f['reported_size']:.8f}")
            print(f"    quote predicted {f['predicted_quote']:.6f} vs reported {f['reported_quote']:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
