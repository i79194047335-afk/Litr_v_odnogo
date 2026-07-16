#!/usr/bin/env python3
"""Patch CONTEXT.md for the scope expansion (backtest-only -> backtest +
testnet live infrastructure). Run from repo root. Fails loudly if any
anchor doesn't match exactly once — does not leave a partial edit."""
import sys

with open('CONTEXT.md', 'r', encoding='utf-8') as f:
    content = f.read()

patches = [

# 1. Scope line in TL;DR
("""crypto/trading and is deliberately taking the safe path: **tick collection →
backtest → paper trading**. No live-trading code is in scope until backtest
shows edge. This is a learning/research project, not financial advice.""",
"""crypto/trading and is deliberately taking the safe path: **tick collection →
backtest → paper/testnet → live**. SCOPE EXPANDED 2026-07-08: live trading
CODE is now in scope, but ONLY against Lighter **testnet**
(https://testnet.zklighter.elliot.ai). Real-money mainnet trading remains
out of scope until a strategy shows edge in backtest AND survives testnet.
This is a learning/research project, not financial advice."""),

# 2. Work plan tail (items 10-11)
("""10. **Only after positive backtest**: paper trading on live WS stream.
11. **Only after successful paper**: talk about live (not in scope now).""",
"""10. **Testnet infrastructure track (NEW, 2026-07-08)**: panel + connector
    infrastructure against Lighter testnet, in parallel with (not
    replacing) the backtest track. See "Scope expansion & architecture"
    section below. Real money still gated on backtest edge + testnet
    survival.
11. **Only after positive backtest AND successful testnet**: real-money
    mainnet, small size, keys secured. Not before."""),

# 3. New section before Anti-patterns
("""## Anti-patterns — do NOT repeat""",
"""## Scope expansion & architecture (2026-07-08)

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
- **Latency**: VPS in Frankfurt, Lighter datacenter in Oceania. Real order
  round-trip ~600-1200ms (not the advertised 300ms); sub-10ms matching is
  theory, "not in practice, especially when the market's hot". Probably
  tolerable for range-bar scalping (not HFT), but measuring real testnet
  round-trip is an early task.
- **Reversal-close on a live DEX is an execution-tech-debt source**: slow
  network, queued orders, bot may count old orders as filled when they're
  not. Our backtest reversal-exit problems may ALSO be executional live —
  reason to test on testnet, not only history.
- **KEY SECURITY — critical at live**. Repo is PUBLIC. GitHub key/seed
  leaks are an epidemic. On moving to real keys (even testnet): keys in
  .env only, never in code; gitleaks is installed but verify by hand.
  Real money only after confirmed edge AND testnet survival.

## Anti-patterns — do NOT repeat"""),
]

for i, (old, new) in enumerate(patches, 1):
    n = content.count(old)
    if n != 1:
        print(f"PATCH {i} FAILED: found {n} matches (need exactly 1)",
              file=sys.stderr)
        sys.exit(1)
    content = content.replace(old, new)
    print(f"Patch {i}: OK")

with open('CONTEXT.md', 'w', encoding='utf-8') as f:
    f.write(content)

print("All patches applied.")
