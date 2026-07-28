#!/usr/bin/env python3
"""
Runner for the two-view consensus protocol (docs/CONSENSUS_PROTOCOL.md).

Calls DeepSeek in the *falsifier* role against a hypothesis file and writes the
answer to disk SEALED, so that it does not pass through Claude's context. That
is the whole reason this script exists instead of the `cheap-llm` MCP tool: an
MCP result is returned into Claude's conversation, which destroys the protocol's
only mechanical anti-anchoring guarantee (Claude must write its own verdict
BEFORE reading DeepSeek's).

Flow:
    falsify.py new    <id>   # scaffold hypotheses/H-<id>.md
    falsify.py run    <id>   # round 1, blind: DeepSeek -> H-<id>.deepseek.sealed
    #                          ... now Claude writes H-<id>.claude.md, blind ...
    falsify.py unseal <id>   # refuses until H-<id>.claude.md exists
    falsify.py rebut  <id>   # round 2: DeepSeek attacks Claude's verdict
    falsify.py status <id>

Two rounds, and the difference between them is the point. Round 1 is blind on
both sides, which is what makes the overlap and the non-overlap of the two
verdicts mean anything. Round 2 hands DeepSeek the Claude verdict and asks what
changes now that it can see the reasoning — complementarity rather than
independence. It is only safe after `unseal`, because Claude's verdict is by
then written and hashed; run earlier, it is anchoring with extra steps.

Requires DEEPSEEK_API_KEY in the environment.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.deepseek.com/chat/completions"

REPO = Path(__file__).resolve().parent.parent
HYP_DIR = REPO / "hypotheses"

MODEL_PRO = "deepseek-v4-pro"
MODEL_FLASH = "deepseek-v4-flash"

# The project record handed to the falsifier for the provenance / conflict
# checks, when a hypothesis does not name its own. Kept explicit rather than
# globbed so a run is reproducible.
DEFAULT_EVIDENCE_FILES = [
    "CONTEXT.md",
    "docs/AUDIT_2026-07-10.md",
    "docs/BIAS_AUDIT_2026-07-07.md",
    "docs/ACCEPTANCE_SWEEP_2026-07-10.md",
    "docs/ROLLING_CALIBRATION_2026-07-10.md",
    "docs/PASS_FAIL_CRITERION.md",
    "docs/START_new_dev_chat.md",
    "docs/START_participant_mining.md",
]

EVIDENCE_HEADING = "## evidence corpus"

# Fixed falsifier system prompt. Do not soften: the role is "break it", not
# "assess it fairly". A model told to assess looks for balance; a model told to
# break looks for holes, and holes are what we need.
SYSTEM_PROMPT = """\
You are a HOSTILE FALSIFIER on a quantitative trading research project. You are \
NOT a helpful assistant, NOT a judge, and NOT a reviewer looking for balance.

Your only job: destroy the hypothesis you are given. Find the measurement, the \
missing assumption, or the alternative explanation that makes it collapse. If \
after genuine effort you cannot break it, say so plainly — but effort comes \
first, and "it looks reasonable" is a failure on your part, not a result.

Hard rules:

1. NUMBER PROVENANCE IS YOUR FIRST TASK, ALWAYS. For every number in the \
hypothesis, search the supplied PROJECT RECORD and answer: where is it written, \
and was it MEASURED (name the script, the data, the log) or merely ASSERTED in \
prose? A number you cannot trace to a measurement is a GHOST. Say GHOST \
explicitly. This project has already shipped decisions built on two ghost \
numbers, so a missing provenance is a bigger finding than a wrong conclusion.

2. The PROJECT RECORD is evidence to be searched, not an authority to agree \
with. If it states something without measurement, that is a finding, not a \
source. Never treat "the document says so" as provenance.

3. KILL-SHOT must be a MEASUREMENT, not an argument. A concrete command, \
script, or data query, with the quantity to compute and the outcome that would \
kill the hypothesis. "It suffers from overfitting" is not a kill-shot. \
"Run X over days A-B, compute Y; if Y < Z the hypothesis dies" is one. Prefer \
the cheapest measurement that discriminates.

4. STAY OUT OF JUDGMENT CALLS. Do not recommend a strategy direction, do not \
decide whether the work is worth doing, do not invent pass/fail thresholds as \
policy, do not opine on practical importance. Those belong to the human. You \
handle arithmetic, provenance, assumptions, and alternative explanations.

5. Do not invent numbers, sources, or filenames. If something is absent from \
the supplied material, the finding is "absent", never a plausible guess.

Output EXACTLY the schema requested in the task, with every field present. No \
preamble, no closing remarks, no meta-commentary.
"""

VERDICT_SCHEMA = """\
HYPOTHESIS: <restate verbatim what is being tested>

VERDICT: SUPPORTED | REFUTED | UNTESTABLE | NEEDS-DATA

GHOST-NUMBERS: [<number> - no source found, ...]   (empty list = all traced)

UNSTATED ASSUMPTIONS:
  - <assumption the conclusion silently depends on>

ALTERNATIVE EXPLANATIONS:
  - <another explanation for the same observation>

ARITHMETIC: <recomputed independently: agrees / disagrees, with the numbers>

CONFLICTS WITH RECORD: <does it contradict the project record, and where>

KILL-SHOT: <the cheapest measurement that would kill this hypothesis>
"""

TASK_PROMPT = """\
Attack the hypothesis in the HYPOTHESIS FILE below. The PROJECT RECORD that \
follows it is the corpus you search for number provenance and for conflicts — \
it is evidence, not authority.

You are given no reasoning from whoever proposed this, deliberately. Do not try \
to reconstruct or defer to it.

Respond in exactly this schema, every field present:

{schema}
=== HYPOTHESIS FILE: {hyp_name} ===
{hyp_body}
"""

# --- round 2: rebuttal -----------------------------------------------------
#
# Round 1 is blind on both sides and that is what makes it evidence of anything.
# Round 2 is the opposite by design: DeepSeek is handed Claude's verdict and told
# to attack it specifically. This is only safe AFTER `unseal`, because by then
# Claude's verdict is written and its sha256 is recorded — he cannot drift, and
# a later edit shows up in `status`. Run before that and it is just anchoring
# with extra steps.
#
# The rebuttal is deliberately NOT sealed: its whole purpose is to be read.

REBUT_SYSTEM_PROMPT = """\
You are a HOSTILE REVIEWER on a quantitative trading research project. You are \
given another analyst's written verdict on a hypothesis. Your job is to attack \
THAT VERDICT — not to summarize it, not to praise it, not to restate it.

You have already produced your own independent verdict on the same hypothesis \
(supplied below). Do not simply repeat it. The value here is what changes once \
you can see the other analyst's reasoning: what he got right that you missed, \
what he asserts without support, and what neither of you covered.

Hard rules:

1. PROVENANCE APPLIES TO HIM TOO. Every factual claim and every number in his \
verdict: is it traceable to the supplied PROJECT RECORD, or is it asserted? An \
assertion dressed as a finding is the single failure mode this project keeps \
repeating. Name each one.

2. LIST YOUR OWN CITATIONS SO THEY CAN BE CHECKED. Every file, section or item \
ID you rely on goes in CITATIONS-TO-VERIFY, with what you claim it says. In an \
earlier round a real document and a real item ID were cited for a claim that \
item does not make — right conclusion, fabricated support. Enumerating your own \
citations is what makes that mechanically checkable instead of a scavenger hunt.

3. DISAGREE ONLY WITH A REASON THAT COULD BE CHECKED. "I am not convinced" is \
not a dispute. Name the specific claim, and what would settle it.

4. STAY OUT OF JUDGMENT CALLS. Do not choose the evaluation split, do not set \
pass/fail thresholds, do not recommend a strategy direction, do not opine on \
whether the work is worth doing. Those belong to the human. In an earlier round \
a proposed measurement would have destroyed the project's only clean holdout \
set; that is what happens when this rule is ignored.

5. Do not invent numbers, sources, or filenames. Absent is a finding; a \
plausible guess is a defect.

6. If his verdict is sound and you have nothing to add, say so plainly in every \
field rather than manufacturing an objection. A fabricated disagreement costs \
more than silence.

Output EXACTLY the schema requested, with every field present. No preamble, no \
closing remarks, no meta-commentary.
"""

REBUT_SCHEMA = """\
AGREE-WITH: <what in his verdict is correct AND load-bearing — briefly, no flattery>

UNSUPPORTED-CLAIMS:
  - <claim of his that has no traceable source in the record, and what is missing>
  (empty list = everything he claims is traceable)

MISSED:
  - <what neither his verdict nor your own round-1 verdict covers>

DISPUTED:
  - <specific claim of his you think is wrong, the counter, and what would settle it>
  (empty list = no factual dispute)

CITATIONS-TO-VERIFY:
  - <file / section / item ID you relied on> -> <what you claim it says>

NEW-KILL-SHOT: <a measurement that discriminates between his verdict and yours, \
or "none - the round-1 kill-shot stands">
"""

REBUT_TASK_PROMPT = """\
Attack the VERDICT below, written by another analyst on the hypothesis that \
follows it. The PROJECT RECORD is the corpus you search for provenance — it is \
evidence, not authority.

Your own independent verdict on the same hypothesis, written before you had seen \
his, is included so that you do not merely repeat it.

Respond in exactly this schema, every field present:

{schema}
=== THE VERDICT UNDER ATTACK (by Claude) ===
{claude_body}

=== YOUR OWN ROUND-1 VERDICT (do not simply repeat it) ===
{deepseek_body}

=== HYPOTHESIS FILE: {hyp_name} ===
{hyp_body}
"""

TEMPLATE = """\
# H-{id}: {title}

Status: open
Proposed: {date}
Proposer: {proposer}

## Claim

<1-3 sentences, falsifiable. No reasoning, no justification — the falsifier
must not see why anyone believes this.>

## Numbers, each with its claimed source

| Number | Claimed source | Measured or asserted? |
|---|---|---|
| <value> | <file:line / script / "prose only"> | <measured / asserted / unknown> |

## Data / scripts available to test this

- <path, or "none">

## Evidence corpus

<Files handed to the falsifier as the project record, one `- path` per line.
Delete this section to use the default list. Include every document where a
number in this hypothesis could plausibly be recorded: a file left out cannot
be searched, and the falsifier will then report the number as a GHOST that has
no source — a ghost manufactured by the tooling.>

## Notes for the falsifier

<Only factual scope: what data exists, what the parameter means. No argument.>
"""


# Schema label -> minimum chars of content that counts as a filled field.
# VERDICT is legitimately one word; the prose fields are not. Presence of a label
# is NOT presence of a field: a truncated answer still ends with a bare
# "KILL-SHOT:", which is exactly how a real run lost its most valuable output.
SCHEMA_FIELDS = [
    ("HYPOTHESIS:", 20),
    ("VERDICT:", 6),
    ("GHOST-NUMBERS:", 2),
    ("UNSTATED ASSUMPTIONS:", 20),
    ("ALTERNATIVE EXPLANATIONS:", 20),
    ("ARITHMETIC:", 20),
    ("CONFLICTS WITH RECORD:", 20),
    ("KILL-SHOT:", 40),
]


REBUT_FIELDS = [
    ("AGREE-WITH:", 20),
    ("UNSUPPORTED-CLAIMS:", 2),
    ("MISSED:", 2),
    ("DISPUTED:", 2),
    ("CITATIONS-TO-VERIFY:", 2),
    ("NEW-KILL-SHOT:", 20),
]


def _check_schema(answer: str, fields=None):
    """Return (missing, thin) label lists. Counts only — callers must never print
    field VALUES before the blind Claude verdict exists."""
    fields = SCHEMA_FIELDS if fields is None else fields
    labels = [lab for lab, _ in fields]
    missing, thin = [], []
    for i, (label, min_chars) in enumerate(fields):
        at = answer.find(label)
        if at < 0:
            missing.append(label)
            continue
        end = len(answer)
        for nxt in labels[i + 1:]:
            nat = answer.find(nxt, at + len(label))
            if nat > 0:
                end = nat
                break
        if len(answer[at + len(label):end].strip()) < min_chars:
            thin.append(label)
    return missing, thin


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paths(hid: str):
    return {
        "hyp": HYP_DIR / f"H-{hid}.md",
        "sealed": HYP_DIR / f"H-{hid}.deepseek.sealed",
        "deepseek": HYP_DIR / f"H-{hid}.deepseek.md",
        "claude": HYP_DIR / f"H-{hid}.claude.md",
        "rebuttal": HYP_DIR / f"H-{hid}.rebuttal.md",
        "run": HYP_DIR / f"H-{hid}.run.json",
    }


def _evidence_files(hyp_body: str):
    """Return (paths, source) — the project record this hypothesis gets searched
    against, and where the list came from ("H-file" or "default").

    A hypothesis may name its own corpus under `## Evidence corpus`. That matters
    more than it looks: the falsifier can only search what it is handed, so a
    document left out of the list cannot supply provenance for a number, and the
    honest answer it then gives is "no source found" — a GHOST manufactured by
    the tooling, in a protocol whose entire purpose is catching real ones.
    """
    lines = hyp_body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(EVIDENCE_HEADING):
            start = i + 1
            break
    if start is None:
        return list(DEFAULT_EVIDENCE_FILES), "default"

    files = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        item = line.strip()
        if not item.startswith("-"):
            continue
        item = item.lstrip("-").strip().strip("`").strip()
        # Skip the scaffold's placeholder and any explicit "use the default".
        if not item or item.startswith("<") or item.lower() in ("none", "default"):
            continue
        files.append(item)
    return (files, "H-file") if files else (list(DEFAULT_EVIDENCE_FILES), "default")


def _read_evidence(files, source: str) -> str:
    """Render the evidence corpus. A path the hypothesis named explicitly must
    exist: silently substituting "[absent]" for a file someone asked for is how a
    provenance search comes back empty for the wrong reason."""
    repo = REPO.resolve()
    parts = ["\n=== PROJECT RECORD (search this; do not defer to it) ===\n"]
    for rel in files:
        f = (REPO / rel).resolve()
        if repo != f and repo not in f.parents:
            sys.exit(f"evidence path escapes the repository: {rel}")
        if not f.exists():
            if source == "H-file":
                sys.exit(
                    f"evidence file named in the hypothesis does not exist: {rel}\n"
                    f"Fix the path or drop the line — an unreadable file would make the "
                    f"falsifier report its contents as missing provenance."
                )
            parts.append(f"--- {rel} ---\n[absent from repo]\n")
            continue
        parts.append(f"--- {rel} ---\n{f.read_text(encoding='utf-8', errors='replace')}\n")
    return "\n".join(parts)


def _refuse_if_truncated(answer: str, finish_reason, max_tokens: int):
    """A verdict cut off mid-sentence is worse than no verdict: the most valuable
    field is last in both schemas, so a length stop silently eats it. On a
    reasoning tier the token budget covers hidden reasoning too, so this fires
    long before the visible text looks long."""
    if finish_reason == "length":
        sys.exit(
            f"REFUSED to record: DeepSeek stopped on finish_reason=length "
            f"({len(answer)} chars returned, max_tokens={max_tokens}).\n"
            f"On a reasoning model the token budget covers hidden reasoning too. "
            f"Re-run with a larger --max-tokens."
        )


def cmd_new(args):
    p = _paths(args.id)
    if p["hyp"].exists():
        sys.exit(f"refusing to overwrite existing {p['hyp'].relative_to(REPO)}")
    HYP_DIR.mkdir(exist_ok=True)
    p["hyp"].write_text(
        TEMPLATE.format(
            id=args.id,
            title=args.title or "<title>",
            date=_now()[:10],
            proposer=args.proposer,
        )
    )
    print(f"created {p['hyp'].relative_to(REPO)}")


def _build_prompt(hid: str, hyp_body: str, files, source: str) -> str:
    task = TASK_PROMPT.format(
        schema=VERDICT_SCHEMA, hyp_name=f"H-{hid}.md", hyp_body=hyp_body
    )
    return task + _read_evidence(files, source)


def _post_deepseek(api_key: str, model: str, user_prompt: str, temperature: float,
                   max_tokens: int, system: str = SYSTEM_PROMPT) -> dict:
    """POST to the DeepSeek chat API. Raises on failure — an API error must not be
    silently sealed as if it were a verdict."""
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        sys.exit(f"DeepSeek API HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"DeepSeek API unreachable: {e.reason}")


def cmd_run(args):
    p = _paths(args.id)
    if not p["hyp"].exists():
        sys.exit(f"no such hypothesis file: {p['hyp'].relative_to(REPO)} (run `new` first)")
    if p["sealed"].exists() and not args.force:
        sys.exit(f"{p['sealed'].name} already exists; pass --force to re-run")
    if p["claude"].exists() and not args.force:
        sys.exit(
            f"{p['claude'].name} already exists — re-running now would let a Claude verdict\n"
            f"written earlier be compared against a fresh DeepSeek draw. Pass --force if that\n"
            f"is genuinely what you want."
        )

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        sys.exit("DEEPSEEK_API_KEY not set in environment")

    if args.force:
        # Never silently clobber a previous round: a discarded draw is part of
        # the audit trail, especially when it was discarded for being truncated.
        stamp = _now().replace(":", "").replace("-", "")
        attempts = HYP_DIR / "attempts"
        for key in ("sealed", "deepseek", "run"):
            old = p[key]
            if old.exists():
                attempts.mkdir(parents=True, exist_ok=True)
                dest = attempts / f"{old.name}.{stamp}"
                old.rename(dest)
                print(f"archived {dest.relative_to(REPO)}")

    hyp_body = p["hyp"].read_text(encoding="utf-8")
    evidence, evidence_source = _evidence_files(hyp_body)
    user_prompt = _build_prompt(args.id, hyp_body, evidence, evidence_source)
    model = MODEL_FLASH if args.flash else MODEL_PRO

    resp = _post_deepseek(api_key, model, user_prompt, args.temperature, args.max_tokens)
    choice = resp["choices"][0]
    answer = choice["message"]["content"]
    finish_reason = choice.get("finish_reason")
    _refuse_if_truncated(answer, finish_reason, args.max_tokens)

    HYP_DIR.mkdir(exist_ok=True)
    p["sealed"].write_text(base64.b64encode(answer.encode("utf-8")).decode("ascii") + "\n")

    usage = resp.get("usage") or {}
    record = {
        "hypothesis_id": args.id,
        "ran_at": _now(),
        "model": model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "system_prompt_sha256": _sha(SYSTEM_PROMPT),
        "user_prompt_sha256": _sha(user_prompt),
        "user_prompt_chars": len(user_prompt),
        "hypothesis_file_sha256": _sha(hyp_body),
        "evidence_files": evidence,
        "evidence_source": evidence_source,
        "response_sha256": _sha(answer),
        "response_chars": len(answer),
        "finish_reason": finish_reason,
        "usage": usage,
    }
    p["run"].write_text(json.dumps(record, indent=2) + "\n")

    # Structural check only. Deliberately prints no field VALUES: the verdict and
    # the ghost list must not leak into the caller's context before the blind
    # Claude verdict is written.
    missing, thin = _check_schema(answer)

    print(f"sealed  : {p['sealed'].relative_to(REPO)}")
    print(f"run log : {p['run'].relative_to(REPO)}")
    print(f"evidence: {len(evidence)} files from {evidence_source}")
    print(f"model={model} temp={args.temperature} prompt_chars={len(user_prompt)} "
          f"response_chars={len(answer)} finish_reason={finish_reason}")
    print(f"schema fields with content: {len(SCHEMA_FIELDS) - len(missing) - len(thin)}"
          f"/{len(SCHEMA_FIELDS)}")
    if missing:
        print(f"MISSING FIELDS: {', '.join(missing)}")
    if thin:
        print(f"EMPTY/TRUNCATED FIELDS: {', '.join(thin)}")
    print(f"\nNEXT: write {p['claude'].name} (your own verdict, same schema) WITHOUT")
    print(f"reading the sealed file, then: falsify.py unseal {args.id}")


def cmd_unseal(args):
    p = _paths(args.id)
    if not p["sealed"].exists():
        sys.exit(f"nothing sealed for H-{args.id}")
    if not p["claude"].exists():
        sys.exit(
            f"REFUSED: {p['claude'].name} does not exist.\n"
            f"The protocol requires the Claude verdict to be written before DeepSeek's is read.\n"
            f"Write it first (same schema), then unseal."
        )
    claude_body = p["claude"].read_text(encoding="utf-8")
    if len(claude_body.strip()) < 200 or "VERDICT:" not in claude_body:
        sys.exit(
            f"REFUSED: {p['claude'].name} does not look like a real verdict "
            f"(needs a VERDICT: field and some substance). A placeholder file "
            f"defeats the point of the ordering."
        )

    answer = base64.b64decode(p["sealed"].read_text().strip()).decode("utf-8")
    p["deepseek"].write_text(answer if answer.endswith("\n") else answer + "\n")

    # Record what the Claude verdict looked like at unseal time, so a later edit
    # to it (after seeing DeepSeek's) is detectable rather than invisible.
    if p["run"].exists():
        record = json.loads(p["run"].read_text())
        record["unsealed_at"] = _now()
        record["claude_verdict_sha256_at_unseal"] = _sha(claude_body)
        record["claude_verdict_chars_at_unseal"] = len(claude_body)
        p["run"].write_text(json.dumps(record, indent=2) + "\n")

    print(f"unsealed -> {p['deepseek'].relative_to(REPO)} ({len(answer)} chars)")


def cmd_rebut(args):
    """Round 2: hand DeepSeek the Claude verdict and have it attack that.

    Requires round 1 to be complete — both verdicts on disk. Refusing otherwise
    is not bookkeeping: if the rebuttal could be run before the blind pair
    exists, the cheap path would be to skip the independent round entirely and
    let DeepSeek comment on Claude's reasoning, which is the anchoring this whole
    protocol is built to prevent.
    """
    p = _paths(args.id)
    if not p["hyp"].exists():
        sys.exit(f"no such hypothesis file: {p['hyp'].relative_to(REPO)}")
    if not p["claude"].exists():
        sys.exit(f"REFUSED: {p['claude'].name} does not exist — nothing to rebut.")
    if not p["deepseek"].exists():
        sys.exit(
            f"REFUSED: {p['deepseek'].name} does not exist.\n"
            f"Round 2 attacks Claude's verdict, which is only meaningful after both\n"
            f"sides have committed a verdict blind. Run `unseal {args.id}` first."
        )
    if p["rebuttal"].exists() and not args.force:
        sys.exit(f"{p['rebuttal'].name} already exists; pass --force to re-run")

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        sys.exit("DEEPSEEK_API_KEY not set in environment")

    hyp_body = p["hyp"].read_text(encoding="utf-8")
    claude_body = p["claude"].read_text(encoding="utf-8")
    deepseek_body = p["deepseek"].read_text(encoding="utf-8")
    evidence, evidence_source = _evidence_files(hyp_body)

    user_prompt = REBUT_TASK_PROMPT.format(
        schema=REBUT_SCHEMA,
        claude_body=claude_body,
        deepseek_body=deepseek_body,
        hyp_name=f"H-{args.id}.md",
        hyp_body=hyp_body,
    ) + _read_evidence(evidence, evidence_source)

    model = MODEL_FLASH if args.flash else MODEL_PRO
    resp = _post_deepseek(
        api_key, model, user_prompt, args.temperature, args.max_tokens,
        system=REBUT_SYSTEM_PROMPT,
    )
    choice = resp["choices"][0]
    answer = choice["message"]["content"]
    finish_reason = choice.get("finish_reason")
    _refuse_if_truncated(answer, finish_reason, args.max_tokens)

    if args.force and p["rebuttal"].exists():
        stamp = _now().replace(":", "").replace("-", "")
        attempts = HYP_DIR / "attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        dest = attempts / f"{p['rebuttal'].name}.{stamp}"
        p["rebuttal"].rename(dest)
        print(f"archived {dest.relative_to(REPO)}")

    # Not sealed, on purpose: Claude's verdict is already written and hashed, so
    # there is nothing left to protect it from. The rebuttal exists to be read.
    p["rebuttal"].write_text(answer if answer.endswith("\n") else answer + "\n")

    if p["run"].exists():
        record = json.loads(p["run"].read_text())
        record["rebuttal"] = {
            "ran_at": _now(),
            "model": model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "system_prompt_sha256": _sha(REBUT_SYSTEM_PROMPT),
            "user_prompt_chars": len(user_prompt),
            # Which version of the verdict was actually attacked. Without this, a
            # rebuttal of an edited verdict is indistinguishable from one of the
            # original.
            "claude_verdict_sha256_at_rebut": _sha(claude_body),
            "response_sha256": _sha(answer),
            "response_chars": len(answer),
            "finish_reason": finish_reason,
            "usage": resp.get("usage") or {},
        }
        p["run"].write_text(json.dumps(record, indent=2) + "\n")

    missing, thin = _check_schema(answer, REBUT_FIELDS)
    print(f"rebuttal: {p['rebuttal'].relative_to(REPO)} ({len(answer)} chars)")
    print(f"evidence: {len(evidence)} files from {evidence_source}")
    print(f"model={model} temp={args.temperature} prompt_chars={len(user_prompt)} "
          f"finish_reason={finish_reason}")
    print(f"schema fields with content: {len(REBUT_FIELDS) - len(missing) - len(thin)}"
          f"/{len(REBUT_FIELDS)}")
    if missing:
        print(f"MISSING FIELDS: {', '.join(missing)}")
    if thin:
        print(f"EMPTY/TRUNCATED FIELDS: {', '.join(thin)}")
    print("\nNEXT: open every entry in CITATIONS-TO-VERIFY before using any of it.")


def cmd_check(args):
    """Validate the sealed verdict's structure without spending an API call and
    without revealing any field values."""
    p = _paths(args.id)
    fields = SCHEMA_FIELDS
    if args.rebuttal:
        src, fields = p["rebuttal"], REBUT_FIELDS
    else:
        src = p["deepseek"] if p["deepseek"].exists() else p["sealed"]
    if not src.exists():
        sys.exit(f"nothing to check for H-{args.id}")
    answer = (
        src.read_text(encoding="utf-8")
        if src != p["sealed"]
        else base64.b64decode(src.read_text().strip()).decode("utf-8")
    )
    missing, thin = _check_schema(answer, fields)
    print(f"source: {src.name} ({len(answer)} chars)")
    print(f"schema fields with content: {len(fields) - len(missing) - len(thin)}"
          f"/{len(fields)}")
    if missing:
        print(f"MISSING FIELDS: {', '.join(missing)}")
    if thin:
        print(f"EMPTY/TRUNCATED FIELDS: {', '.join(thin)}")
    if not missing and not thin:
        print("structure ok")


def cmd_status(args):
    p = _paths(args.id)
    order = ["hyp", "sealed", "claude", "deepseek", "rebuttal", "run"]
    for k in order:
        f = p[k]
        mark = "ok  " if f.exists() else "--  "
        stamp = ""
        if f.exists():
            stamp = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%SZ"
            )
        print(f"{mark}{f.name:34s} {stamp}")
    if p["run"].exists():
        rec = json.loads(p["run"].read_text())
        if "evidence_source" in rec:
            print(f"\nevidence corpus: {len(rec['evidence_files'])} files "
                  f"from {rec['evidence_source']}")
        if "claude_verdict_sha256_at_unseal" in rec and p["claude"].exists():
            now_sha = _sha(p["claude"].read_text(encoding="utf-8"))
            same = now_sha == rec["claude_verdict_sha256_at_unseal"]
            print(f"claude verdict unchanged since unseal: {'yes' if same else 'NO — EDITED'}")
            reb = rec.get("rebuttal") or {}
            if "claude_verdict_sha256_at_rebut" in reb:
                # Round 2 read the verdict; if it changed afterwards, the
                # rebuttal on disk is answering something no longer written.
                fresh = now_sha == reb["claude_verdict_sha256_at_rebut"]
                print(f"rebuttal attacks the current verdict: "
                      f"{'yes' if fresh else 'NO — verdict edited after rebut'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("new", help="scaffold a hypothesis file")
    a.add_argument("id")
    a.add_argument("--title", default="")
    a.add_argument("--proposer", default="Claude")
    a.set_defaults(func=cmd_new)

    a = sub.add_parser("run", help="run the DeepSeek falsifier, sealed")
    a.add_argument("id")
    a.add_argument("--flash", action="store_true", help=f"use {MODEL_FLASH} instead of {MODEL_PRO}")
    a.add_argument("--temperature", type=float, default=0.0)
    # Generous by default: on the reasoning tier this budget covers hidden
    # reasoning as well as the answer, and 4000 truncated a real run mid-KILL-SHOT.
    a.add_argument("--max-tokens", type=int, default=16000)
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_run)

    a = sub.add_parser("unseal", help="reveal DeepSeek's verdict (requires Claude's first)")
    a.add_argument("id")
    a.set_defaults(func=cmd_unseal)

    a = sub.add_parser("rebut", help="round 2: DeepSeek attacks Claude's verdict (after unseal)")
    a.add_argument("id")
    a.add_argument("--flash", action="store_true", help=f"use {MODEL_FLASH} instead of {MODEL_PRO}")
    a.add_argument("--temperature", type=float, default=0.0)
    a.add_argument("--max-tokens", type=int, default=16000)
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_rebut)

    a = sub.add_parser("check", help="validate verdict structure, no API call, no value leak")
    a.add_argument("id")
    a.add_argument("--rebuttal", action="store_true", help="check the round-2 rebuttal instead")
    a.set_defaults(func=cmd_check)

    a = sub.add_parser("status", help="show protocol state for a hypothesis")
    a.add_argument("id")
    a.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
