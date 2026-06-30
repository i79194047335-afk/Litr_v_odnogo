"""
Historical Lighter trade backfill via 0xArchive → JSONL per UTC day in data/ticks/.

Produces files with the SAME schema and naming as src/collector/lighter_ticks.py
(our live collector), so the backtester is source-agnostic:
    data/ticks/trades_{market_id}_{YYYYMMDD}.jsonl
    {"m": int, "p": float, "s": float, "t": ms, "side": "buy"|"sell"}

Reads OXARCHIVE_API_KEY from .env. Symbols and start date come from config.yaml
(section `backfill`). Uses cursor-based pagination, 1000 rows/page (1 credit/page).

Usage:
    python -m src.collector.oxarchive_backfill list-markets
        → prints every Lighter symbol available on 0xArchive with its market_id,
          24h volume, and current status. Use this to confirm symbols before
          backfilling (e.g. is gold "XAU" or "GOLD"?).

    python -m src.collector.oxarchive_backfill backfill
        → backfills every symbol listed in config['backfill']['symbols'] from
          config['backfill']['start_date'] up to today. Idempotent: if a UTC
          day's JSONL already exists for a market, that day is SKIPPED, so the
          script is safe to re-run after interruption.

Side mapping (the cross-check hypothesis):
    0xArchive Trade.side is Literal['A', 'B']; meaning is undocumented.
    Working assumption — same convention as Lighter's own is_maker_ask:
        'A' = maker on Ask → taker BOUGHT  → "buy"
        'B' = maker on Bid → taker SOLD    → "sell"
    The compare_sources script will verify this against our live collector on
    an overlapping day. If results disagree, flip the mapping in _side_to_str.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger
from oxarchive import Client, LighterInstrument, OxArchiveError, Trade
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging


# --- mapping helpers --------------------------------------------------------

def _side_to_str(side: str) -> str:
    """0xArchive Trade.side ('A'|'B') → our JSONL 'buy'|'sell'.

    Hypothesis: 'A' = maker on Ask → taker bought; 'B' = maker on Bid → taker sold.
    Will be verified by compare_sources on overlapping live data; flip if wrong.
    """
    if side == "A":
        return "buy"
    if side == "B":
        return "sell"
    raise ValueError(f"unexpected 0xArchive side: {side!r}")


def _trade_to_record(t: Trade, market_id: int) -> dict:
    """Convert 0xArchive Trade → live-collector JSONL schema."""
    return {
        "m": market_id,
        "p": float(t.price),
        "s": float(t.size),
        "t": int(t.timestamp.timestamp() * 1000),  # datetime → ms
        "side": _side_to_str(t.side),
    }


def _is_retryable(exc: BaseException) -> bool:
    """Retry on rate-limit and transient server errors; fail fast on auth/bad request."""
    if isinstance(exc, OxArchiveError):
        # 429 rate limit, 5xx transient
        return exc.code == 429 or 500 <= exc.code < 600
    # Network blips: httpx raises various exceptions; retry on anything not API-level.
    return not isinstance(exc, (KeyboardInterrupt, SystemExit))


# Retry wrapper for one page call. Fails fast on 4xx (except 429).
_tenacity_logger = logging.getLogger("tenacity")


def _patch_cursor_handling(trades_resource) -> None:
    """Prevent SDK from mangling composite cursor strings like '1759276893930_553252220005'.

    oxarchive v1.7.0 passes ``next_cursor`` through ``_convert_timestamp()``,
    which calls ``int(cursor_str)``.  Python's ``int()`` treats ``_`` as a digit
    separator, so ``int("1759276893930_553252220005")`` → ``1759276893930553252220005``
    — a huge garbage integer the API doesn't understand.  The API then returns
    the same first page forever.

    This monkey-patch detects composite cursor strings and passes them through
    unchanged instead of mangling them.
    """
    original = trades_resource._convert_timestamp  # bound method

    def safe_convert(ts):
        # Cursor tokens from 0xArchive are opaque strings, not timestamps.
        # They always contain an underscore (e.g. "1759276893930_553252220005").
        if isinstance(ts, str) and "_" in ts:
            return ts
        return original(ts)

    trades_resource._convert_timestamp = safe_convert


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(_tenacity_logger, logging.WARNING),
    reraise=True,
)
def _fetch_page(
    client: Client,
    symbol: str,
    start: datetime,
    end: datetime,
    cursor: str | None,
    limit: int,
):
    return client.lighter.trades.list(
        symbol, start=start, end=end, cursor=cursor, limit=limit
    )


# --- main backfill ----------------------------------------------------------

def _iter_existing_days(out_dir: Path, market_id: int) -> set[str]:
    """Days (YYYYMMDD) for which a JSONL file already exists for this market."""
    days: set[str] = set()
    for p in out_dir.glob(f"trades_{market_id}_*.jsonl"):
        stem = p.stem  # trades_{m}_{YYYYMMDD}
        try:
            day = stem.rsplit("_", 1)[-1]
            if len(day) == 8 and day.isdigit():
                days.add(day)
        except IndexError:
            pass
    return days


def _utc_day_bounds(d: datetime) -> tuple[datetime, datetime, str]:
    """For a UTC datetime, return (day_start, day_end_exclusive, 'YYYYMMDD')."""
    day_start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end, day_start.strftime("%Y%m%d")


def _backfill_day(
    client: Client,
    symbol: str,
    market_id: int,
    day_start: datetime,
    day_end: datetime,
    out_path: Path,
    page_limit: int,
    pause_between_pages: float,
    max_pages: int = 5000,
) -> int:
    """Backfill one UTC day for one market. Writes atomically: .part → rename."""
    tmp_path = out_path.with_suffix(".jsonl.part")
    cursor: str | None = None
    n_rows = 0
    n_pages = 0
    prev_cursor: str | None = None

    with tmp_path.open("w") as f:
        while True:
            resp = _fetch_page(
                client, symbol, day_start, day_end, cursor, page_limit
            )
            n_pages += 1
            for tr in resp.data:
                rec = _trade_to_record(tr, market_id)
                f.write(json.dumps(rec) + "\n")
                n_rows += 1

            cursor = resp.next_cursor
            if not cursor:
                break

            # Progress report every 50 pages so the user knows it's alive.
            if n_pages % 50 == 0:
                logger.info(
                    f"{symbol} {day_start.date()}: {n_pages} pages, "
                    f"{n_rows} rows so far..."
                )

            # Guard 1: cursor must advance — if the API returns the same
            # cursor twice, we're in a pagination loop (broken cursor conversion,
            # server bug, etc.).  Bail instead of writing duplicate data forever.
            if cursor == prev_cursor:
                logger.error(
                    f"{symbol} {day_start.date()}: cursor stuck at {cursor!r} "
                    f"after {n_pages} page(s) — API returned same next_cursor "
                    f"twice, pagination is broken.  Wrote {n_rows} rows; "
                    f"discarding .part file."
                )
                tmp_path.unlink(missing_ok=True)
                return 0  # data was discarded — don't count garbage rows

            prev_cursor = cursor

            # Guard 2: hard cap on pages per day — a single UTC day for one
            # market should never need more than this many pages.  If we hit
            # the cap the cursor is probably looping.
            if n_pages >= max_pages:
                logger.error(
                    f"{symbol} {day_start.date()}: reached {n_pages} pages "
                    f"(max_pages={max_pages}), stopping day.  Wrote {n_rows} "
                    f"rows; discarding .part file."
                )
                tmp_path.unlink(missing_ok=True)
                return 0  # data was discarded — don't count garbage rows

            time.sleep(pause_between_pages)  # be polite under 15 req/s

    tmp_path.rename(out_path)
    logger.info(
        f"{symbol} (m={market_id}) {day_start.date()}: "
        f"{n_rows} trades in {n_pages} page(s)"
    )
    return n_rows


def _load_lighter_instruments(client: Client) -> dict[str, LighterInstrument]:
    """symbol → LighterInstrument for ALL active markets on Lighter."""
    instruments = client.lighter.instruments.list()
    return {ins.symbol: ins for ins in instruments}


def cmd_list_markets(client: Client) -> None:
    """Print every Lighter market on 0xArchive with key metadata."""
    instruments = client.lighter.instruments.list()
    instruments.sort(key=lambda i: i.market_id)
    print(f"{'symbol':<10} {'m_id':>5} {'active':>7} {'maker':>8} {'taker':>8} "
          f"{'tick':>6} {'min_base':>10}")
    print("-" * 60)
    for ins in instruments:
        tick = 10 ** -ins.price_decimals
        print(
            f"{ins.symbol:<10} {ins.market_id:>5} {str(ins.is_active):>7} "
            f"{ins.maker_fee:>8.4f} {ins.taker_fee:>8.4f} "
            f"{tick:>6} {ins.min_base_amount:>10}"
        )
    print(f"\nTotal: {len(instruments)} markets")


def _parse_start(v) -> datetime:
    """YAML start_date may be str 'YYYY-MM-DD' or already a date object."""
    if isinstance(v, str):
        return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
    return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)


def cmd_backfill(client: Client, cfg: dict, out_dir: Path) -> None:
    bf = cfg["backfill"]
    symbols: list[str] = bf["symbols"]
    raw_start = bf["start_date"]
    # Two supported shapes:
    #   start_date: "2025-10-01"                    → same start for all symbols
    #   start_date: { BTC: "2025-10-01", XAU: ... } → per-symbol start
    if isinstance(raw_start, dict):
        # validate every requested symbol has an entry
        missing_dates = [s for s in symbols if s not in raw_start]
        if missing_dates:
            logger.error(f"backfill.start_date missing entries for: {missing_dates}")
            sys.exit(2)
        start_per_symbol: dict[str, datetime] = {
            s: _parse_start(raw_start[s]) for s in symbols
        }
    else:
        common = _parse_start(raw_start)
        start_per_symbol = {s: common for s in symbols}
    page_limit = bf.get("page_limit", 1000)
    pause = bf.get("pause_between_pages_s", 0.1)  # ~10 req/s, well under 15
    max_pages = bf.get("max_pages_per_day", 5000)  # safety cap per market-day

    # Optional end_date: stop backfill at this date (exclusive).  Defaults to
    # today UTC, which means "up to yesterday" (today is live-collector territory).
    # Set to e.g. "2025-11-01" to backfill only October 2025.
    raw_end = bf.get("end_date")
    if raw_end is not None:
        end_utc = _parse_start(raw_end)
    else:
        end_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # Work around oxarchive v1.7.0 cursor-conversion bug (see _patch_cursor_handling).
    _patch_cursor_handling(client.lighter.trades)

    instruments = _load_lighter_instruments(client)

    # Validate all requested symbols exist on Lighter before starting.
    missing = [s for s in symbols if s not in instruments]
    if missing:
        available = ", ".join(sorted(instruments.keys()))
        logger.error(
            f"symbols not found on Lighter via 0xArchive: {missing}\n"
            f"available: {available}"
        )
        sys.exit(2)

    out_dir.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    for symbol in symbols:
        ins = instruments[symbol]
        market_id = ins.market_id
        logger.info(
            f"=== {symbol} (m={market_id}, maker={ins.maker_fee}, "
            f"taker={ins.taker_fee}, active={ins.is_active}) ==="
        )

        existing = _iter_existing_days(out_dir, market_id)

        # iterate day by day from this symbol's start up to (but not including) end_utc
        cur = start_per_symbol[symbol]
        while cur < end_utc:
            day_start, day_end, day_str = _utc_day_bounds(cur)
            cur = day_end
            if day_str in existing:
                logger.debug(f"{symbol} {day_str}: already on disk, skipping")
                continue
            out_path = out_dir / f"trades_{market_id}_{day_str}.jsonl"
            try:
                n = _backfill_day(
                    client, symbol, market_id, day_start, day_end,
                    out_path, page_limit, pause, max_pages
                )
                total_rows += n
            except OxArchiveError as e:
                # Per-day failure from the API layer (4xx other than 429,
                # or 5xx after retries exhausted).
                logger.error(
                    f"{symbol} {day_str}: API error {e.code}: {e} — skipping day"
                )
            except Exception as e:
                # Catch-all for unexpected errors (e.g. cursor format change,
                # network blips after retry exhaustion, SDK bugs).
                # Don't let one bad day crash the entire multi-day backfill.
                logger.error(
                    f"{symbol} {day_str}: unexpected error {type(e).__name__}: "
                    f"{e} — skipping day"
                )

    logger.info(f"done. wrote {total_rows} trades total.")


# --- entrypoint -------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-markets", help="print all Lighter symbols on 0xArchive")
    sub.add_parser("backfill", help="backfill symbols from config.yaml")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("OXARCHIVE_API_KEY")
    if not api_key:
        logger.error("OXARCHIVE_API_KEY not set in environment / .env")
        sys.exit(1)

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    out_dir = Path(cfg["collector"]["out_dir"])  # same dir as live collector
    client = Client(api_key=api_key)

    try:
        if args.cmd == "list-markets":
            cmd_list_markets(client)
        elif args.cmd == "backfill":
            cmd_backfill(client, cfg, out_dir)
    finally:
        # SDK uses httpx; explicit close is good practice.
        if hasattr(client, "close"):
            client.close()


if __name__ == "__main__":
    main()
