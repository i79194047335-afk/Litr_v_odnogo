"""
Swing-point structure — Lance Beggs' HH/HL definition, sourced directly from
the uploaded articles (docs/kb sources: "Как определяю тренд",
"Когда меняется тренд", "Когда не доверять откату" — read 2026-07-04/05,
not present in the git repo, only in Claude project knowledge).

DEFINITION (from the source, not an approximation):
    A swing HIGH is a bar whose high is higher than the highs of the 2 bars
    immediately before AND the 2 bars immediately after it.
    A swing LOW is a bar whose low is lower than the lows of the 2 bars
    immediately before AND the 2 bars immediately after it.
    Confirmation is necessarily LAGGED by 2 bars — a swing point cannot be
    known until 2 bars after it forms. This lag is inherent to the
    definition, not a design choice we could remove.

WHY THIS REPLACES THE SLICE-3 REVERSAL RULE:
    Slice 3 exited a position on the first bar where close < open (long) —
    literally "one bar went the other way". On a near-random tick sequence
    that fires roughly every 2 bars by construction (verified: expected wait
    for a p=0.5 coin to show "tails" is 2 flips), which is exactly why 2886
    of 2909 real trades exited this way and only 35 ever reached the take
    target. The source is explicit that a single break is NOT a trend
    change: "не думайте автоматически, что если тренд сломан — это означает
    изменение тренда... отказ цены удержать изменение тренда является
    хорошим сигналом для входа в направлении основного тренда." A real
    reversal requires the swing structure to break AND price to stay broken
    (acceptance) — not one bar flickering the other way.

TWO-STAGE BREAK-AND-ACCEPTANCE RULE (our mechanization of the above):
    Stage 1 (objective break): a bar's CLOSE trades beyond the last
        confirmed swing point (below swing-low for a long, above swing-high
        for a short). Using close rather than a wick-touch avoids
        overreacting to a single spike.
    Stage 2 (acceptance, one further bar): if the VERY NEXT bar's close is
        still beyond that level, the break is accepted -> exit. If the next
        bar closes back on the original side, the break failed — per the
        source this is actually often a signal to STAY, not leave, so we
        just clear the pending flag and keep holding.
    This is our own mechanization of "acceptance", not a number given in the
    source (the articles describe the concept, not a bar-count) — one
    confirmation bar was chosen to mirror the same 2-bar-lag discipline the
    swing definition itself already uses, keeping one consistent time
    constant through the whole rule rather than inventing a second one.

CORNER CASE, documented: until a swing point has confirmed at all (needs 5
bars minimum), there is no swing-based reversal signal available — a
position can only be closed by its stop in that window. This is honest
(we simply don't have structure information yet), not a bug.
"""
from __future__ import annotations

from collections import deque
from typing import Literal


class SwingTracker:
    """Feed range bars one at a time; read `.last_swing_low` /
    `.last_swing_high` — the latest CONFIRMED swing point of each type,
    or None if none has confirmed yet. Tracks continuously regardless of
    position state (swing structure exists independent of being in a trade)."""

    def __init__(self, confirm_bars: int = 2):
        if confirm_bars < 1:
            raise ValueError(f"confirm_bars must be >= 1, got {confirm_bars}")
        self.confirm_bars = confirm_bars
        self._window: deque = deque(maxlen=2 * confirm_bars + 1)
        self.last_swing_low: float | None = None
        self.last_swing_high: float | None = None

    def update(self, bar) -> None:
        self._window.append(bar)
        if len(self._window) < self._window.maxlen:
            return  # not enough bars yet to confirm the middle one

        mid_idx = self.confirm_bars
        candidate = self._window[mid_idx]
        neighbors = [b for i, b in enumerate(self._window) if i != mid_idx]

        if all(candidate.low < b.low for b in neighbors):
            self.last_swing_low = candidate.low
        if all(candidate.high > b.high for b in neighbors):
            self.last_swing_high = candidate.high


def check_break_and_acceptance(
    side: Literal["long", "short"],
    last_swing_low: float | None,
    last_swing_high: float | None,
    pending_break: bool,
    bar,
) -> tuple[bool, bool]:
    """One step of the two-stage rule (see module docstring).

    Returns (exit_now, new_pending_break_state). Pure function — no state
    held here, the caller (WFStrategy) carries `pending_break` across calls.
    """
    if side == "long":
        level = last_swing_low
        if level is None:
            return False, False
        broke = bar.close < level
    else:
        level = last_swing_high
        if level is None:
            return False, False
        broke = bar.close > level

    if pending_break:
        if broke:
            return True, False       # still beyond the level -> accepted -> exit
        return False, False          # reclaimed -> break failed -> stay, clear flag

    if broke:
        return False, True           # objective break just happened, await confirmation
    return False, False
