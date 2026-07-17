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
>
> **`docs/AUDIT_2026-07-10.md` is the live open-items checklist.** It
> supersedes the "Next steps" section below wherever the two disagree, and
> it records three blocking findings (dirty in-sample data, a burnt
> out-of-sample set, repo/CONTEXT drift) that this file does not yet
> reflect. Read it before planning a session. This file's session log and
> test count are stale as of 2026-07-10 (says 174 tests; reality is 210).

## TL;DR

Backtest a range-bar scalping strategy (Lance Beggs / YTC Scalper family, as
applied by the mrcvokka forum diary) on Lighter perp DEX. The user is new to
crypto/trading and is deliberately taking the safe path: **tick collection →
backtest → paper/testnet → live**. SCOPE EXPANDED 2026-07-08: live trading
CODE is now in scope, but ONLY against Lighter **testnet**
(https://testnet.zklighter.elliot.ai). Real-money mainnet trading remains
out of scope until a strategy shows edge in backtest AND survives testnet.
This is a learning/research project, not financial advice.

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

**Duplicate rates in legacy v1 data — RETRACTED 2026-07-10.** This file used
to say "ETH 21.7%, BTC 16.7%, after the fix 0.0%, all pre-July-2 data needs a
dedup pass". That comparison was invalid: the pre-fix number counted
`(t,p,s,side)` collisions, the post-fix number counted `tid` collisions —
two different quantities. Clean v2 files (0.00% `tid` duplicates) collide on
`(t,p,s,side)` at 9.4–17.1%, because one sweep filling several resting orders
prints several same-ms/price/size/side rows. The v1 rates sit inside that
natural range, the live collector logs `dropped_dup=0` continuously, and the
batch-repeat and run-length signatures duplication would leave are both
absent. **The v1 files are not meaningfully duplicated; the dedup pass was
cancelled, not deferred.** See `src/collector/dupe_diagnostic.py` (with tests)
and `docs/AUDIT_2026-07-10.md` item A1 for the full evidence.

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
   - ✅ **Swing-based trailing** (2026-07-06, see session log): raw
     "trail to last bar's low/high" was too tight — a real run showed
     95% of trades exiting via stop once trailing was on. Per Beggs'
     own primary-source management principles (read directly, not
     paraphrased — see session log), trailing should track "peaks and
     troughs" (structural swing points), not every bar. Fixed:
     `trail_stop_swing()` trails to the last CONFIRMED swing low/high
     from the same `SwingTracker` already used by `exit_mode="swing"`,
     same tighten-only max/min discipline as the old `trail_stop()`.
     **`trailing=True` now requires `exit_mode="swing"`** —
     `WFStrategy.__init__` raises `ValueError` on the old
     `trailing=True, exit_mode="bar"` combination, since bar-based
     trailing has no remaining caller and mixing a structural exit rule
     with a non-structural trail was never coherent. Built via a
     DeepSeek task file (`DEEPSEEK_TASK.md`, in
     `feature/swing-trailing`), reviewed line-by-line (diff + a
     from-scratch hand check that the test's claimed swing_low=95 is
     correct against `SwingTracker`'s own confirmation rule) before
     merge — same review standard as Claude's own commits. 7 tests
     added, `test_trailing_wiring_integration` rewritten for
     swing-mode. `strategy.py`'s test file: 43 tests (167 → 174
     project-wide).
9. ✅ **Metrics**: done as part of Slice 5 above (bps headline, R kept,
   win-rate, profit factor, max DD in both units, part-2 fill rate
   corrected to three-way, breakdowns by exit_reason/tag). Walk-forward
   and parameter sensitivity remain open (see Open questions / On the
   horizon).
10. **Testnet infrastructure track (NEW, 2026-07-08)**: panel + connector
    infrastructure against Lighter testnet, in parallel with (not
    replacing) the backtest track. See "Scope expansion & architecture"
    section below. Real money still gated on backtest edge + testnet
    survival.
11. **Only after positive backtest AND successful testnet**: real-money
    mainnet, small size, keys secured. Not before.

## Scope expansion & architecture (2026-07-08)

Project was backtest-only. After a long strategy-review session (the
current range-bar system showed no edge beyond the stable take-exit
component — reversal/stop exits lose regardless of tuning, confirmed
across bias/no-reversal/acceptance-window experiments), direction
shifted: build the **live infrastructure** (neutral to strategy) so
hypotheses can be tested on real markets, not just read as numbers.
Driven partly by forum input (mrcvokka's actual system, ndr's advice):
the edge, if any, likely needs order-book / real S/R levels / lead-lag,
none of which the current Lighter-only backtest sees.

### Architecture: one core, three drivers, strict boundaries

Переделок избегаем ровно одним: стратегия НЕ знает, откуда данные и куда
идут ордера. Три слоя:
1. **Infrastructure (foundation)** — exchange connection, order place/
   cancel, balance/position reads, data stream, error handling. The panel
   runs on THIS. Taken from official SDK (elliottech/lighter-python), we
   write almost none of it. `lighter_ticks.py` already does part.
2. **Strategy (brain)** — the "when to enter/exit" logic. OPTIONAL for the
   panel: in manual mode the human decides. This is what was loosely
   (wrongly) called "the core" mid-discussion. `WFStrategy` is an example.
3. **Panel (hands & eyes)** — UI over the infrastructure. Manual mode
   (human generates orders) and observe mode (shows strategy's decisions,
   human can intervene). NOT a separate driver — a layer over the testnet
   driver.

**Unification is the whole point**: one strategy implementation must run
in backtest (history), on testnet (live), and under the panel, WITHOUT
rewriting. Achieved via a "contract": fixed set of (a) market events into
the strategy, (b) intents out (place/cancel order, move stop), (c)
acknowledgements from the driver (accepted/filled/rejected). Half of this
already exists in the backtester.

**Build order — PANEL FIRST (user's explicit choice).** Engineering-"clean"
is contract-first, but the user needs the visible/tangible first, to
control that the assistant builds what he meant rather than trust numbers:
(1) SDK + testnet, place/cancel one order from code; (2) minimal panel on
a ready web framework (Streamlit/FastAPI) — buttons, position table,
balance, fills; user SEES and TOUCHES testnet trades; (3) unify with
backtest (the contract); (4) attach strategies in observe mode, user
eyeballs bot decisions vs chart.

Execution model (matches mrcvokka AND the existing backtester): entry =
limit (maker), target exit = limit (maker), emergency stop = market
(taker). This is NOT market-making (quoting both sides) — it's directional
trading with maker entry. MM deliberately NOT done (ndr: primitive MM on a
top venue loses by design against institutional MMs).

### Ingredients we build to accept (strategy direction stays OPEN)

Infrastructure is strategy-neutral. Which hypothesis to load is decided
separately, fed by a parallel learning chat. Backlog candidates, none
chosen: S/R levels from M5 + bounce entry (vs current Keltner zones);
CEX→DEX lead-lag (large market leads Lighter — confirmed as mrcvokka's own
"before/after" breakthrough); order book (flow, liquidity walls); Ross Hook
on range bars; spot-perp arb / forex pairs (USDCHF) as an idea inside a
strategy. Take-exit is the only current component with stable edge.

### Live-infra gotchas (forum + our practice, verified)

- **SDK is leaky**: `WsClient` does only order_book/account_all (raw WS
  for trades written by us); mark_price/index_price absent from Pydantic
  models (only in additional_properties); had a PEP-515 cursor bug. Verify
  every field against the live API, don't trust SDK models.
- **TypeScript/node.js SDK also exists** — some functionality may live
  there, not in Python. Check coverage gaps before working around missing
  bits.
- **Latency — MEASURED 2026-07-17, better than feared**: 243 ms to place a
  post-only limit, ~500 ms for a market order (open and close), 330 ms for
  a REST account read. The forum's ~600-1200 ms was pessimistic. Caveat:
  measured from the box the panel runs on during that session, and on a
  quiet testnet — not re-measured from Frankfurt under load, and "not in
  practice when the market's hot" still stands as a warning. Tolerable for
  range-bar scalping (not HFT).
- **Scaling (price/size decimals) MUST be read from `/api/v1/orderBooks`,
  never hardcoded.** Verified live 2026-07-17: ETH(0) 2/4, BTC(1) 1/5,
  SOL(2) **3/3**. `panel.py` had SOL hardcoded as 2/4 — every SOL order
  would have gone out at 1/10 the intended price and 10x the intended size
  — and listed markets 24/92 that testnet does not have. Testnet has 5
  markets: ETH(0), BTC(1), SOL(2) perps, plus ETH/USDC(2048) and
  LIT/USDC(2049) spot. `min_quote_amount` is $10 on all perps.
- **Position `sign`**: `+1` = LONG, `-1` = SHORT (verified against real
  open positions). Closing a position is therefore `is_ask = (sign > 0)`.
- **String-typed fields**: `Order.price`, `Order.remaining_base_amount` and
  the position fields (`position`, `avg_entry_price`, `unrealized_pnl`)
  come back as **strings, already scaled** (`"1846.57"`). Use `float()` —
  do not divide by ticks. The SDK's own models declare these `StrictStr`.
- **Market orders are IOC.** Priced at exactly top-of-book they do not fill
  on a one-tick move. Use `create_market_order_limited_slippage`, which
  reads top-of-book itself and applies slippage in the correct direction —
  don't hand-roll the price.
- **API key slot 0 belongs to the Lighter web UI — never put a bot there.**
  The UI lists it as "0 (Desktop)" and re-registers it, silently killing
  whatever key you had in that slot. Both live scripts hardcoded 0 and lost
  the key twice on 2026-07-17: it verified and traded, Ivan opened the
  Lighter UI, and the next run failed with "private key does not match".
  Two clients were fighting over one slot; it read like a bad paste, which
  cost most of the debugging. The slot now comes from
  `TESTNET_API_KEY_INDEX` in `.env` with **no default** (guessing 0 *is*
  the bug). Currently 4. To reissue: Lighter UI -> API Keys -> Refresh on
  **your** index — the private key shows **once**, copy it before clicking
  anything else; a second Refresh invalidates what you just copied.
- **API keys go stale, and `check_client()` says so in one line** ("private
  key does not match the one on Lighter", with both pubkeys). Run
  `python -m src.live.connect_testnet` before suspecting anything else —
  it is the cheapest health check in the repo. `/api/v1/apikeys` lists what
  is actually registered. Probe which slot a key belongs to with one
  process **per index**: the Go signer is a process-global singleton and
  indexes contaminate each other within one process (this produced a
  misleading result on 2026-07-17).
- **Ivan drives the account from the Lighter web UI too.** The panel is one
  of several hands on account 306, not the only one. Account state changing
  between runs (positions opened/closed, keys rotated) is usually him, not
  a bug. Ask before theorising.
- **`SignerClient.__init__` needs a running event loop** (it builds an
  aiohttp connector). Constructing it from sync code raises "no running
  event loop" — the root of the panel's two event-loop fix commits.
- **Reversal-close on a live DEX is an execution-tech-debt source**: slow
  network, queued orders, bot may count old orders as filled when they're
  not. Our backtest reversal-exit problems may ALSO be executional live —
  reason to test on testnet, not only history.
- **KEY SECURITY — critical at live**. Repo is PUBLIC. GitHub key/seed
  leaks are an epidemic. On moving to real keys (even testnet): keys in
  .env only, never in code; gitleaks is installed but verify by hand.
  Real money only after confirmed edge AND testnet survival.
- **THE PANEL HAS NO AUTHENTICATION AND IT TRADES.** Streamlit binds to
  every interface by default (`server.address` unset => 0.0.0.0). This VPS
  has a public IP and **no firewall** (iptables INPUT policy ACCEPT, zero
  rules), so the default publishes a working trading UI to the open
  internet. On 2026-07-17 it did exactly that for ~2 hours — Claude handed
  Ivan `streamlit run ... --server.port 8501` without thinking about the
  bind address, and only a later review caught it. `.streamlit/config.toml`
  now pins `address = "127.0.0.1"`; reach the panel over an SSH tunnel.
  Verify with `ss -ltnp | grep 8501` — it must read `127.0.0.1:8501`, never
  `*:8501`. A CLI `--server.address` overrides the file silently. **Before
  mainnet this needs real auth, or the panel does not go near real keys.**

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

## Session log (2026-07-17)

Testnet track, steps 1-2 reviewed and repaired. Ivan asked Claude to audit
the live panel against spec, believing DeepSeek had written it — the git
trailers said otherwise: all five `live:` commits are Claude's. DeepSeek's
work in this repo is `bias_audit.py` / `no_reversal_variant.py`. So this
was Claude reviewing Claude, which is exactly the case where the review has
to be adversarial rather than confirmatory.

**Four bugs found in `panel.py`, all fixed and verified live (`0b08c66`,
branch `feature/testnet-step1-connect`, pushed):**

1. Close Position sent the **wrong side** (`close_side = pos_sign < 0` →
   long closed with a BUY). `reduce_only` had the exchange reject it, so
   the panel showed a green tx hash while nothing happened. Nothing could
   be closed *from our panel* — Lighter's own UI was unaffected and is
   where Ivan actually manages positions.
2. Close Position priced its market order at **0** (`# will be overridden`
   — nothing did). IOC at price 0 → cancelled on arrival.
3. The Orders tab **crashed on any live order** — SDK returns price/size as
   already-scaled strings, code divided them by ticks. Hidden by an empty
   order list.
4. Per-market **decimals were hardcoded and had drifted** — SOL 2/4 vs a
   real 3/3 (1/10 price, 10x size on every SOL order). Now read from
   `/api/v1/orderBooks`; on an undescribed market the panel stops rather
   than defaulting.

**The pattern worth remembering: every one of these failed silently.**
`reduce_only` turned a wrong side into a quiet no-op, an empty list hid a
crash, a default hid the SOL error. The fixes remove the muffling too — the
panel now refuses to guess where it used to fall back.

**Verification method** (worth repeating): a scratchpad script mirrored the
panel's call sites literally, traded 0.01 ETH round-trip, and asserted the
OLD code failed *before* asserting the new code worked — the old `str / int`
TypeError and the inverted side were both reproduced live, so the fixes are
answers to demonstrated failures, not to plausible stories. Ivan's
pre-existing BTC/SOL shorts were guarded against and untouched; cost of the
whole exercise was $0.0035 in taker fees.

**Two process errors on Claude's side, both recorded above where they'll be
read:** (a) claimed twice it had no VPS access, quoting this file's stale
"What Claude CANNOT do" section instead of testing it — one `curl`
disproved it; (b) filed the hardcoded decimals as "tech debt, later" during
the static review, when it was in fact the most severe live bug of the four.
Severity guessed from the armchair was wrong; the API said so in one call.

Round-trip latency measured as a by-product — see the gotchas section. It
closes an open item that had been sitting since 2026-07-08.

**Later the same day: review pass, merge, and fills.** An external review
found four more things, three of them real and one of them serious:

- **The panel was on the open internet.** Streamlit binds 0.0.0.0 by
  default, this VPS has a public IP and no firewall, and the panel has no
  auth and trades. It was reachable at `http://<public-ip>:8501` for ~2
  hours — measured, not theorised: the still-running instance answered 200
  from outside while being fixed. Now loopback-only via
  `.streamlit/config.toml`. **Claude handed Ivan that run command without
  once thinking about the bind address.**
- **`int()` truncation** in the tick conversions: `0.29 * 100` is
  `28.999999999999996`, so a close of 0.29 went out as 0.28 and left dust.
  `round()` now, with hand-derived tests that pin the truncation premise
  itself.
- **Severity called wrong, again.** The review flagged the
  `int(s.replace(".", ""))` parsing as broken; measuring said latent — the
  exchange pads to exactly `decimals` today (322/322 values checked). Fixed
  anyway, since it rests on an undocumented courtesy, but the honest label
  is hardening, not a live bug. Worth noting the direction: this time the
  overstatement was in the review, and the earlier one (decimals as "tech
  debt") was Claude's. Measure, then rank.

Both tracks merged to `main` (245 + 21 = 266 tests, verified from a fresh
clone), and the Fills tab closed step 2's last spec item. The realized-PnL
derivation was hand-checked against Ivan's own closed shorts: SOL
(75.789 - 75.255) x 771.278 = 411.862452, matching the API exactly.

## Session log (2026-07-10)

Audit session. Two documented "facts" this file had been carrying turned out
to be wrong, and both were caught by measuring before acting.

- **Standing checklist created**: `docs/AUDIT_2026-07-10.md`, referenced from
  this file's header. It supersedes "Next steps" below wherever they disagree.
  Items carry IDs and statuses so they survive session boundaries — the
  problem this file itself keeps having.
- **A1 — the legacy retro-dedup was CANCELLED, not done, because its premise
  was false.** The plan was to dedup v1 files on `(t,p,s,side)`. Validating
  that key first — against v2 files where `tid` gives the real answer — showed
  it falsely collapses 9.4–17.1% of *genuine* trades: one sweep filling
  several resting orders prints several rows sharing ms/price/size/side. Three
  independent disproofs (clean v2 files collide at up to 17.1% with zero real
  dupes; neither the batch-repeat nor the run-length signature of duplication
  is present; the live collector logs `dropped_dup=0` on every market).
  The old "17.5% before / 0.0% after" compared `(t,p,s,side)` collisions to
  `tid` collisions — two different quantities. Running the script would have
  deleted ~10–17% of real BTC/ETH ticks. Tool + evidence:
  `src/collector/dupe_diagnostic.py` (12 tests). Claim retracted here, in
  Open questions, and in `lighter_ticks.py`'s docstring.
- **A4 — `range_size` was a constant fitted to one regime.** Per-day BTC
  calibration spans 8.9–16.1 (nearly 2×); every OOS run used 15.3, so Jul 4–5
  traded bars ~60–70% too large. Built rolling prior-day calibration
  (`src/rangebars/rolling.py`, `Replay(range_size_schedule=...)`,
  `--rolling-range-size`) — no lookahead, and unlike a constant it is
  something a live bot can compute. **Correcting the size did not rescue the
  result**: Jul 3–9 went from −0.3253 bps (t = −2.48) fixed to −0.3673 bps
  (t = −4.33) rolling, on 52% more trades. Under fixed sizing one day printed
  a positive mean; under rolling none does. The confound is closed and the
  negative verdict survives it. Full result:
  `docs/ROLLING_CALIBRATION_2026-07-10.md`.
  Free sanity check: `0.30 × mean_1m_range(Jul 2)` rounds to exactly 15.3, and
  Jul 3 comes out bit-identical between the two runs — the switching machinery
  does nothing when it should do nothing.
- **`config.yaml` deliberately NOT edited.** The finding is that a constant is
  the wrong *shape* for this parameter; writing a fresh constant would bury it.
  Only a comment was added, pointing at A4.
- **`PASS_FAIL_CRITERION.md` suspended, not amended.** It still named Jul 3–6
  as out-of-sample, though those days have been used three times to select
  between variants (bias audit, no-reversal variant, acceptance sweep). A
  banner now says so in the file someone opens right before running it. The
  calibration/validation/holdout re-split is Ivan's risk call, not Claude's,
  and is open as item A2.
- **Method note worth keeping**: a dedup key is a *hypothesis about identity*.
  Validating it on data that already has a real identity column costs minutes.
  The wrong number lived here for two sessions because nobody did that.

## Session log (2026-07-07)

Recorded retroactively on 2026-07-10 — this session's work was never written
into this file, which is itself an instance of the drift documented below.

- **Pass/fail criterion written down before the re-run** (`f6d689f`), per the
  previous session's Next-steps item 3. See its banner: now suspended.
- **Diagnostic bar/trade export** (`export_sample.py`, DeepSeek task file,
  merged `7ca3d02`) for eyeballing bars/trades outside the terminal.
- **Bias-regime empirical audit + no-reversal-exit variant** (`bias_audit.py`,
  `no_reversal_variant.py`, merged `a98cc14`). Results in
  `docs/BIAS_AUDIT_2026-07-07.md`. Headline: bias churn is high (30–50% of
  regimes last <5 bars) but only ~9–11% of entries land in one that fresh; and
  removing reversal exits entirely does NOT help — sessions collapse from
  1,370 to 30 in-sample and the stop dominates. The real question is which
  filter makes reversals selective, not whether to have them.
  **Caveat found 2026-07-10**: that document describes reversal exits as
  closing positions "by a bias flip". They do not — they close on a swing
  break. See AUDIT item S3 before reasoning from it.
- **`acceptance_bars` parameterized** in `swings.py` + `acceptance_variant.py`
  sweep (branch `feature/acceptance-window`, `4e3c068`). Widening
  `swing_confirm_bars` had made things worse, motivating a separate knob for
  how long a BREAK must hold, as distinct from how mature the swing POINT is.
- **`diag_take_vs_rest.py`** written to test Ivan's liquidity hypothesis
  (do take-exits differ from the rest in pre-entry volatility, participation,
  aggressor imbalance?). It sat **untracked** until 2026-07-10.

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
**Continued same day: swing-based trailing.**

- **Beggs' management principles read directly** from the primary-source
  course (`Курс_Ланса_Бегса_по_Price_Action.pdf`, project knowledge, not
  in repo), same discipline as the earlier reversal-rule fix. Confirmed:
  he always trades two parts opened at the SAME price (we scale in at
  two different limit prices — a known divergence, not changed here);
  target 1 is the next structural level, target 2 is either a further
  structural level or "just trail the price"; he explicitly trails to
  "peaks and troughs" (swing points), not bar-by-bar; he moves to
  breakeven aggressively and manages the trade actively rather than
  "set stop and forget". Only the swing-point trailing piece was in
  scope for this session — breakeven-transfer and the two-parts-same-
  price divergence are noted as open gaps, not yet acted on.
- **Also asked**: does mrcvokka's forum diary suggest anything for this?
  He describes a three-tier multi-timeframe setup — one higher timeframe
  for trend, and TWO separate lower timeframes, one for precise entry
  and a DIFFERENT one for position management. Our current design uses
  the same range-bar series (range_size=15.3) for both entry and
  management/exit. This is a real architectural question (a second,
  finer bar series specifically for management) but explicitly deferred
  — a forum diary is a lower-trust source than Beggs' own writing, and
  this is a bigger change than "add swing-trailing". Logged as a Next
  step, not acted on.
- **Work split to DeepSeek via a task-file workflow**: created
  `DEEPSEEK_TASK.md` (overwritten per-task, lives in the working branch)
  instead of re-explaining tasks in chat each time — Ivan points
  DeepSeek at the file, pastes back the resulting `git log` for review.
  First use: swing-based trailing spec (forbid `trailing=True` +
  `exit_mode="bar"`, add `trail_stop_swing()`, wire into `_maybe_trail`,
  test list). Branch `feature/swing-trailing`.
- **DeepSeek's implementation reviewed the same way Claude's own code
  is reviewed**: fresh clone of the branch (not trusting the pasted
  `git log` alone), full `pytest` run (174/174), full diff read line by
  line. DeepSeek correctly caught something the brief didn't explicitly
  ask for: two existing R-freeze regression tests
  (`test_r_multiple_frozen_*`) used `trailing=True` without
  `exit_mode="swing"`, which the new `ValueError` guard would now
  reject — DeepSeek added the missing `exit_mode="swing"` to both
  rather than leaving them broken. One minor, non-blocking style nit:
  `SwingTracker` is imported locally inside 4 new test functions instead
  of once at module top (harmless, not fixed, not worth a re-round-trip).
- **Independently re-derived the key test's numbers by hand** before
  approving: `test_trailing_wiring_integration`'s claimed
  `swing_low=95` was checked against `SwingTracker`'s own confirmation
  rule (candidate bar lower than the 2 bars before AND after) on the
  actual 5-bar fixture, not taken on the test author's word.
- **A process gap surfaced and was named, not silently patched over**:
  Ivan pushed back correctly when asked to `git push` after a commit —
  the DeepSeek task file's step 8 said "run pytest before your final
  commit" but never said "push". Fixed going forward: any task file for
  DeepSeek (or any agent) needs an explicit push step, not an implied
  one.
- **Merged to `main`** (`e4752a4`, no-ff), independently re-verified from
  a fresh clone: **174/174 passing**.
- **Not done this session** (deferred, see Next steps): re-run of
  swing-mode + swing-trailing on the same 4 BTC days, writing the
  pass/fail criterion before that re-run, breakeven-transfer, the
  two-parts-same-price divergence from Beggs, the second-timeframe
  management-tier idea from mrcvokka's diary, the out-of-sample split,
  the EMA-bias audit against Beggs, `config.example.yaml` drift,
  mean-holding-time instrumentation.

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
  `Deduper` semantics (14 tests). Verified: 0.0% dupes on new data.
  (The "vs 17-22% on legacy files" half of this claim was retracted
  2026-07-10 — it compared `tid` collisions to `(t,p,s,side)` collisions.
  See "Data on disk" above.)
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
2. ~~**Retro-dedup of legacy v1 files**~~ — **CANCELLED 2026-07-10.** The
   fallback key `(t, p, s, side)` was the whole problem: the "may collapse
   distinct trades with identical price/size at the same ms" caveat this
   item carried turned out to describe the DOMINANT effect, not a rounding
   error. Measured on clean v2 files, that key falsely collapses 9.4–17.1%
   of genuine trades — as much as the "duplicates" it was meant to remove.
   Meanwhile `dropped_dup=0` in the live logs shows there were never
   duplicates on `update/trade` to remove. Running the script would have
   deleted ~10-17% of real BTC/ETH ticks. Evidence + reproducible tool:
   `src/collector/dupe_diagnostic.py`, `docs/AUDIT_2026-07-10.md` item A1.
3. ~~Retro-dedup or fresh re-backfill?~~ — **moot**. Re-downloading from
   0xArchive with the fixed cursor handling would still hit the same
   burst-undercount ceiling; it's a source limitation, not a pagination
   bug. No further 0xArchive downloads planned.
4. **Cosmetic**: `RuntimeError: Event loop stopped before Future completed`
   at systemd stop. Not a data loss, just noisy in logs. Not fixed yet.
5. ~~Does `_LOOP` survive a Streamlit rerun?~~ — **no, and fixed 2026-07-17
   (`d2d648e`).** It did not: Streamlit re-executes the script per rerun, so
   the module-level `asyncio.new_event_loop()` built a new loop per click
   while `@st.cache_resource` kept the first render's `SignerClient`, whose
   aiohttp session stayed bound to the original loop. Second click died with
   "Timeout context manager should be used inside a task". The loop is now
   cached like the client, so their lifetimes match. Reproduced outside
   Streamlit before fixing (client on loop A, called from loop B → the
   identical error; loop A again → OK), then **confirmed by Ivan in the
   real panel — Refresh works repeatedly** (the repro only proved the
   mechanism; the click proved the panel).
   **Worth keeping:** commits d59bef1 and a7d183f both claimed to have
   "fixed the event loop" and neither could have — they addressed
   construction on the *first* render and never touched the lifetime
   mismatch, which only shows up on the *second* interaction. A panel that
   loads cleanly proves nothing; the bug lives one click deeper. Nothing in
   the repo could have caught this — it took a human clicking twice.
6. ~~Are Ivan's testnet BTC/SOL shorts stuck?~~ — **not a question; asked
   and answered 2026-07-17.** Ivan opened both by hand in Lighter's own web
   UI, deliberately, and manages them there. Claude had inferred "the
   panel's Close button can't close these, so maybe they're stuck" — a
   guess built on the unstated assumption that the panel is the only way
   Ivan touches the account, when in fact he had been working in the
   exchange's own UI all along. Recorded because the assumption is likely
   to recur: **the panel is one of several hands on this account, not the
   only one.** Any future "the account state looks odd" reasoning should
   start by asking Ivan what he did in the Lighter UI. Both positions stay
   untouched.

## Next steps (priority order)

**Live/testnet track (as of 2026-07-17)** — runs in parallel with the
backtest list below; real money still gated on backtest edge + testnet.

0. ~~Step 2 is not done — fills missing~~ — **built 2026-07-17** (78ed3f4).
   The Fills tab shows side, role, price, size and realized PnL per fill,
   all derived from the account index rather than taken on trust (the API
   describes a trade, not our part in it). Step 2's spec is met. Its
   rendering is still unclicked — see the standing rule in item 1.
1. ~~Have a human run the panel and click twice~~ — **done 2026-07-17**;
   it found the `_LOOP` lifetime bug (open question 5), which no amount of
   reading had. Keep the habit: after any panel change, click twice. The
   first render is not evidence.
2. **Instrument round-trip in the panel itself.** One-off numbers exist now
   (243/500 ms, see gotchas); a running measurement under real use is what
   tells us whether range-bar scalping survives on this venue.
3. **Consider a typed wrapper over the SDK before step 3 (the contract).**
   Every 2026-07-17 bug lived where the panel called the SDK directly and
   trusted its shapes; the code that bypassed the SDK (direct-HTTP account
   read) was the code that worked. Unifying strategy across backtest and
   live on top of a leaky SDK, with no wrapper to pin types and scaling at
   the boundary, invites the same class of bug into the strategy layer.

**Backtest track.** The warning below applies to THIS list only —
the live track above is current.

> **STALE as of 2026-07-10. The live list is `docs/AUDIT_2026-07-10.md`.**
> Where the two disagree, the audit wins. Specifically: item 4 below
> (out-of-sample split) is now audit item A2 and is *blocking*, not pending;
> item 5 (EMA-bias audit) was done on 2026-07-07; the retro-dedup this file
> used to call for was cancelled outright (audit A1); and `range_size` is no
> longer a single constant (audit A4). The list below is kept because items
> 6–8 have not been superseded by anything.

As of the 2026-07-06 session (trailing redesign now done). NOTE:
unchanged since 2026-07-08 — the scope expansion moved attention to the
live track, not because these were resolved:

1. ~~Trailing redesign~~ — **done 2026-07-06**. Swing-based
   (`trail_stop_swing`), merged to `main` (`e4752a4`), 174/174 passing.
   `trailing=True` now requires `exit_mode="swing"`.
2. **Re-run `exit_mode="swing"` + swing-based trailing** on the same 4
   real BTC days (June 29 – July 2, range_size=15.3). This will be the
   first headline number where none of the three known measurement
   distortions (R-freeze, reversal-exit friction, raw bar trailing) are
   still in the way. Not yet run.
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
6. **Breakeven-transfer** (new, from 2026-07-06 Beggs reading). Beggs
   moves stops to breakeven aggressively as a distinct management step,
   separate from trailing. Not mechanized at all currently — the stop
   stays at the 0-line (or the swing trail) until it happens to cross
   breakeven on its own. Not yet scoped or built.
7. **Two-parts-at-the-same-price divergence** (new, from 2026-07-06
   Beggs reading). Beggs always opens both parts at ONE price; our
   mechanization (via mrcvokka's adaptation) scales in at two different
   limit prices (¼ and ½ lines). This is an inherited design choice, not
   a bug, but worth naming as a known divergence from the primary
   source. Not being changed now.
8. **Second-timeframe management tier** (new, from mrcvokka's forum
   diary, 2026-07-06 — lower-trust source than Beggs, treat as a
   hypothesis). mrcvokka describes 3 tiers: one higher timeframe for
   trend, and TWO separate lower timeframes — one for precise entry,
   a DIFFERENT one for position management. We currently use the same
   range-bar series (range_size=15.3) for both entry and
   management/exit (including the new swing trail). A second, finer
   bar series specifically for management is a real architectural
   question but a bigger change than swing-trailing was — explicitly
   deferred until the re-run in item 2 gives a number to react to.
9. **Lower priority**: fix `config.example.yaml` drift (`market_ids:
   [0,1]` vs. the live 5-market set; BTC placeholder 5.00 vs. calibrated
   15.3); add mean-holding-time instrumentation to metrics (needed to
   check, not just assume, whether swing-mode's longer holds make the
   funding=0 assumption stop being safe); collect real funding-rate
   history if that instrumentation says it matters.

## Repo structure

Counts below were read off `pytest --collect-only` on 2026-07-10 at commit
`9673587` (branch `feature/acceptance-window`), not copied forward. When you
update this block, measure — don't increment.

```
src/
  collector/   lighter_ticks.py        ✅ v2 schema, dedup by tid, snapshot skip
               list_markets.py         ✅ helper
               oxarchive_backfill.py   ✅ historical backfill + SDK bugfix
               compare_sources.py      ✅ cross-source cross-check
               dupe_diagnostic.py      ✅ real dupes vs key-collision artifact
                                          (2026-07-10 — why retro-dedup was cancelled)
  rangebars/   builder.py              ✅ + tests
               calibrate.py            ✅ 30% of mean 1m range + tests
               rolling.py              ✅ prior-day range_size schedule, no
                                          lookahead (2026-07-10, AUDIT A4)
  indicators/  ema.py                  ✅ streaming, seed from first price
               keltner.py              ✅ shared core, mult 4 & 8
               stochastic.py           ✅ slow 3/2/3
  backtest/    replay.py               ✅ slice 1: harness, dual series +
                                          optional range_size_schedule (2026-07-10)
               orders.py               ✅ slice 2: fill engine + slippage/fill-prob
               strategy.py             ✅ slice 3+4: WF strategy + trailing +
                                          exit_mode ("bar" default / "swing" fix) +
                                          R-freeze fix + reversal-via-FillEngine +
                                          swing-based trailing (2026-07-06)
               swings.py               ✅ swing HH/HL + break-acceptance (2026-07-05),
                                          acceptance_bars parameterized (2026-07-07)
               costs.py                ✅ slice 4: fees + funding
               metrics.py              ✅ slice 5: bps/R stats, breakdowns
               run_backtest.py         ✅ slice 5: CLI runner + --rolling-range-size
               export_sample.py        ✅ bar/trade export for visualization (07-07)
               bias_audit.py           ✅ bias-regime empirical audit (07-07)
               no_reversal_variant.py  ✅ reversal exits disabled (07-07)
               acceptance_variant.py   ✅ acceptance_bars sweep (07-07)
               rolling_variant.py      ✅ rolling vs fixed range_size (07-10)
  live/        connect_testnet.py      ✅ step 1: connect, place & cancel one
                                          order. Also the fastest health check
                                          in the repo — run it first when the
                                          key looks broken.
               panel.py                ⚠️  step 2: Streamlit panel. NO AUTH and
                                          it trades — loopback only, via
                                          .streamlit/config.toml, reach it over
                                          an SSH tunnel. Order paths fixed and
                                          verified live 2026-07-17. Tabs:
                                          Trading, Orders, Order Book, Fills.
                                          Step 2 spec is now met.
                                          Tests cover the tick conversions
                                          only; the panel itself has none
                                          (Streamlit script, cannot be
                                          imported) — its rendering is only
                                          ever proven by a human clicking.
diag_take_vs_rest.py                   ✅ pre-entry conditions, take vs rest (07-07)
data/ticks/    JSONL, mixed v1 (legacy) and v2 (post-2026-07-02)
docs/
  ytc_scalper_skeleton.md              strategic breakdown of Beggs
  kb_mrcvokka_diary.md                 full research, 19 pages, forum diary
  PASS_FAIL_CRITERION.md               provisional gate — SUSPENDED, see its banner
  BIAS_AUDIT_2026-07-07.md             bias-regime audit + no-reversal variant
  AUDIT_2026-07-10.md                  ← THE live open-items checklist
  ROLLING_CALIBRATION_2026-07-10.md    rolling vs fixed range_size result
scripts/lighter-ticks.service          template (real unit in /etc/systemd/system/)
tests/
  test_strategy.py                     43     test_collector.py             14
  test_orders.py                       25     test_dupe_diagnostic.py      12
  test_live_conversions.py             21     test_export_sample.py        11
  test_costs.py                        19     test_calibrate.py            10
  test_rolling.py                      18     test_run_backtest.py          6
  test_replay.py                       17     test_indicators_{ema,keltner,stochastic}.py  6 each
  test_bias_audit.py                   15     test_acceptance_variant.py    4
  test_swings.py                       14     test_no_reversal_variant.py   3
  test_metrics.py                      14     test_rangebars.py             2
  -- total: 266, measured 2026-07-17 on the VPS after merging both tracks --

  NOTE on src/live/: `test_live_conversions.py` pins the tick<->price/size
  arithmetic and nothing else. The panel itself is untested — it is a
  Streamlit script whose module level calls st.*, so it cannot be imported.
  Every one of the 2026-07-17 panel bugs was invisible to pytest and was
  caught by reading the code, calling testnet, or Ivan clicking twice. A
  green suite says nothing about the live track.
```
.streamlit/config.toml                 binds the panel to 127.0.0.1 — it has
                                       no auth and it trades; reach it over an
                                       SSH tunnel, never by exposing the port
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

# ── live / testnet track ──────────────────────────────────────────────

# step 1: connect, place & cancel one order (also the fastest health check —
# if the API key went stale, this says so in one line)
python -m src.live.connect_testnet

# step 2: the panel — loopback only, reach it over an SSH tunnel.
# .streamlit/config.toml pins server.address to 127.0.0.1. Do NOT pass
# --server.address, and do NOT "just for a minute" bind 0.0.0.0: the panel
# has no auth and trades. On the VPS:
streamlit run src/live/panel.py
# then from the laptop, separate terminal:
#     ssh -N -L 8501:127.0.0.1:8501 root@<vps>
#     open http://localhost:8501

# confirm it is not exposed (must show 127.0.0.1:8501, never *:8501)
ss -ltnp | grep 8501

# kill it by port — never `pkill -f streamlit`, that matches any command
# line containing the word, including your own shell one-liner
fuser -k 8501/tcp

# which api_key_index does the key in .env belong to? (they go stale)
python -c "
import asyncio, os
from dotenv import load_dotenv; load_dotenv()
from lighter import SignerClient
pk = os.getenv('TESTNET_PRIVATE_KEY','').removeprefix('0x')
async def probe():
    for idx in (0, 1, 2, 3, 4):
        try:
            c = SignerClient(url='https://testnet.zklighter.elliot.ai',
                             account_index=306, api_private_keys={idx: pk})
            print(idx, c.check_client() or 'OK - key matches')
            await c.close()
        except Exception as e:
            print(idx, 'EXC', str(e)[:60])
asyncio.run(probe())"

# pubkeys actually registered on the account
curl -s "https://testnet.zklighter.elliot.ai/api/v1/apikeys?account_index=306&api_key_index=255"

# account / positions (note by=index, NOT by=account_index)
curl -s "https://testnet.zklighter.elliot.ai/api/v1/account?by=index&value=306"

# per-market decimals — the source of truth, never hardcode these
curl -s "https://testnet.zklighter.elliot.ai/api/v1/orderBooks"
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

**Read the surface first.** This list was written for Claude in the **web
chat**, where all of it is true. In a **Claude Code** session on the VPS
none of the first three hold: it has a shell, the repo, `.venv`, `.env`,
and the network. Verified 2026-07-17 — it reached testnet directly (~330 ms
on a REST GET), placed and cancelled real orders, and closed a position.
Check the capability instead of quoting this list at the user; on
2026-07-17 Claude told Ivan "run it yourself, I have no access" twice
before a single `curl` disproved it.

In the **web chat**:
- No VPS access — all commands go through the user.
- No live GitHub repo access — the user brings a snapshot.
- Cannot create the GitHub repo under the user's account.

In **both**:
- No long-term memory across chats, hence this file.
- Cannot click a Streamlit UI. Panel code paths can be exercised by calling
  them directly, but rendering and the event-loop behaviour need a human to
  run `streamlit run` and click twice.
