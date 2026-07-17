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
    Stage 2 (acceptance, `acceptance_bars` further bars, default 1): if
        price's close stays beyond that level for `acceptance_bars`
        consecutive bars after the break bar, the break is accepted ->
        exit. If any of those bars closes back on the original side, the
        break failed — per the source this is actually often a signal to
        STAY, not leave, so we just clear the pending count and keep
        holding; a fresh break later starts counting from zero again.
    `acceptance_bars` defaults to 1 bar (2 bars total: break + 1 confirm),
    chosen originally to mirror the swing definition's own 2-bar lag
    discipline. Real-data testing (2026-07-07) found this 1-bar window
    resolves almost as fast as the original single-bar rule this whole
    mechanization was built to replace, and widening `swing_confirm_bars`
    (which controls how mature the swing POINT is, not how long the BREAK
    must hold) made results worse, not better — motivating
    `acceptance_bars` as its own separate, explicit parameter to test.

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
    pending_break: bool | int,
    bar,
    acceptance_bars: int = 1,
) -> tuple[bool, bool | int]:
    """One step of the generalized two-stage rule (see module docstring).

    `pending_break`: False (or 0) means no break currently in progress.
    True (or an int N>=1) means price has closed beyond the level for N
    consecutive bars, including the break bar itself. `acceptance_bars`
    (default 1, matching the original hardcoded behavior exactly) is how
    many further bars price must stay beyond the level, after the break
    bar, before the reversal is accepted — total bars from break to exit
    = acceptance_bars + 1.

    Returns (exit_now, new_pending_break). Pure function — no state held
    here, the caller (WFStrategy) carries `pending_break` across calls.

    Backward compatible by construction, including strict identity checks
    (not just equality): existing callers that don't pass
    `acceptance_bars` get the literal True/False values back exactly as
    before — every pre-existing test in both test_swings.py and
    test_strategy.py (including two that check `is True`/`is False`)
    passes with zero edits. Only when `acceptance_bars > 1` and a break
    has survived more than one bar does this return a plain int (2, 3,
    ...) instead of True — a case that couldn't exist before this change.
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

    if not broke:
        return False, False

    new_count = pending_break + 1
    if new_count > acceptance_bars:
        return True, False
    if new_count == 1:
        # Exact identity match with the pre-2026-07-07 behavior when
        # acceptance_bars=1 (the default): the very first pending bar
        # returns the literal True singleton, not the int 1, because
        # test_strategy.py's swing-mode integration tests check this
        # state with `is True`/`is False` (identity), not `==`
        # (equality). Only bars 2+ of a multi-bar acceptance window
        # (acceptance_bars > 1) surface as plain integers.
        return False, True
    return False, new_count
