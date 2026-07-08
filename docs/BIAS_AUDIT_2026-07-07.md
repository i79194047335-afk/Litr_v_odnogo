# Bias Audit & No-Reversal-Exit Variant — 2026-07-07

## Part A — Bias-regime empirical audit

Ran on all 8 real BTC days (exchange 1, range_size=15.3, exit_mode=swing,
trailing=False).  Bias measured at every range-bar close via `compute_bias()`
— a pure, stateless function with zero acceptance/hold-time filter.

### Calibration (Jun 29 – Jul 2)

| Day | Bars | Regimes | Bull med | Bear med | None med | <5 bars (B/B/N) |
|-----|------|---------|----------|----------|----------|-----------------|
| 0629 | 3,791 | 92 | 25 | 22 | 10 | 1/8/13 |
| 0630 | 10,411 | 301 | 16 | 18 | 10 | 6/9/30 |
| 0701 | 13,551 | 257 | 19 | 26 | 12 | 9/5/28 |
| 0702 | 12,192 | 263 | 36 | 32 | 14 | 8/11/25 |
| **Pooled** | 39,947 | 925 | **20** | **23** | **11** | 25/33/99 |

**Bias age at entry (pooled, n=1416 trades):**
- Median: 77 bars
- Min: 1 bar, Max: 915 bars
- <5 bars: 127 (8.9%), 5-20 bars: 202 (14.3%), >20 bars: 1087 (76.8%)

### Out-of-sample (Jul 3–6)

| Day | Bars | Regimes | Bull med | Bear med | None med | <5 bars (B/B/N) |
|-----|------|---------|----------|----------|----------|-----------------|
| 0703 | 7,162 | 293 | 15 | 14 | 8 | 21/12/38 |
| 0704 | 4,835 | 277 | 10 | 9 | 5 | 16/21/64 |
| 0705 | 5,223 | 275 | 14 | 12 | 6 | 17/21/55 |
| 0706 | 15,760 | 283 | 30 | 19 | 13 | 6/9/27 |
| **Pooled** | 32,981 | 1,130 | **16** | **13** | **8** | 60/62/182 |

**Bias age at entry (pooled, n=1,142 trades):**
- Median: 54 bars
- Min: 1 bar, Max: 1,819 bars
- <5 bars: 130 (11.4%), 5-20 bars: 197 (17.3%), >20 bars: 815 (71.3%)

### Preliminary read (Ivan + Claude to verify independently)

**The "noise-reaction" gap is real but only affects a minority of entries.**
~9-11% of trades enter a bias regime that has existed for <5 bars — these
are the cases where the entry is reacting to a freshly-flipped bias that
hasn't had time to prove durability.  The median regime lasts 13-23 bars
(directional) or 8-11 bars (None), so short-lived regimes are common, but
most entries land in established regimes (median age 54-77 bars).

**Bias churn is high.**  30-50% of regimes last <5 bars across both sample
sets.  Beggs' warning about trend-reading without a hold-time filter is
borne out in the data: the strategy recomputes bias fresh every bar from
EMA + 1m close, and the result is constant micro-flips.  A hold-time or
confirmation-count filter on bias would eliminate the bottom tier of
regime durations — whether that improves or degrades expectancy is an
empirical question for a follow-up.

**Key caveat:** regime detection uses bar-close bias values.  Intra-bar bias
changes (from 1m candle closes) are not captured — a regime could flip
mid-bar and flip back before the bar closes, and this analysis would miss it.
That said, `bias()` recomputes at the same granularity that the strategy
uses for entry/exit decisions, so the bar-close view matches what the
strategy actually acts on.

---

## Part B — No-reversal-exit variant

`NoReversalStrategy` overrides both `_check_reversal_bar_mode` and
`_check_reversal_swing_mode` to always return `False`.  Take-profit and
static 0-line stop are the only exits.  Run with exit_mode=swing,
trailing=False (matching the baseline from prior diagnostic runs).

### In-sample (Jun 29 – Jul 2 combined)

| Metric | Value |
|--------|-------|
| Ticks | 1,935,723 |
| Bars | 39,947 |
| Sessions | **30** (cf. 1,370 baseline) |
| Trades | 46 (cf. 1,425 baseline) |
| Expectancy (bps) | −7.48 |
| t-stat | −4.22 |
| Win rate | 28.26% |
| Profit factor | 0.19 |
| Max DD (bps) | 354.35 |
| Stop exits (0% win) | 33 (71.7%) |
| Take exits (100% win) | 13 (28.3%) |

### Out-of-sample (Jul 3–6 combined)

| Metric | Value |
|--------|-------|
| Ticks | 2,841,448 |
| Bars | 32,981 |
| Sessions | **2** (cf. 1,102 baseline) |
| Trades | 4 (cf. 1,152 baseline) |
| Expectancy (bps) | −2.72 |
| t-stat | −0.36 (4 trades — meaningless) |
| Win rate | 50.00% |
| Profit factor | 0.48 |
| Stop exits (0% win) | 2 |
| Take exits (100% win) | 2 |

### Preliminary read (Ivan + Claude to verify independently)

**Removing reversal exits kills the strategy.** Session count drops from
1,370 → 30 (in-sample) and 1,102 → 2 (out-of-sample).  Without reversal
exits, positions that would have been closed by a bias flip instead hold
until either take-profit or stop-loss.  The stop-loss fires on ~72% of
trades (in-sample), dragging the overall expectancy deeply negative.

**This does NOT mean reversal exits are "good."**  The prior session
established that reversal exits are the main loss driver in the standard
strategy — they generate frequent small losses from flips shortly after
entry.  But the alternative (no reversal exit at all) is demonstrably
worse: the strategy stops trading almost entirely, and the few trades it
does take are stop-dominated losers.

**The real question is not "reversal or no reversal" but "what filter
makes reversals selective enough to be net positive."**  The bias audit
(Part A) suggests one candidate: delay or suppress reversal exits that
would flip a bias regime that has existed for only a handful of bars.
Another candidate: the Beggs-acceptance hold-time filter already applied
to the swing exit rule — require that the bias persists for N bars before
a flip is actionable.  Both are empirical follow-ups, not settled
conclusions.

**Caveat:** The OOS no-reversal run has only 2 sessions and 4 trades —
essentially no trading activity.  This is consistent with the in-sample
result: if reversal is the only mechanism that gets the strategy out of
positions, removing it leaves positions open indefinitely, and the
strategy spends most of its time sitting in a position rather than
entering new ones.  The 2 OOS sessions likely represent the very first
entry after each warm-up period that hasn't been exited yet.
