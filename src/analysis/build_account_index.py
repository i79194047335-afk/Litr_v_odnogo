"""Build a local account_index -> account_type map from the exchange itself.

The tape gives account ids but not what kind of account they are. The ids fall
into two visually distinct ranges, and it is tempting to classify by magnitude —
but that is a guess about an undocumented encoding. The venue answers the
question directly: `GET /account?by=index` returns `account_type` (0 = standard,
1 = sub-account). This asks, once, and caches.

Types do not change, so the cache is append-only: a second run only fetches ids
it has never seen. Resumable — interrupt it and rerun.

Usage:
    python -m src.analysis.build_account_index --ids-from data/participants/
    python -m src.analysis.build_account_index --ids 27927 71533
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import requests

API = "https://mainnet.zklighter.elliot.ai/api/v1/account"
CACHE = Path("data/account_index.json")

# Measured 2026-08-01: 60 sequential lookups took 20s with no 429s. Standard
# accounts get 60 weighted requests/minute and this endpoint weighs 2, so the
# observed ceiling is well under what the docs allow. Stay deliberately slower.
DELAY = 0.35
TIMEOUT = 15
RETRIES = 3


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"cache at {CACHE} is unreadable ({exc}) — inspect it, do not overwrite blindly")


def save_cache(cache: dict[str, dict]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
    tmp.replace(CACHE)


def ids_from_tape(directory: Path) -> set[int]:
    """Every account id appearing on either side of any fill in the directory."""
    ids: set[int] = set()
    files = sorted(directory.glob("*.jsonl.gz"))
    if not files:
        sys.exit(f"no .jsonl.gz under {directory}")
    for path in files:
        try:
            with gzip.open(path, "rt") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a half-written line in the day's open file
                    for side in ("ask_account_id", "bid_account_id"):
                        value = rec.get(side)
                        if isinstance(value, int):
                            ids.add(value)
        except (EOFError, OSError) as exc:
            # The collector holds the current day's file open; its gzip trailer
            # is not written yet. Read what is there and say so.
            print(f"  {path.name}: truncated ({exc}) — using what was readable",
                  file=sys.stderr)
    return ids


def fetch(index: int) -> dict | None:
    """Ask the venue about one account. None if it has nothing to say."""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(API, params={"by": "index", "value": index}, timeout=TIMEOUT)
            if resp.status_code == 200:
                accounts = resp.json().get("accounts") or []
                if not accounts:
                    return None
                acct = accounts[0]
                return {
                    "account_type": acct.get("account_type"),
                    "l1_address": acct.get("l1_address"),
                    "status": acct.get("status"),
                }
            if resp.status_code == 429:
                time.sleep(5 * attempt)
                continue
            if 400 <= resp.status_code < 500:
                return None  # unknown index; retrying will not help
        except requests.RequestException:
            time.sleep(2 * attempt)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-from", type=Path, help="directory of *.jsonl.gz tape files")
    ap.add_argument("--ids", type=int, nargs="+", help="explicit account indexes")
    ap.add_argument("--limit", type=int, help="stop after this many new lookups")
    args = ap.parse_args()

    if not args.ids_from and not args.ids:
        ap.error("need --ids-from or --ids")

    wanted: set[int] = set(args.ids or [])
    if args.ids_from:
        wanted |= ids_from_tape(args.ids_from)

    cache = load_cache()
    missing = sorted(i for i in wanted if str(i) not in cache)
    print(f"ids in scope: {len(wanted)}, already cached: {len(wanted) - len(missing)}, "
          f"to fetch: {len(missing)}")
    if not missing:
        print("nothing to do")
        return 0
    if args.limit:
        missing = missing[: args.limit]
        print(f"limited to {len(missing)} this run")

    added = failed = 0
    for n, index in enumerate(missing, 1):
        info = fetch(index)
        if info is None:
            failed += 1
        else:
            cache[str(index)] = info
            added += 1
        time.sleep(DELAY)
        if n % 200 == 0:
            save_cache(cache)  # checkpoint: an interrupted run keeps its work
            print(f"  {n}/{len(missing)} (added {added}, unknown {failed})")

    save_cache(cache)
    types: dict[str, int] = {}
    for entry in cache.values():
        key = str(entry.get("account_type"))
        types[key] = types.get(key, 0) + 1
    print(f"cached {len(cache)} accounts (+{added} this run, {failed} unknown)")
    print(f"by account_type: {types}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
