# Litr_v_odnogo — project context / handoff

> Read this file end-to-end when resuming work in a new chat. For deeper
> research see `docs/ytc_scalper_skeleton.md` and `docs/kb_mrcvokka_diary.md`.
> Lance Beggs' original articles (Russian translations) are also available
> as reference PDFs in Claude's project knowledge — NOT in this git repo.
> They were the source that corrected the reversal-exit definition (see
> Work plan item 8 and the 2026-07-05 session log).
>
> Before trusting any "done" claim in here (including this file's own test
> counts), re-clone the repo and run `pytest` yourself. This file has
> drifted from the real pushed state twice already (see the 2026-07-06
> session log) — once from a rename sitting uncommitted, once from an
> entire bugfix (reversal-exit-via-FillEngine) sitting uncommitted on the
> VPS while a session summary reported it as done and pushed. Both times
> the only thing that caught it was a fresh clone + test run, not reading
> this file or a chat summary.

## TL;DR

Backtest a range-bar scalping strategy (Lance Beggs / YTC Scalper family, as
applied by the mrcvokka forum diary) on Lighter perp DEX. The user is new to
crypto/trading and is deliberately taking the safe path: **tick collection →
backtest → paper trading**. No live-trading code is in scope until backtest
shows edge. This is a learning/research project, not financial advice.

## Where the code lives

- Repo: https://github.com/i79194047335-afk/Litr_v_odnogo (public)
- VPS: `vm1744139.vds.as210546.net`, project in `/root/projects/Litr_v_odnogo`,
  venv in `.venv/`
- Workflow: GitHub is source of truth → `git pull` on VPS → edits via bash
  heredoc → commit → push
- Secret protection: pre-commit gitleaks locally + GitHub Action gitleaks as
  a backstop

## What works

1. **Range-bar builder** (`src/rangebars/builder.py`) — pure logic, 2 tests pass.
2. **Lighter live trade collector** (`src/collector/lighter_ticks.py`) — 24/7
   as `lighter-ticks.service` systemd unit.
   - Raw WS `wss://mainnet.zklighter.elliot.ai/stream`, no auth.
   - Subscribes to `trade/{market_id}` for all 5 markets: ETH(0), BTC(1),
     SOL(2), HYPE(24), XAU(92) — expanded from ETH/BTC-only on 2026-07-03.
   - Writes JSONL per UTC day: `data/ticks/trades_{market_id}_{YYYYMMDD}.jsonl`.
   - Deduplicated by `trade_id`. `subscribed/trade` snapshot frames are
     dropped (they're re-delivered via `update/trade` — persisting them
     caused ~17% duplicate rows in the legacy v1 schema).
   - Tenacity backoff + systemd `Restart=always`.
3. **Historical backfill** (`src/collector/oxarchive_backfill.py`) — pulls
   Lighter trade history from 0xArchive into the same JSONL schema and same
   directory as the live collector, so downstream code is source-agnostic.
   - Two commands: `list-markets` (discovery) and `backfill` (per-day
     download with cursor pagination, idempotent).
   - Includes a monkey-patch for an oxarchive v1.7.0 bug where
     `_convert_timestamp()` calls `int()` on composite cursor strings
     like `'1759276893930_553252220005'` — Python's `int()` treats `_` as a
     digit separator (PEP 515), producing a garbage number that the API
     doesn't understand, causing infinite pagination loops.
   - Guards: stuck-cursor detection and `max_pages_per_day` cap, both
     discard the day's `.part` file rather than persist partial data.
4. **Cross-check tool** (`src/collector/compare_sources.py`) — compares two
   JSONL files (one live, one backfill) for the same market and UTC day.
   Prints volumes, side distributions, matched/only-in counts and a
   plain-language verdict for the A/B → buy/sell mapping and overall
   overlap. No network access, just file diffing.

### JSONL schema

The collector emits schema **v2**; older files on disk are schema v1.
Downstream loaders must accept both.

- **v1 (legacy, pre-dedup fix):**
  `{"m": int, "p": float, "s": float, "t": ms, "side": "buy"|"sell"}`
- **v2 (current):**
  `{"m": int, "p": float, "s": float, "t": ms, "side": "buy"|"sell", "tid": int}`

`side` is derived from `is_maker_ask` (taker side = aggressor). `tid` is
Lighter's unique trade_id — the anchor for both dedup and cross-source
matching.

## Confirmed Lighter facts

- **SDK on PyPI**: `lighter-sdk` (imported as `import lighter`). Install
  from git: `pip install git+https://github.com/elliottech/lighter-python.git`.
- **WS**: `wss://mainnet.zklighter.elliot.ai/stream`, `pong` reply as
  `{"type":"pong"}`, requires a frame every 2 minutes.
- **Public WS channels** (no auth): `order_book`, `trade`, `ticker`,
  `market_stats`, `spot_market_stats`.
- Built-in `lighter.WsClient` only supports `order_book` and `account_all`
  — for trades we use raw WS.
- **Market IDs** (verified): ETH = 0, BTC = 1, SOL = 2, HYPE = 24, XAU = 92.
  0xArchive reports 221 total Lighter markets.
- **Fees**: maker = taker = **0.0000** on ETH, BTC, and all top-volume
  markets we checked. Scalping is more viable here than on CEX.
- **Trade message fields** (from real captures):
  `trade_id, market_id, size (str), price (str), usd_amount (str),
  is_maker_ask (bool), timestamp (ms), block_height, ask/bid_account_id, ...`
- **Precision**: ETH size_decimals=4, price_decimals=2, min_base=0.005.
  BTC size_decimals=5, price_decimals=1, min_base=0.0002.
- Lighter is a **ZK rollup**; matching happens off-chain in the Sequencer,
  only batched state roots are committed. Individual trades are **not**
  emitted as on-chain events, so `eth_getLogs` on Arbitrum is a dead end
  for historical data — confirmed and ruled out.
- **Colo for low-latency live** (later, not now): AWS Tokyo `ap-northeast-1a`.

## Confirmed 0xArchive facts

- Free tier via **web3 wallet signup**: 50K credits/month, 15 req/s,
  10 WS subs. (Earlier believed to be 10M credits — that number was wrong;
  the real free tier is 50K.)
- Trades cost 1 credit per 1000 rows.
- **Lighter coverage nominally starts Aug 2025**, but empty for all
  markets we probed until **2025-10-01**. XAU has no fills until
  **2025-10-20**. Use per-symbol `start_date` in config, not a common one.
- Trade object has `side: Literal['A', 'B']`, but on Lighter data this
  field is **effectively constant `'B'`** — not usable as a side signal.
- `crossed: bool` **verified as the side signal** (2026-07-02: 17,788
  trades joined by `tid` against live `is_maker_ask`, 100.00% agreement).
  Mapping: `crossed=True → buy`, `crossed=False → sell`.
- **0xArchive systematically undercounts, and the loss is bursty, not
  random.** Same-hour gap-distribution check: live had 5,441 adjacent-`tid`
  pairs (`gap=1`, back-to-back trades), backfill had 10. Missing tids form
  runs of consecutive integers. Worst possible loss pattern for a
  range-bar strategy — it distorts bar closings and undercounts volume
  exactly during bursts. **October 2025 backfill data is downgraded to
  reference-only** — not used for range-bar calibration or fill-rate
  estimation.
- Cursor format is `{timestamp_ms}_{trade_id}`, which triggers the
  PEP 515 bug in the SDK. Our monkey-patch handles this.

## Cross-check result — 2026-07-01 BTC

Ran `compare_sources` on the first full UTC day where the live collector
had `tid` support. Findings:

- **Volume**: live = 591,873 trades, backfill = 369,079 (backfill is 37.6%
  lower).
- **Side**: live 49.7% buy / 50.3% sell — plausible. Backfill 100% sell
  — confirms `Trade.side` from 0xArchive is unusable for Lighter.
- **Initial interpretation was wrong.** First read: "0xArchive is complete,
  live was inflated by ~17.5% dupes." Corrected 2026-07-02 by the
  gap-distribution diagnostic above — 0xArchive is the one dropping data
  (bursts specifically), not live over-counting. Both sources had real,
  separate problems: live had dupes (fixed below), 0xArchive undercounts
  bursts (not fixable on our end — hence reference-only).
- **After dedup fix in live**: measured 0.0% dupes over 1.5 h on July 2.
  New collector output is clean.

## Data on disk

`data/ticks/` currently holds 102 files, ~1.8 GB, ~26M trades across five
markets. Coverage:

| m_id | Symbol | Days | Range                    | Source        | Schema |
|------|--------|------|--------------------------|---------------|--------|
| 0    | ETH    | 3    | 2025-10-01 → 10-03       | backfill      | v2     |
| 0    | ETH    | 2+   | 2026-06-29 → today       | live (mixed*) | v2     |
| 1    | BTC    | 3    | 2025-10-01 → 10-03       | backfill      | v2     |
| 1    | BTC    | 2+   | 2026-06-29 → today       | live (mixed*) | v2     |
| 2    | SOL    | 31   | 2025-10-01 → 10-31       | backfill      | v2     |
| 24   | HYPE   | 31   | 2025-10-01 → 10-31       | backfill      | v2     |
| 92   | XAU    | 30   | 2025-10-20 → 2025-11-18  | backfill      | v2     |

*"mixed" = the file was first written by an earlier backfill run and then
appended to by the live collector. Not clean for cross-check purposes.
Only the first UTC day where live wrote from `00:00:00.x` matters —
verified for 2026-07-01 BTC.

**Duplicate rates in legacy v1 data** (pre-fix): ETH 21.7%, BTC 16.7%.
After the fix, new data is 0.0%. All pre-July-2 data therefore needs a
one-time dedup pass (see Open Questions).

**Status: the October 2025 backfill rows in this table are reference-only**
(see the undercount finding in "Confirmed 0xArchive facts"). Calibration
and backtesting will use live-collected data exclusively, going forward.
Live coverage expanded to all 5 markets on 2026-07-03 (previously
ETH/BTC only) — SOL/HYPE/XAU now accumulating live data too.

## Strategic core (what we backtest)

See `docs/ytc_scalper_skeleton.md` for the full breakdown. In brief:

- **Source**: Lance Beggs "YTC Scalper" + mrcvokka's binguru.net diary
  (all 19 pages in `docs/kb_mrcvokka_diary.md`).
- **Mechanized subset**:
  - HTF = 5m, TF = 1m, scalping chart = 1-range bars (built from ticks).
  - Bias: EMA(15)/EMA(20) cross on 1m — rough proxy for Beggs' discretionary
    trend read.
  - Keltner channel: Keltner(35, 4) + Keltner(35, 8) on range bars.
  - Wholesale/retail zones between 0 / ¼ / ½ / ¾ / 1 lines.
  - **WF (with-flow) setups only.** CF setups (require order-flow reading)
    are excluded.
  - Entry: limit orders at ¼ and ½ lines, stop past 0, two parts.
- **Range-bar size heuristic** (mrcvokka): ~30% of average 1m candle range
  for the session. Calibrated from collected ticks before backtesting.
- **Trailing is critical**: mrcvokka's diary reports trailing improvements
  gave ×5 to daily PnL. Model it as carefully as entry.
- **Honest expectation**: without discretion (environment classification,
  order-flow reading), Beggs' edge is materially weaker. The backtest
  measures *how much* weaker.

## What we deliberately did NOT port to the bot

- Discretionary bias call (Beggs uses PA + swing structure).
- Environment classification (trend/volatile/chop) — and it drives which
  line to enter on.
- Counter-flow (CF) setups — require reading order flow / tape.
- 5m S/R zones — drawn by eye, not from swings.

## Work plan

1. ✅ Range-bar builder + tests
2. ✅ Lighter live trade collector + systemd
3. ✅ Historical backfill (0xArchive) + cross-check tool
4. ✅ Dedup fix in live collector (schema v2 with `tid`)
5. ✅ **Verified `crossed` → side mapping** in 0xArchive using tid-anchored
   overlap (100.00% agreement, 17,788 trades). Result: mapping confirmed,
   but 0xArchive also undercounts bursts by ~24–37% — so this *rules out*
   full historical backfill rather than unlocking it. Backfill data stays
   reference-only; live collector is now the sole source for calibration
   and backtesting.
6. ⏳ **One-time dedup pass** for legacy v1 JSONL files (by `(t,p,s,side)`
   key since they have no `tid`).
7. ✅ **Indicators + range-bar calibration done**:
   - EMA (streaming, seeded from first price), Keltner (shared core
     computes SMA(close) and SMA(H-L) once, both mult=4 and mult=8 read
     off it — cannot desync), Stochastic 3/2/3 (slow: k=3, slowing=2,
     d=3). 34 tests against hand-computed values.
   - Range-bar calibration (`src/rangebars/calibrate.py`): mrcvokka's
     30%-of-mean-1m-range heuristic, pooled per market over collected
     live days. Reports mean-all, mean-nonzero, zero-minute fraction —
     surfaces the illiquid-market ambiguity rather than hiding it.
   - BTC calibrated on 4 live days (June 29 – July 2): **range_size = 15.3**
     (153 ticks at tick size 0.1). October 2025 backfill vs current-week
     live gave 18.0 vs 15.3 — 17% swing driven by a genuine volatility
     regime difference (October was more volatile), NOT by backfill
     undercount as first predicted. range_size is regime-dependent, not
     a constant. Written to `config.yaml::rangebars.range_size` with a
     comment to that effect.
   - ETH/SOL/HYPE/XAU calibration pending: need clean live days first
     (SOL/HYPE/XAU only started live 2026-07-03).
8. ✅ **Event-driven backtester** (all 5 slices done, plus a correction):
   - ✅ Slice 1 — replay harness (`src/backtest/replay.py`): one pass
     over ticks builds range bars and 1m candles in parallel, with a hard
     lookahead guarantee (a bar-close handler never sees a 1m candle that
     ended after that bar). The 1m candle for minute M only closes when
     the first tick of a later minute arrives — the next trade is the
     clock; a live process on the same WS stream would know exactly the
     same amount. 12 tests, two of them dedicated to pinning event order.
   - ✅ Slice 2 — fill engine (`src/backtest/orders.py`): limit orders
     fill "through, not touch" at the limit price (queue ambiguity → we
     under-fill rather than over-fill); stop orders "touch, trigger" at
     the TICK price (gap slippage is not softened). Asymmetry is
     deliberate — pessimistic on both entries and stops. Slice 4 added
     `slippage_ticks` (stops only) and `fill_probability` (limits only),
     both defaulting to neutral. 25 tests.
   - ✅ Slice 3 — WF strategy (`src/backtest/strategy.py`): two-condition
     bias with a neutral zone (EMA cross AND close past fast; both EMAs
     must be past warm-up), zone lines off the shared Keltner core
     (mults 4 and 8 give evenly-spaced 0/¼/½/¾/1 automatically), two-part
     limit entries at ½ and ¼, static stop AT the 0-line (frozen at first
     fill), take-profit at ¾. Per-bar order refresh only while flat.
     Resting-order guard: an entry limit on the wrong side of last tick
     is skipped, not placed. Slice 4 added optional `trailing` (tighten
     stop to last closed bar's low/high, never loosens), default off.
   - ✅ Slice 4 — costs (`src/backtest/costs.py`): maker/taker fees by
     exit_reason (take=maker, stop/reversal=taker), hourly funding
     (Lighter settles once/hour to whoever holds a position at the
     instant — most scalp trades live minutes and never cross a
     boundary, so funding is ~always 0; mechanism built, rate defaults
     to 0 pending real funding-rate collection). 19 tests.
   - ✅ Slice 5 — metrics + runner (`src/backtest/metrics.py`,
     `run_backtest.py`): the actual CLI that answers "does this have
     edge". Session outcomes tracked three-way
     (`n_sessions_part1_only/part2_only/both`) after a binary "part2
     filled" flag was found to conflate genuine scale-in (13 sessions)
     with part1 simply never filling (666 sessions) on real BTC data —
     see session log. **Headline unit is bps of entry price, not
     R-multiple**: on real data the stop fired 1 time in 2909 trades, so
     R's denominator (distance to the line-0 stop) is almost never the
     realized risk and every R number was scaled by a risk that didn't
     happen. bps divides by entry price instead — stable, undistorted.
     R is kept alongside for trades where the stop distance genuinely
     is the risk (the take exits). Also reports stdev/SE/rough t-stat
     (sanity check, not a formal test — trades aren't independent) and
     a breakdown by exit_reason and by entry part. 15 tests.
   - ✅ **Exit-rule correction** (2026-07-05, see session log): Slice 3's
     part-2 exit ("first opposite bar") was found to be a mistranslation
     of Beggs — replaced with `exit_mode="swing"`
     (`src/backtest/swings.py`), a faithful two-stage break-and-acceptance
     rule against real swing structure (HH/HL, 2-bar confirmation each
     side, per the source articles). Opt-in — `exit_mode="bar"` (the
     original) stays the default so every prior result stays
     reproducible; `"swing"` is recommended for new runs. `swings.py` has
     11 tests of its own.
   - ✅ **R-freeze fix** (2026-07-06, see session log): `swing` +
     `trailing` together produced an absurd R-multiple
     (−56,495,836) because the risk denominator was read from the
     CURRENT (trailing-moved) stop instead of the stop at entry — as
     trailing dragged the stop toward price, risk went to ~0 and R blew
     up. bps was unaffected (different denominator), which is how this
     hid until someone actually ran `trailing=True`. Fixed:
     `risk_at_entry` is now frozen per-part at fill time and read by
     `_record` instead of the live stop price. Two regression tests.
   - ✅ **Reversal-exit-via-FillEngine fix** (2026-07-06, see session
     log): reversal exits (either `exit_mode`) used to call `_exit_all`
     directly at the bar's own closing tick — zero latency, zero
     slippage, invisible to `fill_probability`, and adverse exactly when
     it mattered, since a reversal fires when price is already moving
     against you. This was ~99% of exits in every measured run. Fixed:
     the decision to exit now places a stop order at a deliberately
     extreme sentinel trigger price (`_MARKET_SELL_TRIGGER` /
     `_MARKET_BUY_TRIGGER` — a "market order via stop" modeling trick,
     not a new order type) through the same FillEngine every other exit
     uses, guaranteed to fire on the very next tick with slippage
     applied but NOT `fill_probability` (correctly — that model is for
     resting-limit queue ambiguity, not an aggressive next-tick fill).
     Re-evaluating the reversal condition or trailing is skipped while
     an exit is pending. `strategy.py`'s test file now has 36 tests
     total (7 added/updated for this fix and the R-freeze fix combined).
     **Caught a real process failure landing this**: a find-replace
     patch script silently reported success on a 0-match edit and left
     `strategy.py` unchanged; recovered via full-file overwrite verified
     byte-for-byte against a passing sandbox copy. Then the finished,
     locally-passing (167/167) fix sat uncommitted on the VPS into the
     next session — caught only because the new session re-cloned the
     repo and ran `pytest` before touching anything, got 163, and
     traced the gap to `git status` on the VPS. See session log.
9. ✅ **Metrics**: done as part of Slice 5 above (bps headline, R kept,
   win-rate, profit factor, max DD in both units, part-2 fill rate
   corrected to three-way, breakdowns by exit_reason/tag). Walk-forward
   and parameter sensitivity remain open (see Open questions / On the
   horizon).
10. **Only after positive backtest**: paper trading on live WS stream.
11. **Only after successful paper**: talk about live (not in scope now).

## Anti-patterns — do NOT repeat

- Bot fully generated by AI without understanding the code (mrcvokka's
  path: works short-term, falls apart on multi-hour runs).
- AI agent with auto-execute (e.g. Claude Code running code without review).
- Live trading before backtest and paper stages pass.
- Quoting the "+20–162% per day" numbers from the diary as a benchmark —
  those are manual-mode, max leverage, no public proofs.
- **Silent config changes by AI agents**: DeepSeek modifying `config.yaml`
  (dropping symbols, adding fields) during a debug session without
  flagging it. Config is project memory — don't let it drift silently.
  Skepticism applies to Claude too.
- **Accepting AI-suggested code fixes without diagnosis**: the dedup issue
  was real, but "dedupers can be removed since dropped_dup=0" (a
  suggestion made in a partial-day sample) would have been the wrong
  conclusion. Always look at the underlying data before removing safety.
- **Tests that verify the code against itself.** Every meaningful test in
  this repo uses expected values derived by hand, on paper, from the
  rules — not read back from what the implementation produces. Concrete
  payoff (Slice 3, gap scenario, 2026-07-03): a hand-scripted test
  predicted "book must be empty after a gap stop-out" and the code
  disagreed — turned out an entry fill on the same tick had superseded
  the tracked stop with a resized replacement, but the strategy cleared
  its stop tracker unconditionally when the OLD stop's fill came
  through, leaving a phantom size-2 stop on the book. Exactly the class
  of bug that only shows up on multi-hour runs. Same principle as the
  dedup lesson above: don't clear safety trackers without checking
  which specific instance you're clearing.
- **Mechanizing a discretionary concept without the source text.**
  `ytc_scalper_skeleton.md` said "hold part 2 until the first 1-range
  reversal" — that got coded as "first bar where close < open", which on
  near-random ticks fires roughly every 2 bars by construction (verified
  against a fair-coin baseline). Once the actual Beggs articles were
  read (2026-07-05), his real definition of a reversal is a broken swing
  structure (HH/HL) CONFIRMED by price accepting the break over a
  further bar — a single opposite bar is explicitly described as often a
  reason to re-enter, not exit. Result on real BTC data: 2886 of 2909
  trades exited via the wrong rule, only 35 ever reached the take
  target. A paraphrased summary of a discretionary method is not a
  specification — when a concept is doing real mechanical work (an exit
  rule, a threshold), go back to the primary source before encoding it,
  don't extrapolate from a one-line gloss.
- **A patch script that reports success unconditionally is worse than one
  that fails loudly.** 2026-07-06: a find-replace script printed "EDIT N
  FAILED: found 0 matches" for a mismatch but kept going anyway, wrote
  the file unchanged, and then printed an unconditional success message.
  `strategy.py`'s edits never landed; the tool's own output said they
  had. An earlier script in the same session correctly used
  `raise SystemExit(1)` on failure — that's the standard every patch
  script needs, not just the production code. Recovery: full-file
  overwrites verified byte-for-byte against a passing sandbox copy, plus
  an explicit "grep for the new symbol, tell me the count" step after
  every paste.
- **"GitHub is the source of truth" needs a fresh-clone check, not just a
  push, and this has now bitten twice.** 2026-07-03/04:
  `iter_price_ts`'s rename and a new test file sat committed-nowhere on
  the VPS while `run_backtest.py` was actually unimportable from a clean
  clone. 2026-07-06, same failure mode, bigger stakes: the entire
  reversal-exit-via-FillEngine fix (167/167 passing locally) sat
  uncommitted on the VPS across a session boundary, while the outgoing
  session's own summary reported it as "done and pushed." Caught only
  because the next session re-cloned and ran `pytest` before trusting
  either the summary or this file — got 163, not 167, and traced it to
  `git status` on the VPS. **Standing rule going forward: start every
  new session with a fresh `git clone` + `pytest` run, before reading
  any chat summary or acting on this file's own "done" claims.**

## Session log (2026-07-06)

Short session, high-value catch: verified the previous session's
handoff summary against the actual pushed repo before acting on it, per
the "GitHub is source of truth" principle this file already preached —
turned out the principle itself had been violated.

- **Fresh-clone check on session start.** Cloned
  `i79194047335-afk/Litr_v_odnogo` and ran `pytest` before touching
  anything. Got **163 passing**, not the 167 the incoming session
  summary claimed. `grep` for `_MARKET_SELL_TRIGGER` /
  `_reversal_exit_id` in `strategy.py` came back empty — the
  reversal-exit-via-FillEngine fix described as "done and pushed" was
  simply not in the repo. `git log --all` and `git show-ref` confirmed
  no other branch had it either.
- **Root cause, found via VPS `git status`**: the fix was real and had
  been reached (167/167 locally) — it was just never `git add`/
  committed/pushed. Same failure mode as the 2026-07-03/04
  `iter_price_ts` drift, this time on the actual strategy-logic fix
  rather than a rename.
- **Recovery**: `git add` the two modified files (`strategy.py`,
  `test_strategy.py`), confirm nothing else was staged, `pytest` once
  more locally (167), commit
  (`backtest: route reversal exits through FillEngine (was bypassing
  fill/slippage)`, `280273b`), push, then independently re-verified with
  a **second** fresh clone from this side: 167/167 passing, sentinel
  trigger + guard code present in `strategy.py`. Ground truth confirmed
  from both directions before writing anything here.
- **This CONTEXT.md update** brings the file in line with that verified
  state — R-freeze fix and reversal-exit-via-FillEngine fix both now
  documented (Work plan item 8), plus the two anti-patterns above.
- **Not done this session** (deferred, see Open questions / next
  steps): trailing redesign, re-run of swing-mode + redesigned trailing
  on the same 4 BTC days, writing the pass/fail criterion before that
  re-run, the out-of-sample split, the EMA-bias audit against Beggs,
  `config.example.yaml` drift, mean-holding-time instrumentation.

## Session log (2026-07-04 – 2026-07-05)

Continuation of the backtester work (Slices 4-5 built, then a real-data
run exposed two measurement bugs and one strategy-fidelity bug):

- **Slices 4 and 5 built and shipped** — costs/slippage/fill-probability/
  trailing (Slice 4), metrics + CLI runner (Slice 5). See Work plan item 8
  for the per-slice detail.
- **First real BTC backtest run** (4 live days, June 29 – July 2,
  range_size=15.3): expectancy +0.0211 R, t-stat 6.0 — looked like a
  significant result at first glance.
- **Part-2 fill-rate bug found from the real output.** `n_sessions_with_part2`
  (binary) reported 23.34% — read as "part2 averages in fairly often".
  Actual breakdown once tracked three-way: 2230 part1-only, 666 part2-only
  (part1 never filled — price passed its resting-order level before the
  next bar's refresh could place it), only 13 sessions where BOTH parts
  genuinely filled. The two-part scale-in Beggs describes essentially
  never happens at this range_size; "part2 fill rate" was conflating that
  with an unrelated opportunistic-entry pattern. Fixed: three explicit
  counters, `scale_in_rate` (both) reported separately from
  `part2_fill_rate` (any, kept for continuity).
- **R-multiple denominator bug found from the same output.** `by
  exit_reason` showed the stop fired ONCE in 2909 trades — 99% of trades
  exit via the reversal rule, nowhere near the line-0 stop. So R (PnL /
  distance-to-stop) was dividing almost every trade by a risk distance it
  never actually incurred, inflating/distorting the apparent effect size.
  Fixed: bps-of-entry-price is now the headline metric (see Work plan
  item 8); R kept as a secondary, meaningful only for take exits.
- **Re-run in bps**: expectancy +0.2925 bps net, t-stat 6.06 (independent
  confirmation the earlier R-based t-stat wasn't just a units artifact —
  bps and R scale together for a fixed-risk trade, so a similar t was
  expected and observed).
- **`by exit_reason` breakdown read carefully**: take exits (n=35, the ONLY
  ones reaching the Beggs-intended ¾ target) average +10.21 bps net,
  100% win rate, and stayed essentially flat (+10.18 to +10.21) across
  every friction test below. Reversal exits (n=2886, 99% of trades)
  averaged only +0.18 bps and were NOT robust to friction (see next).
  35 trades contributed 42% of total bps despite being 1.2% of trades —
  heavy concentration, a fragility flag on its own.
- **User pushback, correctly, on "2886 reversal exits in 4 days" sounding
  like a bug.** Checked the arithmetic: 39,947 bars / 2909 sessions ≈ 1
  trade per 14 bars — normal entry cadence, not a bug. The real anomaly
  isn't trade frequency, it's that reversal exits happen almost
  immediately after entry: a fair-coin baseline predicts ~2 bars to the
  first opposite bar, matching real behavior. Not a bug — a correct
  implementation of a rule that turned out to be the wrong rule (see
  Anti-patterns).
- **Friction sensitivity sweep.** `slippage_ticks` (stops only) barely
  moved anything (0.2925 → 0.2922 bps from 6 to 60 ticks) — expected,
  only 1 trade in the sample is a stop. `fill_probability` (limits only,
  affects 99%+ of trades) degraded the edge sharply and unevenly:
  p=1.0 → 0.2925 bps (t=6.06); p=0.7 → 0.1671 (t=3.40); p=0.5 → 0.0869
  (t=1.70, already "can't rule out noise"); p=0.3 → 0.0333 (t=0.59).
  Critically, the DEGRADATION WAS CONCENTRATED IN REVERSAL EXITS —
  their mean bps went from +0.18 at p=1.0 to NEGATIVE (-0.02 to -0.08)
  at p≤0.5, while take-exit means stayed pinned near +10.2 throughout.
  This independently pointed at the same weak spot as the Beggs-reading
  finding below: the reversal-exit population is fragile/artifact-driven,
  the take-exit population is robust.
- **Beggs source articles uploaded to the project** (Russian translations,
  Claude project knowledge, not in the repo) and read directly (not
  taken from the `ytc_scalper_skeleton.md` paraphrase). Key finding:
  a swing high/low requires 2 bars of confirmation on EACH side (5-bar
  window), and a "trend change" has two stages — an "objective" break
  (price crosses the prior swing point) that on its own is NOT treated as
  a reversal, and "acceptance" (price stays beyond the break) which is
  what actually confirms it. Direct quote translated: "don't automatically
  think that a broken trend means a trend change... price's refusal to
  hold a trend change is often a good signal to enter in the direction
  of the original trend." This directly explains both the fragility
  found in the friction sweep and the earlier over-triggering.
- **Exit-rule correction implemented**: `src/backtest/swings.py`
  (`SwingTracker` + `check_break_and_acceptance`, both pure/hand-tested)
  plus `WFStrategy(exit_mode="swing")`. Old behavior (`exit_mode="bar"`)
  kept as the default so all prior results and tests stay reproducible;
  not recommended for new runs. Not yet re-tested against real data —
  first thing to do next session.
- **Self-correction on process**: mid-session, a `metrics.py` with a
  further bps/StatBlock rework appeared in the assistant's working
  environment that didn't match the deployed repo. Initially
  (incorrectly) flagged as possible external drift (DeepSeek or the
  user); cloning the actual repo showed the deployed file was clean at
  the correct commit and the extra code was the assistant's own
  lost-track-of scratch work, not drift from anyone else. Recorded
  because the instinct to check was right; the accusation aimed at the
  wrong source was not — check your own recent actions before
  suspecting the environment.

## Session log (2026-07-02)

Key events from the current chat, most recent first:

- **Dedup fix landed.** New `lighter_ticks.py` (schema v2 with `tid`,
  `Deduper` class with 50K-per-market FIFO, `subscribed/trade` frames
  dropped, `is not None` checks, missing `is_maker_ask` → drop with
  warning, JSON/YAML error handling, periodic stats logging).
  `tests/test_collector.py` covers `_normalize_trade` edge cases and
  `Deduper` semantics (14 tests). Verified: 0.0% dupes on new data,
  vs 17-22% on legacy files.
- **Cross-check tool written.** `compare_sources.py` — reads two JSONL
  files, prints volume/side/overlap stats and a plain-language verdict.
- **Backfill script written.** `oxarchive_backfill.py` — `list-markets` +
  `backfill` commands, atomic per-day writes, idempotent. Includes SDK
  cursor bugfix (`_patch_cursor_handling`) and guards.
- **oxarchive SDK bug found.** `_convert_timestamp()` mangles composite
  cursor strings via `int()` on PEP-515 underscore digits, causing
  infinite pagination. Diagnosed by DeepSeek, monkey-patched in our code.
  Reported? No — a fix upstream would remove the need for the patch.
- **0xArchive setup done.** Wallet signup, key in `.env`, ~26M trades
  backfilled across 5 markets for October 2025 (partial). BTC/ETH full
  month not backfilled due to disk/credit budget.
- **Cross-check on 2026-07-01 BTC** produced an initial (later corrected)
  diagnosis that (a) 0xArchive `side` field is useless (100% 'B'), (b)
  live collector had ~37% inflation from dupes.
- **`crossed` mapping verified** (2026-07-02, 10:00–11:00 UTC BTC,
  17,788 tid-joined trades): `crossed=True → buy`, 100.00% agreement.
- **Undercount finding** (same session): gap-distribution check showed
  0xArchive drops trades in bursts (5,441 live `gap=1` adjacencies vs. 10
  in backfill for the same hour) — corrects the July 1 interpretation.
  Live was not inflated relative to a complete backfill; 0xArchive was
  incomplete. October 2025 backfill downgraded to reference-only.

## Session log (2026-07-03)

- **`collector.market_ids` expanded from `[0, 1]` to `[0, 1, 2, 24, 92]`.**
  Live collector had been writing ETH/BTC only since 2026-06-29; given the
  live-only calibration decision above, SOL/HYPE/XAU needed live coverage
  too. Collector restarted, all 5 subscriptions confirmed.
- **Auth workflow permanently fixed.** Multiple previous push attempts had
  been failing with `vscode-git-*.sock: ECONNREFUSED`. Root cause: VS Code
  Remote-SSH injects `GIT_ASKPASS` pointing at a socket owned by the VS
  Code window, and stale env vars from a dead window overrode
  `credential.helper` (which is checked AFTER `GIT_ASKPASS` in git's
  resolution order). `gh auth` re-established; the ghost env vars
  (`GIT_ASKPASS`, `VSCODE_GIT_*`, `VSCODE_GIT_IPC_HANDLE`) are now unset
  from `~/.bashrc` on every login shell so this cannot recur silently.
- **Indicators + tests landed** (task 7). EMA, Keltner (shared core),
  Stochastic 3/2/3. All streaming/incremental so the eventual backtester
  can be a single lookahead-free event loop. 34 tests against
  independently hand-computed values (period=3 or period=2 chosen so the
  arithmetic is checkable on paper). One late correction landed here: the
  first pass of the tests used a stub `RangeBar` with defaulted
  `start_ts`/`end_ts`; the real repo dataclass makes them required
  positional. Caught by re-verifying against the repo signature before
  handing the heredoc over.
- **Range-bar calibration landed.** `calibrate.py` + 10 tests. Pooled BTC
  live days gave 15.3 (153 ticks). The globbed run (including 3 October
  backfill days) had come in at 18.0; the 17% gap turned out to be a
  volatility regime difference, not backfill undercount as I had
  predicted. Named explicitly — a wrong prediction is worth flagging, not
  glossing over. BTC written to `config.yaml`; ETH/SOL/HYPE/XAU await
  more clean live days.
- **Backtester slices 1–3 landed** (task 8, in progress). Replay harness,
  fill engine, WF strategy. 39 tests including three hand-scripted
  end-to-end scenarios (take, gap stop, reversal). The gap scenario
  caught the orphaned-stop bug described under Anti-patterns.
- **CONTEXT.md update method.** All CONTEXT.md updates this session used
  targeted Python find-replace scripts (each edit verified to match
  exactly once, or fail loudly) rather than full rewrites — makes drift
  visible instead of silent.

## Open questions

1. ~~`crossed` → side mapping~~ — **resolved 2026-07-02**, see "Confirmed
   0xArchive facts". Mapping confirmed, but the undercount finding means
   we don't act on the "unlock 9 months of history" branch — backfill
   stays reference-only regardless of a correct side mapping.
2. **Retro-dedup of legacy v1 files**. Older JSONL has no `tid`, so
   dedup falls back to `(t, p, s, side)`. Less precise (may collapse
   distinct trades with identical price/size at the same ms) but still
   removes ~17-22% of false volume. One-time script, run in place with
   a `.bak` alongside. Low priority — only a few days of pre-fix data.
3. ~~Retro-dedup or fresh re-backfill?~~ — **moot**. Re-downloading from
   0xArchive with the fixed cursor handling would still hit the same
   burst-undercount ceiling; it's a source limitation, not a pagination
   bug. No further 0xArchive downloads planned.
4. **Cosmetic**: `RuntimeError: Event loop stopped before Future completed`
   at systemd stop. Not a data loss, just noisy in logs. Not fixed yet.

## Next steps (priority order)

As of the 2026-07-06 session, once CONTEXT.md itself is caught up:

1. **Trailing redesign.** Raw "trail to last bar's low/high" is too tight
   — a real run showed 95% of trades exiting via stop once trailing was
   on, up from near-zero without it; ordinary noise clips it almost
   immediately. Two options on the table: swing-based trail (reuse
   `SwingTracker`, trail to the last *confirmed* swing low/high instead
   of every bar's raw extreme — consistent with the just-fixed exit
   rule) vs. a buffered/fixed-distance trail. Not yet decided or built.
2. **Re-run `exit_mode="swing"` + redesigned trailing** on the same 4
   real BTC days (June 29 – July 2, range_size=15.3) once #1 is built.
   This will be the first headline number where none of the three known
   measurement distortions (R-freeze, reversal-exit friction, raw
   trailing) are still in the way.
3. **Write the pass/fail criterion in writing before that re-run**, not
   after. What bps, what t-stat, what robustness across
   `fill_probability`/`slippage_ticks` counts as "worth moving toward
   paper trading" is a risk-tolerance call for the user to make, not a
   technical one for Claude/DeepSeek to infer from the numbers after the
   fact.
4. **Out-of-sample split.** `range_size=15.3` was calibrated on the SAME
   4 days used for every backtest run so far. No result should be
   trusted until tested on days it wasn't tuned on. Entirely
   unaddressed.
5. **Audit the EMA-bias mechanization against the Beggs source**, the
   same way the reversal rule was audited — consistency demands it,
   since the reversal rule turned out to be a mistranslation of a
   one-line gloss and the bias rule came from the same kind of gloss.
6. **Lower priority**: fix `config.example.yaml` drift (`market_ids:
   [0,1]` vs. the live 5-market set; BTC placeholder 5.00 vs. calibrated
   15.3); add mean-holding-time instrumentation to metrics (needed to
   check, not just assume, whether swing-mode's longer holds make the
   funding=0 assumption stop being safe); collect real funding-rate
   history if that instrumentation says it matters.

## Repo structure

src/ collector/   lighter_ticks.py        ✅ v2 schema, dedup by tid, snapshot skip
list_markets.py         ✅ helper
oxarchive_backfill.py   ✅ historical backfill + SDK bugfix
compare_sources.py      ✅ cross-source cross-check
rangebars/   builder.py              ✅ + tests
calibrate.py            ✅ 30% of mean 1m range + tests
indicators/  ema.py                  ✅ streaming, seed from first price
keltner.py              ✅ shared core, mult 4 & 8
stochastic.py           ✅ slow 3/2/3
backtest/    replay.py               ✅ slice 1: harness, dual series
orders.py               ✅ slice 2: fill engine + slippage/fill-prob
strategy.py             ✅ slice 3+4: WF strategy + trailing +
exit_mode ("bar" default / "swing" fix) +
R-freeze fix + reversal-via-FillEngine (2026-07-06)
swings.py               ✅ swing HH/HL + break-acceptance (2026-07-05)
costs.py                ✅ slice 4: fees + funding
metrics.py              ✅ slice 5: bps/R stats, breakdowns
run_backtest.py         ✅ slice 5: CLI runner, ties it all together
data/ticks/    JSONL, mixed v1 (legacy) and v2 (post-2026-07-02)
docs/
ytc_scalper_skeleton.md              strategic breakdown of Beggs
kb_mrcvokka_diary.md                 full research, 19 pages, forum diary
scripts/lighter-ticks.service          template (real unit in /etc/systemd/system/)
tests/
test_rangebars.py                    2 tests, passing
test_collector.py                    14 tests, passing
test_calibrate.py                    10 tests, passing
test_indicators_ema.py               6 tests, passing
test_indicators_keltner.py           6 tests, passing
test_indicators_stochastic.py        6 tests, passing
test_replay.py                       12 tests, passing
test_orders.py                       25 tests, passing
test_strategy.py                     36 tests, passing (was 30; +6 for
the R-freeze + reversal-via-
FillEngine fixes, 2026-07-06)
test_costs.py                        19 tests, passing
test_metrics.py                      14 tests, passing (was misreported
as 15; corrected against actual
pytest output, 2026-07-06)
test_run_backtest.py                 6 tests, passing
test_swings.py                       11 tests, passing
-- total: 167, verified via fresh clone + pytest, 2026-07-06 --
.pre-commit-config.yaml                gitleaks local
.github/workflows/gitleaks.yml         gitleaks server-side
config.example.yaml                    reference
config.yaml                            local, not in git
CONTEXT.md                             this file

## Command memo

```bash
# collector status
sudo systemctl status lighter-ticks --no-pager
tail -f /var/log/lighter-ticks.log
wc -l data/ticks/*.jsonl

# restart after edits
sudo systemctl restart lighter-ticks

# tests
source .venv/bin/activate
python -m pytest tests/ -v

# range-bar size calibration (pool per market)
python -m src.rangebars.calibrate --market 1 --data-dir data/ticks --tick 0.1
# or explicit files (recommended: live-only, no October backfill mixed in):
python -m src.rangebars.calibrate \
    --files data/ticks/trades_1_YYYYMMDD.jsonl \
    --tick 0.1

# discovery of Lighter markets on 0xArchive
python -m src.collector.oxarchive_backfill list-markets

# historical backfill (configured in config.yaml::backfill)
python -m src.collector.oxarchive_backfill backfill

# cross-check live vs backfill for a specific day
python -m src.collector.compare_sources \
    --live data/ticks/trades_1_20260701.jsonl \
    --backfill data/ticks_backfill/trades_1_20260701.jsonl

# quick dupe check on any JSONL (needs tid — schema v2)
python3 -c "
import json
from collections import Counter
with open('data/ticks/trades_1_20260702.jsonl') as f:
    tids = [json.loads(l)['tid'] for l in f]
c = Counter(tids)
dupes = sum(v-1 for v in c.values() if v > 1)
print(f'rows={len(tids)} unique={len(c)} dupes={dupes} ({100*dupes/len(tids):.3f}%)')
"

# push workflow
git add -A && git commit -m "..." && git push
```

## User profile (important to keep in mind)

- New to crypto and trading — started this project from scratch.
- Understands basics: wallets, stablecoins, DEX/CEX, leverage, TVL/APR,
  maker/taker, gas, RPC.
- Programming: does not write code themselves. Works via bash heredocs on
  the VPS (no IDE).
- Accepts risk warnings, but sometimes overestimates pace — new chats
  should hold the **backtest → paper → live** order firmly.
- Prefers **English** communication.
- Tone: direct, constructive, concise. No hand-holding. Be honest about
  trade-offs and about what Claude can and can't do.
- Uses multiple AI assistants in parallel (Claude + DeepSeek). Expects
  Claude to review and reason about work done by others rather than
  rubber-stamp it — and vice versa.

## What Claude CANNOT do

- No VPS access — all commands go through the user.
- No live GitHub repo access — the user brings a snapshot.
- Cannot create the GitHub repo under the user's account.
- No long-term memory across chats, hence this file.
