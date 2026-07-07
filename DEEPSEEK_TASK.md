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
- When done, leave the final `git log --oneline -3` of the branch as the
  last line of your output — Claude will ask Ivan to paste exactly that.

---

## CURRENT TASK: diagnostic bar/trade export for visualization

**Branch**: create `feature/export-sample` off current `main`.

**Files in scope**: new file `src/backtest/export_sample.py`, new file
`tests/test_export_sample.py`. Do not modify `strategy.py`, `replay.py`,
or any other existing file — this is a read-only diagnostic tool, built
by observing `WFStrategy` from the outside (subclassing), not by
changing it.

**Context**: Ivan can't see range-bar charts directly (no charting UI —
this is a heredoc/VPS-only workflow) and needs a way to visually inspect
what the strategy is doing bar-by-bar: bias, zone lines, swing points,
entries, and exits (especially reversal exits, which are currently the
main loss driver — see CONTEXT.md session log). This task exports a
window of bars plus the trades that occurred in that window as JSON,
which Claude will then render as an interactive chart.

### 1. Design: subclass, don't modify

```python
from src.backtest.strategy import WFStrategy

class ObservedStrategy(WFStrategy):
    """Wraps WFStrategy to snapshot per-bar state after each bar close,
    without touching strategy.py. Snapshots only bars in
    [start_bar, start_bar + n_bars) by 0-indexed order of closure."""

    def __init__(self, *args, start_bar: int = 0, n_bars: int = 300,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.start_bar = start_bar
        self.n_bars = n_bars
        self._bar_index = -1
        self.snapshots: list[dict] = []

    def on_range_bar(self, bar) -> None:
        super().on_range_bar(bar)
        self._bar_index += 1
        if self.start_bar <= self._bar_index < self.start_bar + self.n_bars:
            b = self.bias()
            self.snapshots.append({
                "index": self._bar_index,
                "start_ts": bar.start_ts,
                "end_ts": bar.end_ts,
                "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close,
                "n_ticks": bar.n_ticks,
                "bias": b,  # Bias = Literal["bull", "bear"] in strategy.py — plain string, no .value
                "lines": dict(self._lines) if self._lines else None,
                "swing_low": self.swings.last_swing_low if self.swings else None,
                "swing_high": self.swings.last_swing_high if self.swings else None,
                "in_position": bool(self._pos),
                "side": self._side,
                "stop_price": self._stop_price,
            })
```

`Bias` is confirmed as `Literal["bull", "bear"]` in `strategy.py` — a
plain string, not an enum. Use `b` directly as shown above.

### 2. CLI (`src/backtest/export_sample.py`, mirror `run_backtest.py`'s
argument style — reuse `load_ticks`, `iter_price_ts` and construction
pattern from there rather than duplicating differently):

```
python -m src.backtest.export_sample \
    --files data/ticks/trades_1_20260703.jsonl \
    --range-size 15.3 --tick-size 0.1 \
    --exit-mode swing \
    --start-bar 0 --n-bars 300 \
    --out sample.json
```

Also accept `--trailing` and `--swing-confirm-bars`, same as
`run_backtest.py`, passed straight through to `ObservedStrategy`.

### 3. Trade filtering

After running the replay to completion, filter `strategy.trades` (list
of `Trade`, see the dataclass in `strategy.py`) to only those whose
`entry_ts` OR `exit_ts` falls within
`[snapshots[0]["start_ts"], snapshots[-1]["end_ts"]]` (inclusive) — a
trade opened just before the window or closed just after it should
still show up, since its entry or exit marker is still relevant context
even if the other end is outside the window.

### 4. Output JSON shape

```json
{
  "meta": {
    "files": ["..."], "range_size": 15.3, "tick_size": 0.1,
    "exit_mode": "swing", "trailing": false, "swing_confirm_bars": 2,
    "start_bar": 0, "n_bars": 300,
    "bars_exported": "<actual count, may be < n_bars if the run ended early>"
  },
  "bars": "<one dict per snapshot, as built above, in index order>",
  "trades": "<filtered Trade dicts: side, tag, entry_price, entry_ts, exit_price, exit_ts, exit_reason, r_multiple>"
}
```

Write with `json.dump(..., indent=None)` (compact, this will be pasted/
uploaded, no need for human-formatted JSON) via `--out <path>`; if
`--out` is omitted, print compact JSON to stdout.

### 5. Tests (`tests/test_export_sample.py`)

Build a tiny synthetic tick stream (a handful of hand-picked prices/
timestamps — same style as existing `test_strategy.py` fixtures) where
you can hand-derive exactly which bars should close and what
`start_bar`/`n_bars` should select. Cover:
- Window selection: with a stream producing >= 6 known bars, request
  `start_bar=2, n_bars=3` and assert exactly bars index 2,3,4 appear in
  `snapshots`, with hand-verified OHLC values for each (derive from the
  synthetic tick stream on paper, not from running the code).
- Trade filtering: construct a scenario (reuse `SWING_FORMATION_BARS`-
  style fixtures from `test_strategy.py` if that's easier than
  inventing new ones — check what's importable) where a trade's
  entry_ts is INSIDE the window and exit_ts is AFTER it — assert it's
  still included. And one where both entry_ts and exit_ts are before
  `start_bar`'s window — assert it's excluded.
- JSON output is valid and round-trips (`json.loads(json.dumps(...))`
  matches the in-memory dict).

### 6. Before your final commit

Run `pytest -q` on the whole suite (existing 174 + your new tests).
State the new total in the commit message.

### 7. Do NOT touch

`CONTEXT.md`, `README.md`, `strategy.py`, `replay.py`, `run_backtest.py`,
any file outside the two new files listed in "Files in scope".