# DeepSeek task file

Read this file before starting. It is overwritten with a new task each
time — only the CURRENT task below is active. Do not carry over
assumptions from a previous task unless restated here.

Rules that apply to every task in this file:
- Work only in the branch named in "Branch" below. Never commit to `main`.
- Never touch `CONTEXT.md` or `README.md` — that's handled separately
  after review.
- Only touch the files explicitly listed in "Files in scope".
- Run the full `pytest -q` suite before your final commit and put the
  resulting pass count in the commit message.
- Every expected value in a new/changed test must be derived BY HAND from
  the rule being tested, on paper — never read back from what the code
  produces. This is a hard project convention (see CONTEXT.md
  Anti-patterns: "Tests that verify the code against itself").
- If a find-replace/patch script reports 0 matches on an edit, that is a
  failure — stop and fix it, do not continue and do not report success.
  Prefer full-file overwrites over multi-site patch scripts when an edit
  touches more than ~3-4 sites in one file.
- **Push your branch when done** (`git push -u origin <branch>` if it's
  not tracked yet, else `git push`). A finished commit sitting
  unpushed is not a finished task — this has bitten the project twice
  already (see CONTEXT.md anti-patterns).
- **Write up findings, but don't treat your own conclusion as final.**
  Ivan and Claude review every DeepSeek result independently (fresh
  clone, re-run, hand-check) before acting on it — same standard
  applied to Claude's own work. Report the raw numbers plainly and give
  your own preliminary read, but flag it as preliminary, not settled.
- When done, leave the final `git log --oneline -3` of the branch as the
  last line of your output — Claude will ask Ivan to paste exactly that.

---

## CURRENT TASK: two parts, run in order

**Branch**: create `feature/bias-audit-no-reversal` off current `main`.

**Background** (context only, so the "why" is clear — don't re-derive
this, it's already established): a prior session found that reversal
exits are the strategy's main loss driver, and that the swing points
triggering those reversals are usually very recently formed (0-4 bars
before entry in a hand-checked sample) — i.e. the exit rule is mostly
reacting to short-term noise, not established structure. Claude
separately re-read the Beggs primary source on trend determination
(not available to you — it's Russian-translated PDFs in Claude's
project knowledge, not in this repo) and confirmed by direct code read
that `compute_bias()` in `strategy.py` is a pure, stateless function
with **zero acceptance/hold-time filter** — it recomputes fresh every
bar from EMA values and last close, with no memory of prior state. Beggs
explicitly warns against exactly this failure mode for trend-reading
(a break that doesn't hold shouldn't flip your read), the same principle
already used to fix the reversal-exit rule. This task measures whether
that gap actually matters on real data.

### Part A — bias-regime empirical audit (measurement only, no code
changes to strategy.py)

**Files in scope**: new file `src/backtest/bias_audit.py`, new file
`tests/test_bias_audit.py`.

Build a diagnostic tool (same non-invasive subclass pattern as
`src/backtest/export_sample.py` from a prior task — read that file
first for the established style) that runs the standard pipeline
(`Replay` + `FillEngine` + `WFStrategy(exit_mode="swing", trailing=False)`,
same as every other real-data run this project has done) and reports,
**per file/day**, both across ALL 8 real BTC days (the 4 calibration
days June 29 - July 2 AND the 4 out-of-sample days July 3-6 — list both
sets separately in the output, don't merge them):

1. **Bias regime-length distribution**: every time `self.bias()`
   changes value (including transitions to/from `None`), that's a new
   regime. Record each regime's length in bars. Report median, min, max,
   and a simple histogram bucketed as `<5 bars`, `5-20 bars`, `>20 bars`
   (count in each bucket), separately for `bull`, `bear`, and `None`
   regimes.
2. **Bias age at entry**: for every trade in `strategy.trades`, find
   how many bars the CURRENT bias regime had existed at the moment of
   that trade's entry (same "age" concept already used for the swing
   analysis — bars since the regime last changed value, looking
   backward from the entry bar). Report the distribution (median, min,
   max) across all trades, pooled across all 8 days.

Output as plain text to stdout (this is a one-off diagnostic run, not
part of the CLI suite — a simple script is fine, doesn't need
`argparse` polish, but DOES need to actually run against real files
under `data/ticks/` — hardcode the 8 filenames or accept them as
positional args, your choice, just make it runnable and show the exact
command you used).

**Tests**: hand-derive a synthetic bias sequence (a short list of bias
values across bars, worked out by hand, e.g. `[None, None, "bear",
"bear", "bear", "bull", "bull", None]`) and hand-verify the regime
detection produces the correct segments and lengths BEFORE writing the
detection code, the same way `SWING_FORMATION_BARS` was hand-derived
in `test_strategy.py`. Test the "age at entry" calculation the same way
`filter_trades`/bar-index tests were done for `export_sample.py`
(construct a case with a known entry_ts landing partway through a known
regime, assert the correct age).

### Part B — no-reversal-exit variant (run only after Part A completes;
if Part A's numbers already make the outcome obvious to you, still
run Part B — it answers a different question and isn't gated on Part A's
result, just sequenced after it)

**Files in scope**: new file `src/backtest/no_reversal_variant.py`
(or add to `bias_audit.py` if that reads more naturally — your call,
just keep it out of `strategy.py`), no test file strictly required for
this part since it's a thin override with no new logic of its own, but
add one or two tests if you judge it adds real confidence, not just to
pad the count.

Design (do not modify `strategy.py`):

```python
from src.backtest.strategy import WFStrategy

class NoReversalStrategy(WFStrategy):
    """Reversal exit permanently disabled — take-profit and the static
    0-line stop are the only exits. Everything else (entries, costs,
    trailing if enabled) is unchanged from WFStrategy."""

    def _check_reversal_bar_mode(self, bar) -> bool:
        return False

    def _check_reversal_swing_mode(self, bar) -> bool:
        return False
```

Run this variant with `exit_mode="swing", trailing=False` (matching the
current best-diagnosed baseline, so the comparison is apples-to-apples
against runs already on record) via `run_backtest.py`'s existing
plumbing (`Replay` + `FillEngine` + this strategy class + `apply_costs_to_trades`
+ `compute_metrics` — reuse those functions directly, don't reimplement
metrics). Run it on:
- The 4 in-sample days (June 29 - July 2) as one combined run.
- The 4 out-of-sample days (July 3-6) as a separate combined run.

Report the same metrics block `run_backtest.py` normally prints (bps
expectancy, t-stat, win rate, profit factor, max drawdown, breakdown by
exit_reason — note `exit_reason` will now only ever be `take` or `stop`,
never `reversal`).

### Write-up

Append a short summary (raw numbers first, your preliminary read
second, clearly labeled as preliminary) to a new file
`docs/BIAS_AUDIT_2026-07-07.md`. Do not touch any other doc file.

### Before your final commit

Run `pytest -q` on the whole suite (existing 185 + whatever you added).
State the new total in the commit message.

### Do NOT touch

`CONTEXT.md`, `README.md`, `strategy.py`, `replay.py`, `orders.py`,
`costs.py`, `metrics.py`, `run_backtest.py`, `swings.py`, any file
outside what's listed above in "Files in scope".