"""Do account ids mean the same account tomorrow as they did on day one?

Ranking participants by account id assumes the id is a stable handle on one
participant. Nothing measured so far establishes that. If the venue recycles
ids, or an id migrates between owners, then every per-account number in this
track — role, turnover, and eventually PnL — is an average over whoever held
the id, and the ranking is meaningless.

The check is cheap because the answer is already half on disk. `account_index`
cached `account_type` and `l1_address` for every account seen up to 2026-08-01.
Re-asking the venue about the accounts from the first collection day and
comparing against that snapshot turns a structural assumption into a
measurement.

What a disagreement means, in order of how badly it breaks the track:

- `l1_address` changed  — the id now answers to a different owner. Fatal:
  per-account aggregation across days is invalid.
- `account_type` changed — the id changed kind. Also fatal for grouping, and
  contradicts the claim in build_account_index that types do not change.
- `status` changed      — accounts open and close. Expected, not a persistence
  failure, and reported separately.

Read-only against the venue and against the cache: this never writes
`data/account_index.json`. A tool that repairs the evidence it is auditing
cannot be rerun to check itself.

Usage:
    python -m src.analysis.account_persistence --market 1 --day 20260728
    python -m src.analysis.account_persistence --market 1 --day 20260728 --limit 200
    python -m src.analysis.account_persistence --ids 27927 71533
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

API = "https://mainnet.zklighter.elliot.ai/api/v1/account"
CACHE = Path("data/account_index.json")
TAPE_DIR = Path("data/participants")

# Same budget build_account_index measured: 60 sequential lookups in 20s with
# no 429s, endpoint weight 2 against 60 weighted requests/minute. Deliberately
# slower than the observed ceiling.
DELAY = 0.35
TIMEOUT = 15
RETRIES = 3

# Fields whose change breaks per-account aggregation, versus fields that are
# expected to move. Kept as data so the test can state the rule independently.
IDENTITY_FIELDS = ("account_type", "l1_address")
VOLATILE_FIELDS = ("status",)


@dataclass
class Comparison:
    """Outcome of comparing one account's fresh reading against the cache."""

    identity_changes: dict[int, dict[str, tuple]] = field(default_factory=dict)
    volatile_changes: dict[int, dict[str, tuple]] = field(default_factory=dict)
    vanished: list[int] = field(default_factory=list)
    unreachable: list[int] = field(default_factory=list)
    agreed: int = 0

    @property
    def checked(self) -> int:
        return (self.agreed + len(self.identity_changes) + len(self.volatile_changes)
                + len(self.vanished))


def compare(cached: dict[str, dict], fresh: dict[int, dict | None],
            unreachable: list[int]) -> Comparison:
    """Compare fresh readings against the cached snapshot. Pure — no network.

    `fresh[i] is None` means the venue answered and disowned the id: it knows
    nothing about an account it previously described. That is a vanished
    account, which is different from a request that never got an answer —
    the latter is our failure and is carried in `unreachable`, never counted
    as agreement.
    """
    result = Comparison(unreachable=sorted(unreachable))
    for index, reading in sorted(fresh.items()):
        before = cached.get(str(index))
        if before is None:
            # Not in the snapshot: nothing to compare against, so it cannot
            # testify either way. Treating it as agreement would inflate the
            # pass rate with accounts we never knew.
            continue
        if reading is None:
            result.vanished.append(index)
            continue
        identity = {f: (before.get(f), reading.get(f))
                    for f in IDENTITY_FIELDS
                    if before.get(f) != reading.get(f)}
        volatile = {f: (before.get(f), reading.get(f))
                    for f in VOLATILE_FIELDS
                    if before.get(f) != reading.get(f)}
        if identity:
            result.identity_changes[index] = identity
        elif volatile:
            result.volatile_changes[index] = volatile
        else:
            result.agreed += 1
    return result


def verdict(result: Comparison) -> tuple[int, str]:
    """Exit code and one-line verdict. Non-zero on any identity change.

    A vanished account is also non-zero: an id the venue no longer recognises
    cannot be ranked, and silently dropping it would shrink the population
    without saying so.
    """
    if result.identity_changes:
        return 1, (f"FAIL: {len(result.identity_changes)} of {result.checked} accounts "
                   f"changed identity — per-account aggregation across days is invalid")
    if result.vanished:
        return 1, (f"FAIL: {len(result.vanished)} of {result.checked} accounts vanished "
                   f"— the venue no longer knows ids it previously described")
    if result.checked == 0:
        return 1, "FAIL: nothing was compared — no account appeared in both the tape and the cache"
    return 0, (f"PASS: {result.agreed} of {result.checked} accounts identical on "
               f"{', '.join(IDENTITY_FIELDS)}")


def ids_from_day(market: int, day: str) -> set[int]:
    """Every account id on either side of any fill for one market-day."""
    path = TAPE_DIR / f"trades_full_{market}_{day}.jsonl.gz"
    if not path.exists():
        sys.exit(f"no tape at {path}")
    ids: set[int] = set()
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
        print(f"  {path.name}: truncated ({exc}) — using what was readable", file=sys.stderr)
    return ids


def fetch(index: int) -> tuple[dict | None, bool]:
    """Ask the venue about one account.

    Returns (reading, reached). `reached` False means we never got an answer —
    our failure, not evidence about the account. The distinction matters: a
    timeout counted as "vanished" would fake a persistence failure, and counted
    as agreement would hide one (WORKFLOW.md §2.3.5).
    """
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(API, params={"by": "index", "value": index}, timeout=TIMEOUT)
            if resp.status_code == 200:
                accounts = resp.json().get("accounts") or []
                if not accounts:
                    return None, True
                acct = accounts[0]
                return {
                    "account_type": acct.get("account_type"),
                    "l1_address": acct.get("l1_address"),
                    "status": acct.get("status"),
                }, True
            if resp.status_code == 429:
                time.sleep(5 * attempt)
                continue
            if 400 <= resp.status_code < 500:
                return None, True  # the venue disowns this index
        except requests.RequestException:
            time.sleep(2 * attempt)
    return None, False


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        sys.exit(f"no cache at {CACHE} — run build_account_index first")
    try:
        return json.loads(CACHE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"cache at {CACHE} is unreadable ({exc}) — inspect it, do not overwrite blindly")


def report(result: Comparison) -> None:
    print(f"compared: {result.checked}  agreed: {result.agreed}")
    if result.identity_changes:
        print(f"\nIDENTITY CHANGED ({len(result.identity_changes)}):")
        for index, changes in list(result.identity_changes.items())[:20]:
            for fld, (was, now) in changes.items():
                print(f"  {index}: {fld} {was!r} -> {now!r}")
        if len(result.identity_changes) > 20:
            print(f"  ... and {len(result.identity_changes) - 20} more")
    if result.vanished:
        print(f"\nVANISHED ({len(result.vanished)}): {result.vanished[:20]}")
    if result.volatile_changes:
        print(f"\nstatus moved ({len(result.volatile_changes)}) — expected, not a persistence failure:")
        for index, changes in list(result.volatile_changes.items())[:10]:
            for fld, (was, now) in changes.items():
                print(f"  {index}: {fld} {was!r} -> {now!r}")
    if result.unreachable:
        print(f"\nUNREACHABLE ({len(result.unreachable)}) — our failure, not evidence: "
              f"{result.unreachable[:20]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, help="market id of the tape to take ids from")
    ap.add_argument("--day", help="YYYYMMDD of the tape to take ids from")
    ap.add_argument("--ids", type=int, nargs="+", help="explicit account indexes")
    ap.add_argument("--limit", type=int, help="check only the first N ids (sorted)")
    args = ap.parse_args()

    if not args.ids and not (args.market is not None and args.day):
        ap.error("need --ids, or both --market and --day")

    wanted: set[int] = set(args.ids or [])
    if args.market is not None and args.day:
        wanted |= ids_from_day(args.market, args.day)

    cache = load_cache()
    targets = sorted(i for i in wanted if str(i) in cache)
    skipped = len(wanted) - len(targets)
    if args.limit:
        targets = targets[: args.limit]
    print(f"ids on tape: {len(wanted)}, in cache: {len(wanted) - skipped}, "
          f"checking: {len(targets)}")
    if not targets:
        sys.exit("nothing to check — no tape id is present in the cache")

    fresh: dict[int, dict | None] = {}
    unreachable: list[int] = []
    for n, index in enumerate(targets, 1):
        reading, reached = fetch(index)
        if reached:
            fresh[index] = reading
        else:
            unreachable.append(index)
        time.sleep(DELAY)
        if n % 200 == 0:
            print(f"  {n}/{len(targets)}")

    result = compare(cache, fresh, unreachable)
    report(result)
    code, line = verdict(result)
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
