# Litr_v_odnogo — project context / handoff

> Read this file end-to-end when resuming work in a new chat. For deeper
> research see `docs/ytc_scalper_skeleton.md` and `docs/kb_mrcvokka_diary.md`.

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
7. **Indicators**: EMA, Keltner (NT formula:
   `centerline = SMA(close,35); band = centerline ± mult * SMA(H-L,35)`),
   Stochastic (3,2,3) — all with unit tests.
8. **Event-driven backtester**:
   - Load ticks → build range bars → indicators → strategy.
   - Execution model: limit orders (maker, fee=0 on Lighter), but **fill
     rate** must be modeled (Beggs notes part-2 orders often don't fill).
   - Tick-level slippage, perp funding rate.
9. **Metrics**: expectancy in R, win-rate, profit factor, max DD, part-2
   fill rate, walk-forward, parameter sensitivity.
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

## Repo structure

```
src/
  collector/   lighter_ticks.py        ✅ v2 schema, dedup by tid, snapshot skip
               list_markets.py         ✅ helper
               oxarchive_backfill.py   ✅ historical backfill + SDK bugfix
               compare_sources.py      ✅ cross-source cross-check
  rangebars/   builder.py              ✅ + tests
  indicators/  (empty, next step)
  backtest/    (empty, next step)
data/ticks/    JSONL, mixed v1 (legacy) and v2 (post-2026-07-02)
docs/
  ytc_scalper_skeleton.md              strategic breakdown of Beggs
  kb_mrcvokka_diary.md                 full research, 19 pages, forum diary
scripts/lighter-ticks.service          template (real unit in /etc/systemd/system/)
tests/
  test_rangebars.py                    2 tests, passing
  test_collector.py                    14 tests, passing
.pre-commit-config.yaml                gitleaks local
.github/workflows/gitleaks.yml         gitleaks server-side
config.example.yaml                    reference
config.yaml                            local, not in git
CONTEXT.md                             this file
```

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