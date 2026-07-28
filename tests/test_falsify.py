"""Tests for the consensus-protocol runner (scripts/falsify.py).

The point of these is the *ordering guarantee*: `unseal` must refuse to reveal
DeepSeek's verdict until Claude's blind verdict exists. That refusal is the whole
anti-anchoring mechanism of docs/CONSENSUS_PROTOCOL.md — if it silently breaks,
the protocol keeps producing files that look valid and prove nothing.

Also pins the truncation bug found during the H-001 shakedown: a cut-off answer
still ends with a bare "KILL-SHOT:", so counting labels reported a full schema
while the most valuable field was empty.
"""

import argparse
import base64
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "falsify.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("falsify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fal(tmp_path, monkeypatch):
    """The module with its hypotheses dir redirected into tmp_path."""
    mod = _load_module()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "HYP_DIR", tmp_path / "hypotheses")
    (tmp_path / "hypotheses").mkdir()
    return mod


FULL_ANSWER = """\
HYPOTHESIS: some claim being tested, stated at length

VERDICT: REFUTED

GHOST-NUMBERS:
  - 42 — no source found

UNSTATED ASSUMPTIONS:
  - that the thing was measured at all

ALTERNATIVE EXPLANATIONS:
  - the sample simply shrank

ARITHMETIC: recomputed, 2*2+1 = 5, agrees

CONFLICTS WITH RECORD: yes, CONTEXT.md says it was never swept

KILL-SHOT: grep the repo for a results table; if absent the premise is false
"""

# Exactly how the first real run failed: stops inside the last field.
TRUNCATED_ANSWER = FULL_ANSWER[: FULL_ANSWER.index("KILL-SHOT:") + len("KILL-SHOT: Sweep `swing")]


def _seal(fal, hid, answer):
    p = fal._paths(hid)
    p["sealed"].write_text(base64.b64encode(answer.encode()).decode() + "\n")
    return p


# --- the ordering guarantee ------------------------------------------------

def test_unseal_refuses_when_claude_verdict_absent(fal, capsys):
    _seal(fal, "010", FULL_ANSWER)
    with pytest.raises(SystemExit) as e:
        fal.cmd_unseal(argparse.Namespace(id="010"))
    assert "REFUSED" in str(e.value)
    assert not fal._paths("010")["deepseek"].exists()


def test_unseal_refuses_a_stub_claude_verdict(fal):
    p = _seal(fal, "011", FULL_ANSWER)
    p["claude"].write_text("VERDICT: REFUTED\n")  # has the field, but no substance
    with pytest.raises(SystemExit) as e:
        fal.cmd_unseal(argparse.Namespace(id="011"))
    assert "REFUSED" in str(e.value)
    assert not p["deepseek"].exists()


def test_unseal_refuses_verdict_without_verdict_field(fal):
    p = _seal(fal, "012", FULL_ANSWER)
    p["claude"].write_text("I think this hypothesis is wrong. " * 20)  # long, but no VERDICT:
    with pytest.raises(SystemExit):
        fal.cmd_unseal(argparse.Namespace(id="012"))
    assert not p["deepseek"].exists()


def test_unseal_succeeds_with_a_real_claude_verdict(fal):
    p = _seal(fal, "013", FULL_ANSWER)
    p["claude"].write_text("VERDICT: REFUTED\n\n" + "reasoning about the claim. " * 20)
    p["run"].write_text(json.dumps({"hypothesis_id": "013"}) + "\n")
    fal.cmd_unseal(argparse.Namespace(id="013"))
    assert p["deepseek"].read_text() == FULL_ANSWER


def test_unseal_records_claude_verdict_hash_for_tamper_detection(fal):
    p = _seal(fal, "014", FULL_ANSWER)
    body = "VERDICT: REFUTED\n\n" + "reasoning about the claim. " * 20
    p["claude"].write_text(body)
    p["run"].write_text(json.dumps({"hypothesis_id": "014"}) + "\n")
    fal.cmd_unseal(argparse.Namespace(id="014"))
    rec = json.loads(p["run"].read_text())
    assert rec["claude_verdict_sha256_at_unseal"] == fal._sha(body)
    assert "unsealed_at" in rec


def test_unseal_with_nothing_sealed_exits(fal):
    with pytest.raises(SystemExit):
        fal.cmd_unseal(argparse.Namespace(id="015"))


# --- schema validation: the truncation bug --------------------------------

def test_check_schema_accepts_a_complete_answer(fal):
    missing, thin = fal._check_schema(FULL_ANSWER)
    assert missing == []
    assert thin == []


def test_check_schema_catches_truncated_killshot(fal):
    """The bug: label present, content gone. Counting labels said 8/8."""
    assert "KILL-SHOT:" in TRUNCATED_ANSWER  # label really is there
    missing, thin = fal._check_schema(TRUNCATED_ANSWER)
    assert missing == []
    assert "KILL-SHOT:" in thin


def test_check_schema_allows_a_one_word_verdict(fal):
    """VERDICT is legitimately short — it must not be flagged as thin."""
    missing, thin = fal._check_schema(FULL_ANSWER)
    assert "VERDICT:" not in thin


def test_check_schema_allows_an_empty_ghost_list(fal):
    """An empty GHOST-NUMBERS list is a real result: all numbers traced."""
    answer = FULL_ANSWER.replace("GHOST-NUMBERS:\n  - 42 — no source found", "GHOST-NUMBERS: []")
    missing, thin = fal._check_schema(answer)
    assert thin == []


def test_check_schema_reports_missing_fields(fal):
    answer = FULL_ANSWER.replace("ARITHMETIC: recomputed, 2*2+1 = 5, agrees", "")
    missing, _ = fal._check_schema(answer)
    assert "ARITHMETIC:" in missing


# --- scaffolding ----------------------------------------------------------

def test_new_refuses_to_overwrite_an_existing_hypothesis(fal):
    args = argparse.Namespace(id="020", title="t", proposer="Claude")
    fal.cmd_new(args)
    original = fal._paths("020")["hyp"].read_text()
    with pytest.raises(SystemExit) as e:
        fal.cmd_new(args)
    assert "refusing to overwrite" in str(e.value)
    assert fal._paths("020")["hyp"].read_text() == original


def test_run_requires_an_existing_hypothesis_file(fal):
    with pytest.raises(SystemExit) as e:
        fal.cmd_run(argparse.Namespace(id="021", force=False))
    assert "no such hypothesis file" in str(e.value)


# --- the evidence corpus ---------------------------------------------------
#
# The falsifier can only search what it is handed. A document left out cannot
# supply provenance, so the honest answer becomes "no source found" — a ghost
# manufactured by the tooling, in a protocol built to catch real ones.

HYP_WITH_EVIDENCE = """\
# H-030: something

## Claim

A claim.

## Evidence corpus

- CONTEXT.md
- `docs/ONE.md`

## Notes for the falsifier

Not a path: - this bullet lives in another section.
"""


def test_evidence_list_comes_from_the_hypothesis_when_present(fal):
    files, source = fal._evidence_files(HYP_WITH_EVIDENCE)
    assert files == ["CONTEXT.md", "docs/ONE.md"]  # backticks stripped
    assert source == "H-file"


def test_evidence_section_ends_at_the_next_heading(fal):
    files, _ = fal._evidence_files(HYP_WITH_EVIDENCE)
    assert not any("bullet" in f for f in files)


def test_evidence_falls_back_to_default_without_the_section(fal):
    files, source = fal._evidence_files("# H-031\n\n## Claim\n\nA claim.\n")
    assert files == fal.DEFAULT_EVIDENCE_FILES
    assert source == "default"


def test_evidence_falls_back_when_only_the_scaffold_placeholder_is_present(fal):
    body = "## Evidence corpus\n\n- <Files handed to the falsifier, one per line.>\n"
    files, source = fal._evidence_files(body)
    assert source == "default"


def test_explicitly_named_missing_evidence_file_is_fatal(fal):
    """The failure this guards: a silent '[absent from repo]' substitution makes a
    provenance search come back empty for the wrong reason."""
    with pytest.raises(SystemExit) as e:
        fal._read_evidence(["docs/DOES_NOT_EXIST.md"], "H-file")
    assert "does not exist" in str(e.value)


def test_missing_default_evidence_file_is_tolerated(fal):
    out = fal._read_evidence(["docs/DOES_NOT_EXIST.md"], "default")
    assert "[absent from repo]" in out


def test_evidence_path_escaping_the_repo_is_rejected(fal):
    with pytest.raises(SystemExit) as e:
        fal._read_evidence(["../../etc/passwd"], "H-file")
    assert "escapes the repository" in str(e.value)


def test_evidence_contents_are_rendered_with_their_path(fal, tmp_path):
    (tmp_path / "CONTEXT.md").write_text("the record says X")
    out = fal._read_evidence(["CONTEXT.md"], "H-file")
    assert "--- CONTEXT.md ---" in out
    assert "the record says X" in out


# --- round 2: the rebuttal -------------------------------------------------

REBUTTAL = """\
AGREE-WITH: his provenance search is correct and load-bearing

UNSUPPORTED-CLAIMS:
  - "the three sites descend from one observation" - plausible, but not traced

MISSED:
  - neither verdict checks the interaction with trailing

DISPUTED:
  - none

CITATIONS-TO-VERIFY:
  - docs/AUDIT_2026-07-10.md item S2 -> concerns acceptance_bars, not this knob

NEW-KILL-SHOT: none - the round-1 kill-shot stands
"""


def _round1_complete(fal, hid):
    p = fal._paths(hid)
    p["hyp"].write_text(HYP_WITH_EVIDENCE)
    p["claude"].write_text("VERDICT: REFUTED\n\n" + "reasoning. " * 40)
    p["deepseek"].write_text(FULL_ANSWER)
    return p


def _rebut_args(hid, **kw):
    base = dict(id=hid, flash=False, temperature=0.0, max_tokens=16000, force=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_rebut_refuses_before_the_claude_verdict_exists(fal):
    p = fal._paths("040")
    p["hyp"].write_text(HYP_WITH_EVIDENCE)
    p["deepseek"].write_text(FULL_ANSWER)
    with pytest.raises(SystemExit) as e:
        fal.cmd_rebut(_rebut_args("040"))
    assert "REFUSED" in str(e.value)
    assert not p["rebuttal"].exists()


def test_rebut_refuses_before_unseal(fal):
    """Round 2 is only meaningful once both sides committed a verdict blind.
    Without this, the cheap path is to skip the independent round entirely."""
    p = fal._paths("041")
    p["hyp"].write_text(HYP_WITH_EVIDENCE)
    p["claude"].write_text("VERDICT: REFUTED\n\n" + "reasoning. " * 40)
    with pytest.raises(SystemExit) as e:
        fal.cmd_rebut(_rebut_args("041"))
    assert "unseal" in str(e.value)
    assert not p["rebuttal"].exists()


def test_rebut_writes_the_rebuttal_and_records_which_verdict_it_attacked(fal, monkeypatch):
    p = _round1_complete(fal, "042")
    p["run"].write_text(json.dumps({"hypothesis_id": "042"}) + "\n")
    (fal.REPO / "CONTEXT.md").write_text("the record")
    (fal.REPO / "docs").mkdir()
    (fal.REPO / "docs" / "ONE.md").write_text("one")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    seen = {}

    def fake_post(api_key, model, user_prompt, temperature, max_tokens, system=None):
        seen["prompt"] = user_prompt
        seen["system"] = system
        return {"choices": [{"message": {"content": REBUTTAL}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr(fal, "_post_deepseek", fake_post)
    fal.cmd_rebut(_rebut_args("042"))

    assert p["rebuttal"].read_text() == REBUTTAL
    rec = json.loads(p["run"].read_text())
    assert rec["rebuttal"]["claude_verdict_sha256_at_rebut"] == fal._sha(p["claude"].read_text())
    # Round 2 must actually see the verdict it is told to attack, plus its own
    # round-1 answer so it does not merely repeat it.
    assert "VERDICT: REFUTED" in seen["prompt"]
    assert FULL_ANSWER.strip() in seen["prompt"]
    assert seen["system"] == fal.REBUT_SYSTEM_PROMPT


def test_rebut_refuses_to_record_a_truncated_answer(fal, monkeypatch):
    p = _round1_complete(fal, "043")
    (fal.REPO / "CONTEXT.md").write_text("the record")
    (fal.REPO / "docs").mkdir()
    (fal.REPO / "docs" / "ONE.md").write_text("one")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(
        fal, "_post_deepseek",
        lambda *a, **k: {"choices": [{"message": {"content": REBUTTAL[:80]},
                                      "finish_reason": "length"}]},
    )
    with pytest.raises(SystemExit) as e:
        fal.cmd_rebut(_rebut_args("043"))
    assert "REFUSED" in str(e.value)
    assert not p["rebuttal"].exists()


def test_rebut_will_not_silently_overwrite_a_previous_round(fal):
    p = _round1_complete(fal, "044")
    p["rebuttal"].write_text(REBUTTAL)
    with pytest.raises(SystemExit) as e:
        fal.cmd_rebut(_rebut_args("044"))
    assert "--force" in str(e.value)


def test_rebuttal_schema_is_checked_against_its_own_fields(fal):
    missing, thin = fal._check_schema(REBUTTAL, fal.REBUT_FIELDS)
    assert missing == []
    assert thin == []


def test_rebuttal_missing_citations_field_is_caught(fal):
    body = REBUTTAL.replace("CITATIONS-TO-VERIFY:", "CITATIONS:")
    missing, _ = fal._check_schema(body, fal.REBUT_FIELDS)
    assert "CITATIONS-TO-VERIFY:" in missing


def test_verdict_schema_check_is_unchanged_by_the_new_parameter(fal):
    """Regression: the default must stay the round-1 schema."""
    assert fal._check_schema(FULL_ANSWER) == fal._check_schema(FULL_ANSWER, fal.SCHEMA_FIELDS)
