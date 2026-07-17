# Rolling range_size calibration — result (2026-07-10)

Implements and measures AUDIT item A4. Reproduce with:

```bash
python -m src.backtest.rolling_variant
```

## The rule

`range_size(D) = 0.30 × mean_1m_range(D−1)`, switched at UTC midnight.

Day `D−1` is complete before day `D` opens, so this carries no lookahead, and
it is something a live bot can actually do — a constant fitted to one week is
not. `rolling.py` refuses `max_staleness_days=0` (that would size a day from
itself) and refuses to bridge a missing day silently.

The first day of any stream has no predecessor. It runs on the seed size and
its trades are **discarded** from every number below. Both variants see the
identical warm-up, so the comparison is apples-to-apples.

## The hypothesis being tested

Every prior out-of-sample run used the constant `range_size = 15.3`, calibrated
on 2026-06-29..07-02. On 2026-07-04/05 the instrument's own volatility called
for ~9. So the recorded OOS result (−0.1740 bps, t = −0.86) might have been
measuring "the strategy with a mis-sized bar" rather than "the strategy on
unseen days". If bar size was the confound, correcting it should improve the
result.

**It does not.**

## Result — BTC, `exit_mode=swing`, `trailing=False`, costs zero

Seed day 2026-07-02 (discarded). Measured 2026-07-03 .. 2026-07-09.

Schedule the rule produced:

| Day | range_size (from prior day) |
|-----|------|
| 07-03 | 15.3 |
| 07-04 | 11.7 |
| 07-05 | 8.9 |
| 07-06 | 9.4 |
| 07-07 | 16.0 |
| 07-08 | 15.3 |
| 07-09 | 14.7 |

Pooled:

| | bars | sessions | trades | net bps | t | win | PF |
|---|---|---|---|---|---|---|---|
| **FIXED 15.3** | 79,947 | 2,709 | 2,390 | **−0.3253** | −2.48 | 34.73% | 0.88 |
| **ROLLING** | 112,577 | 3,869 | 3,644 | **−0.3673** | −4.33 | 35.92% | 0.83 |

Per exit day (mean net bps):

| Day | FIXED | ROLLING |
|-----|-------|---------|
| 07-03 | −0.775 | −0.775 |
| 07-04 | −0.142 | −0.337 |
| 07-05 | −0.003 | −0.182 |
| 07-06 | **+0.080** | −0.254 |
| 07-07 | −0.498 | −0.612 |
| 07-08 | −0.097 | −0.101 |
| 07-09 | −0.854 | −0.753 |

## Reading

**The mis-sized bar was not the reason the strategy loses.** Correcting the
size made the loss *clearer*, not smaller: expectancy went from −0.33 to −0.37
bps and the t-stat from −2.48 to −4.33. The extra significance is mostly a
sample-size effect — right-sized (smaller) bars on the calm days produce more
bars, more sessions and 52% more trades — but the *per-trade* loss did not
improve on a single day where the size actually changed.

Under the fixed size, exactly one day (07-06) printed a positive mean. Under
rolling, no day does. Every day is negative or indistinguishable from zero.

**A4's confound is therefore closed, and the OOS verdict survives it.** The
strategy does not fail condition 1 of `PASS_FAIL_CRITERION.md` (net expectancy
> 0) because of a sizing artifact. It fails it on its own merits, and now with
a larger, better-sized sample and a more negative t.

### Harness sanity check, unplanned but welcome

`0.30 × mean_1m_range(07-02)` rounds to exactly **15.3** at tick 0.1 — the same
value the fixed variant uses. And 2026-07-03 comes out bit-identical between
the two runs (272 trades, −0.775 bps, t = −1.99, 31.25% win). A day where the
schedule and the constant agree produces an identical result: the switching
machinery does nothing when it should do nothing.

## Caveats, named

- **Costs are zero** (Lighter maker = taker = 0), `fill_probability = 1.0`,
  `slippage_ticks = 0`. This isolates the sizing change. Rolling trades 52%
  more, so on any venue with fees it would be *more* punished than fixed, not
  less.
- **Smaller bars ⇒ more trades ⇒ a more significant negative t.** The honest
  statement is "more evidence for the same per-trade loss", not "rolling made
  the strategy worse". Per-trade means are close; only 07-06 moves materially
  (+0.080 → −0.254), and that is the day whose size fell furthest (15.3 → 9.4).
- **One calendar day is a coarse window.** A trailing N-hour window would adapt
  faster. Not tested; it is also not what the mrcvokka heuristic describes.
- **Still one market.** BTC only — AUDIT item D1 is untouched by this.
- The rounding to whole ticks (`tick=0.1`) is inherited from `calibrate.py`.

## What changed in the code

- `src/rangebars/rolling.py` — `utc_day`, `mean_1m_range_by_day`,
  `rolling_range_sizes`, `schedule_from_ticks`. Pure, 18 hand-derived tests.
- `src/backtest/replay.py` — optional `range_size_schedule`; size swaps at UTC
  midnight *before* the tick is fed to the bar builder. `range_size_changes`
  records what actually fired. Default (`None`) leaves behaviour unchanged;
  5 new tests pin both the switching and the unchanged default.
- `src/backtest/run_backtest.py` — `--rolling-range-size`, `--calib-pct`.
- `src/backtest/rolling_variant.py` — the comparison that produced this file.

`config.yaml` was **not** edited: the finding is that a constant is the wrong
shape for this parameter, and writing a fresh constant would bury that.
