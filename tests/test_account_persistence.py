"""Tests for account id persistence checking.

Expected values are derived from the rule, on paper, not read off the
implementation. The rule being tested:

  An account id is persistent if the venue, asked again, returns the same
  `account_type` and `l1_address` it returned when the snapshot was taken.
  A changed identity field is a failure. A changed `status` is not. An
  unanswered request is neither — it is our failure and must never be
  counted as agreement.

The tests that matter most are the ones that must go red on trivial
agreement: a checker that returns PASS unconditionally, or that counts
timeouts as matches, would pass a naive suite while hiding exactly the
outcome this tool exists to detect.
"""

from __future__ import annotations

from src.analysis.account_persistence import Comparison, compare, verdict


def entry(account_type: int = 0, l1: str = "0xaaa", status: int = 1) -> dict:
    return {"account_type": account_type, "l1_address": l1, "status": status}


# --- agreement -------------------------------------------------------------

def test_identical_readings_agree():
    cache = {"1": entry(), "2": entry(account_type=1, l1="0xbbb")}
    fresh = {1: entry(), 2: entry(account_type=1, l1="0xbbb")}

    result = compare(cache, fresh, [])

    # Both accounts match on both identity fields: 2 agreed, nothing else.
    assert result.agreed == 2
    assert result.checked == 2
    assert result.identity_changes == {}
    assert verdict(result)[0] == 0


# --- the failures the tool exists to catch ---------------------------------

def test_changed_l1_address_is_an_identity_failure():
    """The id now answers to a different owner — fatal for aggregation."""
    cache = {"1": entry(l1="0xaaa")}
    fresh = {1: entry(l1="0xZZZ")}

    result = compare(cache, fresh, [])

    assert result.agreed == 0
    assert result.identity_changes == {1: {"l1_address": ("0xaaa", "0xZZZ")}}
    code, line = verdict(result)
    assert code == 1
    assert "identity" in line


def test_changed_account_type_is_an_identity_failure():
    """build_account_index caches types on the claim that they never change."""
    cache = {"1": entry(account_type=0)}
    fresh = {1: entry(account_type=1)}

    result = compare(cache, fresh, [])

    assert result.identity_changes == {1: {"account_type": (0, 1)}}
    assert verdict(result)[0] == 1


def test_one_failure_among_many_still_fails():
    """A single migrated id invalidates ranking; a majority-passes rule would not catch it."""
    cache = {str(i): entry() for i in range(100)}
    fresh = {i: entry() for i in range(100)}
    fresh[42] = entry(l1="0xdifferent")

    result = compare(cache, fresh, [])

    assert result.agreed == 99
    assert list(result.identity_changes) == [42]
    assert verdict(result)[0] == 1


def test_vanished_account_fails_and_is_not_agreement():
    """The venue answering "no such account" about an id it once described."""
    cache = {"1": entry(), "2": entry()}
    fresh = {1: entry(), 2: None}

    result = compare(cache, fresh, [])

    assert result.vanished == [2]
    assert result.agreed == 1
    code, line = verdict(result)
    assert code == 1
    assert "vanished" in line


# --- our failures must not masquerade as evidence --------------------------

def test_unreachable_is_never_counted_as_agreement():
    """A timeout is our failure. Counting it either way fakes a measurement."""
    cache = {"1": entry(), "2": entry()}
    fresh = {1: entry()}          # account 2 never answered
    unreachable = [2]

    result = compare(cache, fresh, unreachable)

    assert result.agreed == 1              # not 2
    assert result.checked == 1             # account 2 contributed no evidence
    assert result.unreachable == [2]
    assert result.vanished == []           # emphatically not "vanished"


def test_all_unreachable_does_not_pass():
    """Network down must not read as "everything is fine"."""
    cache = {"1": entry(), "2": entry()}

    result = compare(cache, fresh={}, unreachable=[1, 2])

    assert result.checked == 0
    code, line = verdict(result)
    assert code == 1
    assert "nothing was compared" in line


def test_account_absent_from_cache_is_not_evidence():
    """An id we never snapshotted cannot testify for or against persistence."""
    cache = {"1": entry()}
    fresh = {1: entry(), 999: entry()}     # 999 was never cached

    result = compare(cache, fresh, [])

    assert result.agreed == 1              # not 2
    assert result.checked == 1


# --- status is expected to move --------------------------------------------

def test_status_change_is_not_a_persistence_failure():
    """Accounts open and close; that is not an id changing meaning."""
    cache = {"1": entry(status=1)}
    fresh = {1: entry(status=0)}

    result = compare(cache, fresh, [])

    assert result.identity_changes == {}
    assert result.volatile_changes == {1: {"status": (1, 0)}}
    assert result.agreed == 0              # it did change, just not fatally
    assert verdict(result)[0] == 0         # ... and the run still passes


def test_identity_change_outranks_status_change():
    """When both moved, the fatal one is what gets reported."""
    cache = {"1": entry(l1="0xaaa", status=1)}
    fresh = {1: entry(l1="0xbbb", status=0)}

    result = compare(cache, fresh, [])

    assert 1 in result.identity_changes
    assert result.volatile_changes == {}
    assert verdict(result)[0] == 1


# --- the empty case --------------------------------------------------------

def test_empty_comparison_fails_rather_than_passing_vacuously():
    result = compare({}, {}, [])

    assert result.checked == 0
    assert verdict(result)[0] == 1
