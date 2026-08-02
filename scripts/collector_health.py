"""Is the capture actually capturing? Checks data, not service status.

A collector fails silently more easily than it crashes. systemd reports
`active` while the socket is dead, the day file was never rotated, or the
gzip tail is buffered and nothing has reached disk. Each of those looks
healthy from the outside and is only visible in the bytes.

The point of this project's collectors is forward-only capture: what is not
written now cannot be recovered later. A week of silent failure costs a week.
So this runs daily and reports on what a reader could verify by hand — file
growth, freshness, replayability — rather than on a green unit.

Three failure classes, deliberately distinguished, because the response to
each differs (WORKFLOW.md §2.3.5):

  STALE   — the file exists but has not grown. The collector is wedged.
  MISSING — no file for today at all. Rotation or startup failed.
  QUIET   — growth is far below its own recent norm. Might be a slow market
            at 4am, might be a half-broken subscription. Reported as a
            warning, never as a pass, and never as a failure either: calling
            a quiet Sunday an outage trains everyone to ignore the check.

Exit codes: 0 healthy, 1 something is wrong. Non-zero is what a cron mail or
a systemd OnFailure hangs off.

Usage:
    python -m scripts.collector_health
    python -m scripts.collector_health --min-free-gb 5 --json
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Each stream: directory, filename pattern with {market} and {day}, and the
# markets that must be present. Book and participants share markets by design
# so the two can be joined later.
STREAMS = {
    "book": {"dir": "data/book", "pattern": "book_{market}_{day}.jsonl.gz",
             "markets": [0, 1, 2, 24]},
    "participants": {"dir": "data/participants",
                     "pattern": "trades_full_{market}_{day}.jsonl.gz",
                     "markets": [0, 1, 2, 24]},
}

STATE_PATH = Path("data/.collector_health.json")
# Growth below this fraction of the previous run's growth is called QUIET.
# Not a tuned number: it is deliberately loose, because the check exists to
# catch "stopped", not to model market activity.
QUIET_FRACTION = 0.1

# Below this gap between runs, absence of growth means nothing: the collectors
# flush their gzip buffer every 200 records, so a quiet market can legitimately
# show zero bytes on disk for minutes. Measured the hard way — two runs 13
# seconds apart reported three healthy streams as STALE. A check that cries
# wolf on a working system gets ignored, and an ignored check is no check.
MIN_COMPARE_SECONDS = 1800


@dataclass
class StreamReport:
    name: str
    market: int
    path: str = ""
    exists: bool = False
    size: int = 0
    grew_by: int = 0
    age_seconds: float = 0.0
    status: str = "MISSING"
    note: str = ""


@dataclass
class Health:
    checked_at: str
    free_gb: float
    reports: list[StreamReport] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupt state file must not mask a real outage: treat it as a
        # first run rather than aborting the check.
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(path)


def tail_is_readable(path: Path) -> tuple[bool, str]:
    """Can the file be read back at all?

    A live gzip file has no trailer until the writer closes it, so EOFError is
    expected and healthy. What is not healthy is a file from which not one
    record can be parsed — that is a writer producing garbage.
    """
    records = 0
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                    records += 1
                except json.JSONDecodeError:
                    continue
                if records >= 5:
                    break
    except (EOFError, OSError):
        pass  # open file, trailer not written yet
    if records == 0:
        return False, "no parseable record in the file"
    return True, ""


def check(streams: dict, state: dict, now: float, day: str) -> Health:
    free_gb = shutil.disk_usage("/").free / 1e9
    health = Health(checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    free_gb=round(free_gb, 1))

    for name, spec in streams.items():
        for market in spec["markets"]:
            rep = StreamReport(name=name, market=market)
            path = Path(spec["dir"]) / spec["pattern"].format(market=market, day=day)
            rep.path = str(path)
            key = f"{name}:{market}"
            previous = state.get(key, {})

            if not path.exists():
                # A day file appears only once the first frame lands, so a
                # freshly rotated day is briefly legitimate. Anything older
                # than an hour into the UTC day is not.
                seconds_into_day = now % 86400
                if seconds_into_day < 3600 and not previous:
                    rep.status = "PENDING"
                    rep.note = "new UTC day, file not created yet"
                else:
                    rep.status = "MISSING"
                    health.problems.append(f"{key}: no file at {path}")
                health.reports.append(rep)
                continue

            rep.exists = True
            rep.size = path.stat().st_size
            rep.age_seconds = now - path.stat().st_mtime
            rep.grew_by = rep.size - previous.get("size", 0)

            # Only compare against a reading old enough for the writer's
            # buffer to have flushed; otherwise zero growth proves nothing.
            elapsed = now - previous.get("at", 0) if previous else 0.0
            comparable = bool(previous) and elapsed >= MIN_COMPARE_SECONDS \
                and previous.get("day") == day

            readable, why = tail_is_readable(path)
            if not readable:
                rep.status = "CORRUPT"
                rep.note = why
                health.problems.append(f"{key}: {why}")
            elif not comparable:
                rep.status = "OK"
                if previous and elapsed < MIN_COMPARE_SECONDS:
                    rep.note = (f"last reading {elapsed/60:.0f} min old, "
                                f"too recent to judge growth")
            elif rep.grew_by <= 0:
                rep.status = "STALE"
                rep.note = (f"no growth in {elapsed/60:.0f} min "
                            f"({rep.size} bytes)")
                health.problems.append(
                    f"{key}: no growth in {elapsed/60:.0f} min")
            elif previous.get("grew_by", 0) > 0 and \
                    rep.grew_by < QUIET_FRACTION * previous["grew_by"]:
                rep.status = "QUIET"
                rep.note = (f"grew {rep.grew_by} B against {previous['grew_by']} B "
                            f"last time")
            else:
                rep.status = "OK"

            health.reports.append(rep)
            # Only advance the baseline when it was actually used, so a burst
            # of quick runs cannot keep resetting the comparison window.
            if comparable or not previous:
                state[key] = {"size": rep.size, "grew_by": rep.grew_by,
                              "day": day, "at": now}

    return health


def render(health: Health) -> str:
    lines = [f"collector health @ {health.checked_at}  free={health.free_gb} GB"]
    for rep in health.reports:
        note = f"  — {rep.note}" if rep.note else ""
        lines.append(f"  {rep.status:<8} {rep.name}/{rep.market:<3} "
                     f"{rep.size/1e6:8.2f} MB  +{rep.grew_by/1e6:6.2f} MB"
                     f"  age {rep.age_seconds/60:5.1f} min{note}")
    if health.problems:
        lines.append("")
        lines.append("PROBLEMS:")
        lines.extend(f"  - {p}" for p in health.problems)
    else:
        lines.append("")
        lines.append("all streams growing")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-free-gb", type=float, default=3.0,
                    help="fail if free disk falls below this (default 3)")
    ap.add_argument("--state", type=Path, default=STATE_PATH)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    state = load_state(args.state)
    health = check(STREAMS, state, time.time(), utc_day())

    if health.free_gb < args.min_free_gb:
        health.problems.append(
            f"free disk {health.free_gb} GB below floor {args.min_free_gb} GB")

    save_state(args.state, state)

    if args.json:
        print(json.dumps({
            "checked_at": health.checked_at, "free_gb": health.free_gb,
            "ok": health.ok, "problems": health.problems,
            "streams": [vars(r) for r in health.reports],
        }, indent=1))
    else:
        print(render(health))

    return 0 if health.ok else 1


if __name__ == "__main__":
    sys.exit(main())
