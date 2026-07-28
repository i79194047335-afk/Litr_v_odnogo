# Microstructure research pipeline — design

Status: **design + step 1 only**. Written 2026-07-24 after the range-bar /
swing track was shown to have no exploitable edge at the one scale it was
ever tested on. This document is the plan; only the WS probe (step 1 of the
capture layer) exists in code so far.

## Why this exists — the reframe

Everything before this treated Lighter like a forex OHLC feed: build range
bars from **trade prints**, find swings, overlay Keltner. That threw away
the thing a CLOB DEX actually gives you — the generating process. Price on
an order-book venue is a *derivative* of book state, order submissions,
cancellations, and (forced) liquidations. We were analysing the shadow.

Confirmed by inspection 2026-07-24: the live collector subscribes to exactly
one channel, `trade/{market}`, and writes only `{p, s, t, side, tid}`. The
book, cancels, and liquidations are **not being captured**. So the two
months of negative results were not just a bad parameter choice — they were
run on the only data we had, which was the least informative layer.

This does not overturn the negative results. It reframes them: *price-derived
features at one scale showed no edge.* Whether **book-derived** features
predict short-horizon price is an entirely separate, untested question.

## Data layers, generating-process first

```
L3  individual orders: add / cancel / modify        cancels, spoofing, icebergs
L2  book levels: price+size per side, as deltas      imbalance, pressure, depth
    liquidations (if the venue publishes them)        forced flow
    trades (prints: aggressor side, size)   <-- WE HAVE THIS, AND ONLY THIS
    funding / mark / index
—————————————————————————————————————————————————————
price = derivative of all of the above
```

## The pipeline — 6 layers, boundaries are law

Same discipline as the trading architecture: layers decoupled, or every
change is a rewrite.

### 1. Capture
Record raw events losslessly, append-only, with the server timestamp:
L2 book deltas, trades (already done), liquidations / L3 as available. No
processing at this layer. **This is what does not exist yet and must run
first** — nothing downstream can touch real data until the book has been
recorded for a while (days, not minutes).

Open schema questions (answered by the WS probe, not by guessing):
- first frame a full snapshot? later frames deltas or full snapshots?
- levels per side; exact price/size field names
- sequence/offset number present? (needed to detect gaps on reconnect —
  a gap silently corrupts every reconstructed book after it)
- liquidations: separate channel/type, or just trades?
- per-update server timestamp present?

### 2. Replay / reconstruction
From raw deltas, deterministically rebuild book state at any timestamp
(event sourcing). Emits, per event, a consistent snapshot: best bid/ask,
depth ladder, mid, microprice. Every feature is computed from this, so it
must be exactly right — a mis-applied delta poisons everything after it.
Reconstruction must be tested against periodic full snapshots if the venue
sends them.

### 3. Features (all causal, no lookahead)
Per decision point (each trade, each book update, or a fixed time grid):
- **queue imbalance** `(bid_sz - ask_sz)/(bid_sz + ask_sz)` at best levels —
  next-tick direction predictor.
- **OFI** (order-flow imbalance; Cont–Kukanov–Stoykov) — signed change in
  touch size combined with price moves. The best-grounded short-horizon
  predictor in the literature.
- **microprice − mid** — size-weighted fair price vs midpoint; a standing
  lean toward one side.
- **depth slope** — how fast cumulative size grows away from touch (thin vs
  thick book).
- **cancel rate at touch** — proxy for ephemeral/spoof liquidity (needs L2
  deltas or L3). Liquidity that vanishes as price approaches was never
  liquidity.
- **trade-flow imbalance** — signed aggressor volume. We already have the
  aggressor from `is_maker_ask`, so no Lee–Ready tick rule needed.

### 4. Labels (what we predict)
- **markout** — mid move at +Δt (e.g. 100 ms, 1 s, 5 s). The target.
- **adverse selection on a passive fill** — after a maker fill at touch,
  where does mid go next? This directly measures the project's own finding
  (a limit fill means the level was consumed → directional pressure tends to
  continue). For a maker-entry strategy this is *the* label: markout of the
  fill itself.

### 5. Evaluation
Does feature X predict label Y **out of sample**? Information coefficient
(rank correlation of feature vs forward return), train/test split fixed by
day **before** looking (same discipline as PASS_FAIL_CRITERION). A feature
only earns a strategy after it shows stable IC across the held-out days.

### 6. Strategy / backtest
Reached only if layer 5 produces a real signal. The existing FillEngine and
backtester slot in here unchanged.

## Three microstructure traps (not present in the forex framing)

1. **Bid-ask bounce.** Price hops bid↔ask even in a pure random walk with a
   spread, creating **spurious mean reversion** at short horizons. This is
   exactly the "we compute like forex" error: compute on **mid/microprice**,
   never on raw prints, or every markout and variance-ratio is wrong.
2. **Adverse selection is structural, not incidental** — already established
   by the project's own audit. Passive-fill markout will be negative by
   default; the question is not *whether* it's negative but *whether there
   are book states where it's less negative.*
3. **Cancellations ≠ liquidity.** Size that disappears as price approaches
   was never real. Cancel rate is a feature in its own right, not noise.

## Sequencing — bottom-up build, top-down design

1. **[in code] WS probe** (`ws_probe_book.py`) — discover the real book /
   liquidation schema from live frames.
2. Extend the collector to capture L2 (+ liquidations if present) to disk,
   alongside the existing trade capture. Let it run.
3. Build the replay/reconstruction layer; test it against snapshots.
4. Feature + label layers on reconstructed book.
5. IC evaluation with a fixed day split. **Decision point:** does any
   book-derived feature predict markout out of sample? If no across all of
   them — the venue is efficient at our latency and this whole direction is
   closed, which is a real answer. If yes — that feature defines the
   strategy.
6. Strategy in the existing backtester, then testnet, then live.

## What this does NOT prejudge
The CEX→DEX lead-lag hypothesis (mrcvokka's actual breakthrough) is a
*second data feed*, orthogonal to this. It can be added as another feature
source at layer 3 (a lagged CEX mid as an input) once capture exists. It is
not either/or with book microstructure — the pipeline holds both.
