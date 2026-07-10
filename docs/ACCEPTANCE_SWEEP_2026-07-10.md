# Acceptance-window sweep — result (run 2026-07-10)

The `feature/acceptance-window` branch (`4e3c068`, 2026-07-07) built
`acceptance_bars` and the sweep to exercise it, but **its results were never
recorded anywhere** — AUDIT item A3. This is that record. Reproduce with:

```bash
python -m src.backtest.acceptance_variant
```

BTC, `exit_mode=swing`, `trailing=False`, `range_size=15.3`, costs zero.

## What the parameter does

`acceptance_bars` = how many bars a swing-structure break must *hold* after
the break bar before the reversal exit fires. It is deliberately separate from
`swing_confirm_bars`, which controls how mature the swing *point* is. Widening
the latter had made results worse; this knob asks the other question.

`acceptance_bars=1` is the pre-2026-07-07 hardcoded behaviour.

## Harness sanity check

`acceptance_bars=1` reproduces the on-record baseline exactly: in-sample
−0.50 bps / t = −2.95, out-of-sample −0.17 bps / t = −0.86. The generalized
code path is behaviour-identical at the default, as `swings.py` claims.

## Headline

| `acceptance_bars` | | sessions | trades | net bps | t | win | PF |
|---|---|---|---|---|---|---|---|
| 1 | in-sample | 1,370 | 1,425 | −0.50 | −2.95 | 34.25% | 0.78 |
| 3 | in-sample | 1,001 | 1,116 | −0.43 | −1.61 | 36.38% | 0.89 |
| 5 | in-sample | 708 | 877 | −0.46 | −1.15 | 41.16% | 0.92 |
| 1 | validation | 1,102 | 1,152 | −0.17 | −0.86 | 34.72% | 0.95 |
| 3 | validation | 851 | 959 | −0.35 | −1.28 | 35.87% | 0.94 |
| 5 | validation | 607 | 761 | −0.12 | −0.32 | 41.66% | 1.00 |

("validation" = Jul 3–6. These days are no longer out-of-sample — they have
now been used to select between variants several times. See AUDIT item A2 and
`PASS_FAIL_CRITERION.md`'s banner.)

**No setting is positive.** Win rate climbs from 34% to 42% and profit factor
reaches 1.00, which looks like progress until you notice that the t-stat
collapses toward zero *because the sample shrinks*, not because the mean
improves. Expectancy at `acceptance_bars=5` is −0.12 bps with t = −0.32:
indistinguishable from noise, and on the wrong side of zero.

## The structural read — this is the interesting part

Mean net bps per exit type, and the count of each (validation days):

| `acceptance_bars` | take | reversal | stop |
|---|---|---|---|
| 1 | **+9.74** × 221 (100% win) | −2.53 × 931 | — × 0 |
| 3 | **+9.75** × 222 (100% win) | −3.27 × 726 | −11.49 × 11 |
| 5 | **+9.75** × 222 (100% win) | −3.03 × 472 | −12.39 × 67 |

Three facts, all stable in-sample too:

1. **The winning population is fixed.** Take exits sit at ~+9.75 bps, 100% win
   rate, and there are ~222 of them no matter what `acceptance_bars` is. This
   knob cannot create winners. It never touches the trades that reach the ¾
   target — those exit on a resting limit, not on a reversal decision.

2. **Holding losers longer does not make them winners; it makes them stops.**
   As the window widens, reversal exits fall 931 → 472 and stop exits rise
   0 → 67. The trades did not turn around. They were merely held until the
   0-line stop caught them instead.

3. **A stop costs ~4× what an early reversal costs.** −12.39 bps vs −3.03 bps
   per trade. So the two effects nearly cancel: fewer, cheaper reversal losses
   are traded for more, dearer stop losses, and the total barely moves.

Check the arithmetic at `acceptance_bars=5` (validation):
`222 × 9.75 + 472 × (−3.03) + 67 × (−12.39) = +2165 − 1430 − 830 = −95`,
over 761 trades = **−0.125 bps**, matching the reported −0.12.

## What this rules out, and what it doesn't

**Ruled out:** "the reversal exit fires too early; make it patient and the
edge appears." It doesn't. Patience converts a small, frequent loss into a
large, rare one at roughly constant total cost. `acceptance_bars` is not the
missing filter.

**Not ruled out:** a filter that acts on *entry* rather than exit. The take
population is genuinely profitable (+9.75 bps, 100% win, ~222 trades) and
completely insensitive to this parameter. The whole problem is the ~1,000
other trades. If something observable *before entry* separates the two
populations, the strategy has an edge; if nothing does, it doesn't. That is
exactly the question `diag_take_vs_rest.py` was written to ask and has never
been run against. **That is the more promising next experiment.**

Note the trap in that framing: "select only the trades that reach the target"
is not a strategy, it is hindsight. The test has to be whether *pre-entry*
conditions differ — volatility, participation, aggressor imbalance — not
whether the outcomes differ.

## Caveats

- Costs zero (Lighter maker = taker = 0), `fill_probability = 1.0`,
  `slippage_ticks = 0`. Stop exits pay taker and eat slippage in reality, so
  the wide-window settings are *flattered* here relative to a real venue.
- Ranges are pooled per day-set; per-day dispersion is not reported here (it
  is in `docs/ROLLING_CALIBRATION_2026-07-10.md` for the sizing question).
- `range_size=15.3` fixed — this sweep predates the rolling calibration
  (AUDIT A4). Re-running it on rolling sizing has not been done. Given A4
  found sizing was not the cause of the loss, it is unlikely to change the
  structural read above, but that is an expectation, not a measurement.
- **`acceptance_bars` remains half-wired** (AUDIT item S2): it lives in
  `swings.py` and `AcceptanceWindowStrategy`, but not in `WFStrategy.__init__`
  nor as a `run_backtest.py` flag. On this evidence there is no reason to
  finish that wiring.
