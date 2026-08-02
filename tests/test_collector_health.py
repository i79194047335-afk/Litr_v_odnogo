"""Tests for the capture healthcheck.

A healthcheck that returns green on a dead collector is worse than none: it
converts a visible outage into an invisible one. So every test here is about
the check *failing* when it should, and the two that matter most are
`test_a_file_that_stopped_growing_is_not_healthy` and
`test_growth_is_measured_against_the_previous_run` — a stalled writer leaves a
file that still exists, still parses, and still has a plausible size.

Freshness is deliberately not judged by mtime alone: a gzip writer buffers, so
mtime can lag minutes behind a perfectly healthy stream. Growth between runs is
the signal that cannot be faked by buffering.
"""

from __future__ import annotations

import gzip
import json
import time

from scripts.collector_health import check


def write_gz(path, records):
    with gzip.open(path, "wt") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def spec(tmp_path, markets=(1,)):
    return {"s": {"dir": str(tmp_path), "pattern": "f_{market}_{day}.jsonl.gz",
                  "markets": list(markets)}}


# --- the failures this exists to catch -------------------------------------

def test_a_file_that_stopped_growing_is_not_healthy(tmp_path):
    """The wedged-collector case: file present, parseable, unchanged."""
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])
    state = {"s:1": {"size": path.stat().st_size, "grew_by": 500,
                     "day": "20260803", "at": time.time() - 3600}}

    health = check(spec(tmp_path), state, time.time(), "20260803")

    assert not health.ok
    assert health.reports[0].status == "STALE"


def test_a_missing_day_file_is_not_healthy(tmp_path):
    """Rotation or startup failed. Mid-day, this is never legitimate."""
    noon = 86400 * 100 + 43200            # well past the first hour of a day

    health = check(spec(tmp_path), {}, noon, "20260803")

    assert not health.ok
    assert health.reports[0].status == "MISSING"


def test_a_file_with_no_parseable_record_is_not_healthy(tmp_path):
    """A writer producing garbage still produces bytes."""
    path = tmp_path / "f_1_20260803.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("this is not json\nnor is this\n")

    health = check(spec(tmp_path), {}, time.time(), "20260803")

    assert not health.ok
    assert health.reports[0].status == "CORRUPT"


def test_growth_is_measured_against_the_previous_run(tmp_path):
    """Absolute size says nothing: a big stale file looks like a big healthy one."""
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": i} for i in range(500)])
    size = path.stat().st_size
    state = {"s:1": {"size": size, "grew_by": 1000, "day": "20260803",
                     "at": time.time() - 3600}}

    health = check(spec(tmp_path), state, time.time(), "20260803")

    assert health.reports[0].status == "STALE"
    assert health.reports[0].size > 1000          # large, and still not healthy


# --- and the ones it must not raise ----------------------------------------

def test_a_growing_file_is_healthy(tmp_path):
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": i} for i in range(100)])
    state = {"s:1": {"size": 10, "grew_by": 10, "day": "20260803",
                     "at": time.time() - 3600}}

    health = check(spec(tmp_path), state, time.time(), "20260803")

    assert health.ok
    assert health.reports[0].status == "OK"


def test_first_ever_run_is_not_a_failure(tmp_path):
    """With no previous state there is nothing to compare against.

    Reporting STALE here would make every fresh install look broken.
    """
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])

    health = check(spec(tmp_path), {}, time.time(), "20260803")

    assert health.ok


def test_missing_file_in_the_first_hour_of_a_new_day_is_pending(tmp_path):
    """Day files are created by the first frame, not at midnight.

    Failing here would page someone every single night at 00:00 UTC.
    """
    just_after_midnight = 86400 * 100 + 600

    health = check(spec(tmp_path), {}, just_after_midnight, "20260803")

    assert health.ok
    assert health.reports[0].status == "PENDING"


def test_a_quiet_market_warns_without_failing(tmp_path):
    """Low growth is reported but does not fail the run.

    Calling a slow Sunday an outage is how a check gets ignored — and an
    ignored check is the same as no check.
    """
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])
    size = path.stat().st_size
    state = {"s:1": {"size": size - 5, "grew_by": 100000, "day": "20260803",
                     "at": time.time() - 3600}}

    health = check(spec(tmp_path), state, time.time(), "20260803")

    assert health.ok                              # warned, not failed
    assert health.reports[0].status == "QUIET"


# --- every configured market is checked ------------------------------------

def test_each_market_is_reported_separately(tmp_path):
    """One dead market among four must not hide behind three healthy ones."""
    for market in (0, 1, 2):
        write_gz(tmp_path / f"f_{market}_20260803.jsonl.gz", [{"a": 1}])
    # market 24 deliberately absent
    noon = 86400 * 100 + 43200

    health = check(spec(tmp_path, markets=(0, 1, 2, 24)), {}, noon, "20260803")

    assert not health.ok
    statuses = {r.market: r.status for r in health.reports}
    assert statuses[24] == "MISSING"
    assert statuses[0] == "OK"
    assert len(health.reports) == 4


def test_state_is_updated_so_the_next_run_can_compare(tmp_path):
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])
    state = {}

    check(spec(tmp_path), state, time.time(), "20260803")

    assert state["s:1"]["size"] == path.stat().st_size
    assert state["s:1"]["day"] == "20260803"


# --- the buffered-writer trap ----------------------------------------------

def test_zero_growth_over_a_short_interval_is_not_stale(tmp_path):
    """Collectors flush gzip every 200 records, so a quiet minute writes nothing.

    Found live: two runs 13 seconds apart reported three healthy streams as
    STALE. A check that fails on a working system gets ignored, and an ignored
    check is the same as no check.
    """
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])
    now = time.time()
    state = {"s:1": {"size": path.stat().st_size, "grew_by": 500,
                     "day": "20260803", "at": now - 13}}

    health = check(spec(tmp_path), state, now, "20260803")

    assert health.ok
    assert health.reports[0].status == "OK"
    assert "too recent" in health.reports[0].note


def test_zero_growth_over_a_long_interval_is_still_stale(tmp_path):
    """The fix must not disarm the check it exists for."""
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])
    now = time.time()
    state = {"s:1": {"size": path.stat().st_size, "grew_by": 500,
                     "day": "20260803", "at": now - 7200}}

    health = check(spec(tmp_path), state, now, "20260803")

    assert not health.ok
    assert health.reports[0].status == "STALE"


def test_a_short_run_does_not_reset_the_comparison_window(tmp_path):
    """Frequent runs must not keep pushing the baseline forward.

    Otherwise a check every minute would never accumulate an interval long
    enough to judge growth, and a dead collector would stay green forever.
    """
    path = tmp_path / "f_1_20260803.jsonl.gz"
    write_gz(path, [{"a": 1}])
    now = time.time()
    original = {"size": path.stat().st_size, "grew_by": 500,
                "day": "20260803", "at": now - 60}
    state = {"s:1": dict(original)}

    check(spec(tmp_path), state, now, "20260803")

    assert state["s:1"]["at"] == original["at"]
