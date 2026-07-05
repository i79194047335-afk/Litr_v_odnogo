"""
Slice 3 of the backtester: the WF (with-flow) strategy.

This is the mechanized core of Beggs' YTC Scalper as agreed in
docs/ytc_scalper_skeleton.md, wired to the Slice-1 harness (replay.py) and
the Slice-2 fill engine (orders.py). Decisions fixed in review before coding:

  BIAS (two-condition, neutral zone is a feature):
      bull:  ema_fast > ema_slow  AND  last_closed_1m.close > ema_fast
      bear:  ema_fast < ema_slow  AND  last_closed_1m.close < ema_fast
      else:  None -> do not trade, cancel resting entries.

  LINES (why mults 4 and 8 give evenly spaced lines: inner = outer/2):
      bull flow, bottom->top:  0 = c - mo*r, 1/4 = c - mi*r, 1/2 = c,
                               3/4 = c + mi*r, 1 = c + mo*r
      bear flow mirrors top->bottom. (c = Keltner centerline, r = SMA(H-L))

  ENTRY: two resting limits while flat and bias is set — part1 at 1/2,
      part2 at 1/4 (scale-in). GUARD: an entry limit is only placed if it is
      genuinely resting (buy strictly below last tick price / sell strictly
      above). A line on the wrong side of the market is skipped this bar —
      it will be reconsidered at the next bar's refresh. (Placing a
      marketable limit into our through-not-touch engine would fill it at
      the limit price later, which is semantically wrong.)

  STOP: static, at the 0-line AS OF the first entry fill. Parameter-free
      reading of "stop past the 0-line": the stop rests exactly AT line 0 —
      touching it means the flow structure broke. Covers the total held
      size; resized (same price) when part2 joins.

  TRAILING (Slice 4, optional, default OFF — see costs.py's sibling for the
      same "default neutral" pattern): the diary's mrcvokka reports a big
      PnL improvement from trailing but never states a concrete rule — it's
      an anecdote ("trailing gave x5"), not a spec (checked the diary
      directly, 2026-07-04: no formula anywhere, only the qualitative
      claim). The rule below is an engineering choice, not an extraction:
          long:  new_stop = max(current_stop, last_closed_bar.low)
          short: new_stop = min(current_stop, last_closed_bar.high)
      i.e. trail to the most recently closed range bar's low/high, TIGHTEN
      ONLY — a bar whose low fell below the current stop must never loosen
      it. Uses range-bar structure directly rather than inventing a new
      distance parameter (no ATR multiplier, no fixed offset). Checked on
      every closed bar while in a position, after the reversal check (so a
      bar that already triggered a reversal exit doesn't also trail into
      an empty position).

  EXITS:
      part1 -> take-profit limit at the 3/4 line as of entry.
      part2 -> exit condition depends on `exit_mode` (added 2026-07-05):

        exit_mode="bar" (DEFAULT, Slice 3 original): market exit at the
          close of the first OPPOSITE range bar (long: close < open; short:
          close > open). Cheap to compute, but on real data this fires on
          97%+ of trades because a single opposite bar happens roughly every
          2 bars by construction (verified against a fair-coin baseline) —
          it is NOT what Beggs describes as a reversal. Kept as the default
          ONLY so every existing test and prior result stays reproducible;
          not recommended for new runs.

        exit_mode="swing" (Beggs-faithful, see swings.py): exit only when
          the swing structure actually breaks AND price accepts the break
          for one further bar (see swings.py's module docstring for the
          full two-stage rule and its sourcing from the uploaded articles).
          Requires a SwingTracker fed alongside the Keltner core.

      SIMPLIFICATION (documented, either exit_mode): an opposite-bar or
      accepted-break signal closes the ENTIRE remaining position, part1
      included if its take hasn't filled yet. Beggs holds part1 to target;
      "reversal closes everything" is the conservative single rule.
      A stop fill flattens the ENTIRE position at the stop's fill price —
      if a gap tick fills an entry and the stop simultaneously, the stop
      wins and everything is out at the gapped price (gap honesty).

  ORDER REFRESH (per range-bar close, only while flat):
      bias set   -> cancel old entry limits, re-place at the fresh lines
      bias None  -> cancel entry limits, stay out
      While in a position entry limits are NOT re-placed; an unfilled part2
      either fills at its original price or dies with the exit cleanup.

  SIZING: 1 unit per part (part_size). PnL is recorded per part in price
      points and in R, where risk = |part entry - stop price|. Money sizing
      (risk_per_trade) arrives with the costs/metrics slices — expectancy
      in R does not need it.

Event wiring (order guaranteed by replay.py):
      on_tick        -> fill engine first: entries/stop/take react to ticks
      on_range_bar   -> keltner update, reversal check, entry refresh
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.backtest.replay import Replay
from src.backtest.orders import FillEngine, Fill
from src.backtest.swings import SwingTracker, check_break_and_acceptance
from src.indicators.keltner import KeltnerCore

Bias = Literal["bull", "bear"]


# --- pure functions (unit-tested directly) -----------------------------------

def compute_bias(ema_fast: float | None, ema_slow: float | None,
                 last_close: float | None) -> Bias | None:
    """Two-condition bias with a neutral zone. None in any input -> None."""
    if ema_fast is None or ema_slow is None or last_close is None:
        return None
    if ema_fast > ema_slow and last_close > ema_fast:
        return "bull"
    if ema_fast < ema_slow and last_close < ema_fast:
        return "bear"
    return None


def trail_stop(side: Literal["long", "short"], current_stop: float,
               bar_low: float, bar_high: float) -> float:
    """Tighten-only trailing candidate from one closed range bar.

    long:  candidate = bar_low,  only ever moves the stop UP (max).
    short: candidate = bar_high, only ever moves the stop DOWN (min).
    A candidate on the wrong side of the current stop leaves it unchanged —
    this function can never loosen a stop, by construction (max/min, not
    unconditional assignment).
    """
    if side == "long":
        return max(current_stop, bar_low)
    return min(current_stop, bar_high)


def zone_lines(centerline: float, range_sma: float,
               mult_inner: float, mult_outer: float, bias: Bias) -> dict[str, float]:
    """Map the two Keltner channels to the 0/quarter/half/3q/1 lines.

    Keys: "0", "q" (1/4), "h" (1/2), "tq" (3/4), "1".
    """
    inner = mult_inner * range_sma
    outer = mult_outer * range_sma
    if bias == "bull":
        return {"0": centerline - outer, "q": centerline - inner,
                "h": centerline, "tq": centerline + inner,
                "1": centerline + outer}
    return {"0": centerline + outer, "q": centerline + inner,
            "h": centerline, "tq": centerline - inner,
            "1": centerline - outer}


# --- trade record -------------------------------------------------------------

@dataclass(frozen=True)
class Trade:
    side: Literal["long", "short"]
    tag: str                    # "part1" | "part2"
    size: float
    entry_price: float
    entry_ts: int
    exit_price: float
    exit_ts: int
    exit_reason: Literal["take", "stop", "reversal"]
    r_multiple: float           # signed, risk = |entry - stop at entry|
    risk: float = 0.0           # |entry - stop at entry|, in price units — stored
                                 # explicitly (not just implied by r_multiple) so
                                 # Slice 4 cost functions can recompute net R
                                 # without dividing by a possibly-zero r_multiple.


# --- the strategy --------------------------------------------------------------

class WFStrategy:
    """With-flow two-part scalp. Plug into Replay.run(ticks, strategy)."""

    def __init__(self, replay: Replay, engine: FillEngine,
                 keltner_period: int = 35,
                 mult_inner: float = 4.0, mult_outer: float = 8.0,
                 part_size: float = 1.0, trailing: bool = False,
                 exit_mode: Literal["bar", "swing"] = "bar",
                 swing_confirm_bars: int = 2):
        self.r = replay
        self.e = engine
        self.keltner = KeltnerCore(keltner_period)
        self.mult_inner = mult_inner
        self.mult_outer = mult_outer
        self.part_size = part_size
        self.trailing = trailing
        self.exit_mode = exit_mode
        self.swings = SwingTracker(swing_confirm_bars) if exit_mode == "swing" else None
        self._break_pending = False

        self._last_price: float | None = None
        self._lines: dict[str, float] | None = None       # as of last bar close
        self._entry_ids: dict[str, int] = {}              # tag -> order id
        self._stop_id: int | None = None
        self._take_id: int | None = None
        # open position parts: [{tag, entry_price, entry_ts, size}]
        self._pos: list[dict] = []
        self._side: Literal["long", "short"] | None = None
        self._stop_price: float | None = None             # fixed at first fill
        self.trades: list[Trade] = []

        # Slice 5 instrumentation, corrected 2026-07-04: session outcomes are
        # tracked as three DISTINCT categories, not one binary "part2 joined"
        # flag. A binary flag conflates "part1 THEN part2 joined" (genuine
        # scale-in) with "part1 never filled, ONLY part2 filled alone" (price
        # already passed part1's level before the next bar's resting-order
        # refresh could place it — see _refresh_entries' resting guard). On
        # real data these turned out to be wildly different in size (13 vs
        # 666 out of 2909 sessions) — a single "part2 fill rate" number was
        # actively misleading about which one was happening.
        self._part1_joined = False
        self._part2_joined = False
        self.n_sessions = 0
        self.n_sessions_part1_only = 0
        self.n_sessions_part2_only = 0
        self.n_sessions_both = 0

    # --- helpers ----------------------------------------------------------------

    def bias(self) -> Bias | None:
        # warm-up gate: seed-biased EMA values are not signal (see ema.py).
        if not (self.r.ema_fast.ready and self.r.ema_slow.ready):
            return None
        lc = self.r.last_closed_minute
        return compute_bias(self.r.ema_fast.value, self.r.ema_slow.value,
                            lc.close if lc else None)

    def _record(self, part: dict, exit_price: float, exit_ts: int,
                reason: str) -> None:
        risk = abs(part["entry_price"] - self._stop_price)
        if self._side == "long":
            pnl = exit_price - part["entry_price"]
        else:
            pnl = part["entry_price"] - exit_price
        r_mult = pnl / risk if risk > 0 else 0.0
        self.trades.append(Trade(
            side=self._side, tag=part["tag"], size=part["size"],
            entry_price=part["entry_price"], entry_ts=part["entry_ts"],
            exit_price=exit_price, exit_ts=exit_ts,
            exit_reason=reason, r_multiple=r_mult, risk=risk,
        ))

    def _cancel_entries(self) -> None:
        for oid in self._entry_ids.values():
            self.e.cancel(oid)
        self._entry_ids.clear()

    def _cancel_protection(self) -> None:
        if self._stop_id is not None:
            self.e.cancel(self._stop_id)
            self._stop_id = None
        if self._take_id is not None:
            self.e.cancel(self._take_id)
            self._take_id = None

    def _flat_reset(self) -> None:
        if self._side is not None:      # a session was genuinely open
            self.n_sessions += 1
            if self._part1_joined and self._part2_joined:
                self.n_sessions_both += 1
            elif self._part1_joined:
                self.n_sessions_part1_only += 1
            elif self._part2_joined:
                self.n_sessions_part2_only += 1
            # no other case is reachable: a session only exists once at
            # least one part has filled (see _handle_fill's `if not self._pos`)
        self._cancel_entries()
        self._cancel_protection()
        self._pos.clear()
        self._side = None
        self._stop_price = None
        self._break_pending = False
        self._part1_joined = False
        self._part2_joined = False

    @property
    def has_open_position(self) -> bool:
        """True if a position is still open — e.g. at the end of the data
        window. Such a position is NOT in self.trades (unrealized) and NOT
        counted in n_sessions (never reached _flat_reset) — same "don't
        fabricate closure" discipline as replay.py's still-open final bar."""
        return bool(self._pos)

    def _exit_all(self, price: float, ts: int, reason: str) -> None:
        for part in self._pos:
            self._record(part, price, ts, reason)
        self._flat_reset()

    def _replace_stop(self) -> None:
        """(Re)place the protective stop for the current total size."""
        if self._stop_id is not None:
            self.e.cancel(self._stop_id)
        total = sum(p["size"] for p in self._pos)
        side = "sell" if self._side == "long" else "buy"
        self._stop_id = self.e.place(side, "stop", self._stop_price,
                                     total, tag="stop").id

    # --- fill handling -------------------------------------------------------------

    def _handle_fill(self, f: Fill) -> None:
        if f.tag in ("part1", "part2"):
            # entry fill
            self._entry_ids.pop(f.tag, None)
            if not self._pos:  # first fill defines side and freezes levels
                self._side = "long" if f.side == "buy" else "short"
                self._stop_price = self._lines["0"]
            self._pos.append({"tag": f.tag, "entry_price": f.price,
                              "entry_ts": f.ts, "size": f.size})
            if f.tag == "part1":
                self._part1_joined = True
            else:
                self._part2_joined = True
            self._replace_stop()
            if f.tag == "part1":
                take_side = "sell" if self._side == "long" else "buy"
                self._take_id = self.e.place(take_side, "limit",
                                             self._lines["tq"], f.size,
                                             tag="take").id

        elif f.tag == "stop":
            # gap honesty: a stop fill flattens EVERYTHING at its fill price,
            # even parts that joined on the same tick.
            # Only clear the tracker if the filled stop IS the tracked one:
            # on a gap tick an entry fill may have already superseded this
            # stop with a resized replacement — that replacement must remain
            # tracked so _flat_reset() below cancels it (otherwise it would
            # be orphaned on the book: found by the gap-scenario test).
            if f.order_id == self._stop_id:
                self._stop_id = None
            self._exit_all(f.price, f.ts, "stop")

        elif f.tag == "take":
            self._take_id = None
            part1 = next((p for p in self._pos if p["tag"] == "part1"), None)
            if part1 is not None:
                self._record(part1, f.price, f.ts, "take")
                self._pos.remove(part1)
            if not self._pos:
                self._flat_reset()
            else:
                self._replace_stop()   # resize to remaining part2

    def _maybe_trail(self, bar) -> None:
        """No-op unless trailing is on and we're in a position — a
        replacement stop is only issued when the candidate actually
        tightens (trail_stop returns unchanged otherwise, so the price
        comparison below correctly skips a needless cancel/replace)."""
        if not self.trailing or not self._pos:
            return
        new_stop = trail_stop(self._side, self._stop_price, bar.low, bar.high)
        if new_stop != self._stop_price:
            self._stop_price = new_stop
            self._replace_stop()

    # --- entry refresh --------------------------------------------------------------

    def _refresh_entries(self) -> None:
        self._cancel_entries()
        if not self.keltner.ready:
            return
        b = self.bias()
        if b is None:
            self._lines = None
            return
        self._lines = zone_lines(self.keltner.centerline,
                                 self.keltner._range_sma,
                                 self.mult_inner, self.mult_outer, b)
        if self._last_price is None:
            return
        side = "buy" if b == "bull" else "sell"
        for tag, key in (("part1", "h"), ("part2", "q")):
            px = self._lines[key]
            resting = (px < self._last_price) if side == "buy" \
                else (px > self._last_price)
            if resting:
                self._entry_ids[tag] = self.e.place(side, "limit", px,
                                                    self.part_size, tag=tag).id

    # --- harness hooks ---------------------------------------------------------------

    def on_tick(self, price: float, ts: int) -> None:
        self._last_price = price
        for f in self.e.on_tick(price, ts):
            self._handle_fill(f)

    def _check_reversal_bar_mode(self, bar) -> bool:
        """Slice 3 original: exit on the first opposite bar. See module
        docstring for why this over-triggers on real data."""
        return (self._side == "long" and bar.close < bar.open) or \
               (self._side == "short" and bar.close > bar.open)

    def _check_reversal_swing_mode(self, bar) -> bool:
        """Beggs-faithful: break-and-acceptance against swing structure."""
        exit_now, self._break_pending = check_break_and_acceptance(
            self._side, self.swings.last_swing_low, self.swings.last_swing_high,
            self._break_pending, bar,
        )
        return exit_now

    def on_range_bar(self, bar) -> None:
        self.keltner.update(bar)
        if self.swings is not None:
            self.swings.update(bar)     # tracks continuously, position or not

        if self._pos:
            if self.exit_mode == "swing":
                reversal = self._check_reversal_swing_mode(bar)
            else:
                reversal = self._check_reversal_bar_mode(bar)
            if reversal:
                self._exit_all(bar.close, bar.end_ts, "reversal")

        if self._pos:                    # still in position (didn't just exit)
            self._maybe_trail(bar)

        if not self._pos:
            self._refresh_entries()
