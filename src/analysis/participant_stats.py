"""Aggregate the participant tape into per-account statistics.

One record is one fill with two sides. `is_maker_ask` says which side rested;
the other crossed the spread. Both sides are credited — a fill is volume for
whoever made it and volume for whoever took it — so the sum over accounts is
twice the market's one-sided turnover.

Account type is NOT inferred from the magnitude of the id. The two ranges in
the tape look separable, but that encoding is undocumented; `data/account_index.json`
carries what the venue itself reports (see build_account_index.py). Accounts
missing from that map are reported as type None rather than guessed at.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple


class Sides(NamedTuple):
    """Who rested and who crossed, resolved from is_maker_ask."""

    maker: int
    taker: int
    taker_is_buyer: bool


@dataclass
class AccountStats:
    """What one account did over the fills it appeared in."""

    account_id: int
    maker_fills: int = 0
    taker_fills: int = 0
    maker_notional: float = 0.0
    taker_notional: float = 0.0
    liquidations: int = 0
    last_position: float = 0.0
    markets: set[int] = field(default_factory=set)

    @property
    def fills(self) -> int:
        """Role-participations, not distinct fills: an account on both sides counts twice."""
        return self.maker_fills + self.taker_fills

    @property
    def notional(self) -> float:
        return self.maker_notional + self.taker_notional

    @property
    def maker_share(self) -> float:
        """Fraction of this account's participations where it rested. 0.0 if idle."""
        return self.maker_fills / self.fills if self.fills else 0.0


def sides_of(rec: dict) -> Sides:
    """Resolve maker/taker from the record's own is_maker_ask flag.

    is_maker_ask=True  -> the ask rested, so the bidder crossed upward (a buy).
    is_maker_ask=False -> the bid rested, so the asker crossed downward (a sell).
    """
    ask = rec["ask_account_id"]
    bid = rec["bid_account_id"]
    if rec["is_maker_ask"]:
        return Sides(maker=ask, taker=bid, taker_is_buyer=True)
    return Sides(maker=bid, taker=ask, taker_is_buyer=False)


def notional(rec: dict) -> float:
    """Quote-currency value of the fill. Sizes and prices arrive as strings."""
    return float(rec["size"]) * float(rec["price"])


def accumulate(records: Iterable[dict]) -> dict[int, AccountStats]:
    """Roll a stream of fills up per account."""
    stats: dict[int, AccountStats] = {}

    def get(account_id: int) -> AccountStats:
        entry = stats.get(account_id)
        if entry is None:
            entry = stats[account_id] = AccountStats(account_id=account_id)
        return entry

    for rec in records:
        sides = sides_of(rec)
        value = notional(rec)
        is_liq = rec.get("type") == "liquidation"
        market = rec.get("market_id")

        maker = get(sides.maker)
        maker.maker_fills += 1
        maker.maker_notional += value
        if market is not None:
            maker.markets.add(market)

        taker = get(sides.taker)
        taker.taker_fills += 1
        taker.taker_notional += value
        if market is not None:
            taker.markets.add(market)

        if is_liq:
            # Both sides of a liquidation are tallied: one was liquidated, the
            # other absorbed it, and the tape does not say which is which.
            maker.liquidations += 1
            taker.liquidations += 1

        # Position carried in the record is the state BEFORE this fill, so the
        # last one seen is the most recent known state, not the final one.
        maker_pos = rec.get("maker_position_size_before")
        if maker_pos is not None:
            maker.last_position = float(maker_pos)
        taker_pos = rec.get("taker_position_size_before")
        if taker_pos is not None:
            taker.last_position = float(taker_pos)

    return stats


def read_tape(paths: Iterable[Path]) -> Iterator[dict]:
    """Stream fills out of gzipped JSONL, tolerating the open day's torn tail."""
    for path in paths:
        try:
            with gzip.open(path, "rt") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue  # half-written final line
        except (EOFError, OSError):
            # Current-day files are still being appended to; the gzip trailer is
            # missing. Everything read before the tear is still good.
            continue


def load_account_types(path: Path = Path("data/account_index.json")) -> dict[int, int | None]:
    """account_index -> account_type, as reported by the venue. Empty if absent."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {int(k): v.get("account_type") for k, v in raw.items()}
