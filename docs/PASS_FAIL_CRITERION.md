# Pass/fail criterion — swing-mode + swing-trailing re-run

Status: provisional, set by Claude at Ivan's explicit request (2026-07-07,
"делай на своё усмотрение, как предлагал, если что потом поправим").
Not yet exercised against a real result. Adjustable after seeing numbers —
this is Ivan's risk-tolerance call, Claude just wrote the first draft down.

## The rule

Worth continuing toward paper trading if ALL of the following hold on the
OUT-OF-SAMPLE days (July 3–6, 2026 — NOT the June 29–July 2 calibration
days range_size=15.3 was tuned on):

1. Net expectancy > 0 bps (after fees; funding stub currently 0).
2. t-stat >= 3 on that same out-of-sample sample (rough sanity check, not
   a formal test — trades aren't independent, per metrics.py's own
   caveat).
3. Still net positive at fill_probability = 0.7 (i.e. assuming only 70%
   of resting limit orders actually fill), same out-of-sample days.

If any of the three fails: not a green light. That doesn't automatically
mean "the strategy is dead" — it means diagnose why (which condition
failed) before deciding what's next, rather than lowering the bar to
match whatever number came out.

## Explicitly NOT gated by this criterion (report, don't gate)

- **Concentration risk**: if the result only passes because a small
  handful of trades prop up the average (already seen once — take exits
  were 1.2% of trades, 42% of profit), that's a fragility flag worth
  reporting alongside the headline number even on a technical pass.
- **slippage_ticks sensitivity** — cheap to check, worth reporting, not
  a hard gate.
- **Whether range_size=15.3 is even the right size for the out-of-sample
  regime** — if the criterion fails, this is one thing to check before
  concluding "no edge" (see Next steps item 4, out-of-sample split).

## Why these numbers

- bps not R — R's denominator (stop distance) is rarely the realized
  risk once exits are dominated by structural rules, not the stop.
- t>=3 is a common rough heuristic for "probably not noise," not a
  rigorous claim, same caveat as everywhere else in this project.
- fill_probability=0.7 is a stress test, not a measured Lighter fill
  rate — no real fill-rate data exists yet. 0.7 chosen as "a
  meaningfully pessimistic haircut," nothing more. Revisit once real
  fill-rate data exists.
- Out-of-sample, not in-sample — every number this project has produced
  so far was measured on the same 4 calibration days. A "pass" that only
  holds in-sample repeats the exact overfitting risk Next steps item 4
  exists to catch.

## Status log

- 2026-07-07: initial provisional version. Not yet run against the
  swing+trailing re-run.
