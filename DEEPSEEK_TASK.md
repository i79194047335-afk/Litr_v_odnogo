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
- When done, leave the final `git log --oneline -3` of the branch as the
  last line of your output — Claude will ask Ivan to paste exactly that.

---

## CURRENT TASK: swing-based trailing (replaces bar-based trailing)

**Branch**: `feature/swing-trailing` (already created off `main`, check it
out, don't create a new one).

**Files in scope**: `src/backtest/strategy.py`, `tests/test_strategy.py`.

**Context**: `trailing=True` currently tightens the stop to the low/high
of each closed range bar (function `trail_stop`, called from
`_maybe_trail`). On real data this was too tight (95% of trades exited
via stop in a run with trailing on). Replace it with trailing to the
last CONFIRMED swing low/high instead — the same structural point
`exit_mode="swing"` already uses (`SwingTracker` in
`src/backtest/swings.py`, attributes `.last_swing_low` / `.last_swing_high`,
`None` until confirmed). This makes trailing consistent with the exit
rule instead of using an unrelated (bar-based) notion of "recent price".

### 1. Forbid `trailing=True` + `exit_mode="bar"`

In `WFStrategy.__init__`, after `self.trailing` / `self.exit_mode` are
set, add:

```python
if trailing and exit_mode != "swing":
    raise ValueError('trailing=True requires exit_mode="swing" — bar-based trailing is no longer supported (see CONTEXT.md anti-patterns)')
```

### 2. New function `trail_stop_swing`

Add next to the existing `trail_stop` in `strategy.py`:

```python
def trail_stop_swing(side: Literal["long", "short"], current_stop: float,
                      swings: "SwingTracker") -> float:
    """Tighten-only trailing candidate from the latest CONFIRMED swing
    point (not the raw bar extreme). No-op if the relevant swing hasn't
    confirmed yet (swings.last_swing_low/high is None) — same
    tighten-only max/min discipline as trail_stop, so this can never
    loosen a stop either."""
    if side == "long":
        level = swings.last_swing_low
        if level is None:
            return current_stop
        return max(current_stop, level)
    level = swings.last_swing_high
    if level is None:
        return current_stop
    return min(current_stop, level)
```

### 3. Update `_maybe_trail`

Replace the `trail_stop(...)` call with
`trail_stop_swing(self._side, self._stop_price, self.swings)`. After
step 1, this is the only reachable path when `self.trailing` is True —
`self.swings` is guaranteed not `None` in that case (it's only ever
`None` when `exit_mode != "swing"`, which trailing now forbids).

### 4. Docstring

Next to the existing "REVERSAL EXECUTION, corrected 2026-07-06" block in
the class docstring, add a similarly dated block:

```
TRAILING REDESIGN, corrected 2026-07-06 (was: trail to raw bar low/high —
too tight, 95% of trades exited via stop in a real run; now: trail to
last CONFIRMED swing low/high, consistent with exit_mode="swing".
trailing=True now requires exit_mode="swing").
```

### 5. Tests to remove/replace

`test_trailing_wiring_integration` (current version constructs
`WFStrategy(..., trailing=True)` with no `exit_mode`, i.e. implicit
`"bar"` — this construction will now raise). Replace it with a
swing-mode equivalent: build a tick/bar sequence long enough to confirm
at least one swing low (minimum 5 bars to confirm with
`confirm_bars=2`, per `SwingTracker`'s own confirmation window), then
assert the stop tightens to that swing's price — not to any bar's raw
low. Derive the expected swing-low value by hand from the
`SwingTracker` confirmation rule (candidate bar's low is lower than the
2 bars before AND after it), not from running the code.

### 6. Tests to add

- `WFStrategy(..., trailing=True, exit_mode="bar")` →
  `pytest.raises(ValueError)`.
- `WFStrategy(..., trailing=True, exit_mode="swing")` → constructs
  without error.
- `trail_stop_swing`: swing not yet confirmed (`last_swing_low=None`) →
  returns `current_stop` unchanged.
- `trail_stop_swing`: swing confirmed and pulls the stop forward (long:
  `level > current_stop`) → returns `level`.
- `trail_stop_swing`: swing confirmed but would loosen the stop (long:
  `level < current_stop`) → returns `current_stop` unchanged
  (never-loosens).
- `trail_stop_swing`: at least one symmetric short-side case.

### 7. Do NOT touch

`CONTEXT.md`, `README.md`, anything outside
`src/backtest/strategy.py` / `tests/test_strategy.py`.

### 8. Before your final commit

Run `pytest -q` on the whole suite. Put the resulting pass count in the
commit message (starting point was 167; state the new total).
