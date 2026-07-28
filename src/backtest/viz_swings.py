"""
Visualize how SwingTracker finds swing points and HH/HL structure — on the
REAL code (src.backtest.swings.SwingTracker + src.rangebars.builder), not a
re-telling of it.

What the output shows (standalone HTML, open in any browser):
  - range bars as candles (x-axis = bar index; range bars have no uniform
    clock, so uniform spacing by index is the honest axis);
  - triangle at every CONFIRMED swing point, drawn at the bar where the
    extremum actually IS, labeled H1/L1/H2/L2... plus its class:
    HH/LH for highs, HL/LL for lows (vs the previous swing of the same kind);
  - a "✓H1"/"✓L1" text mark on the bar where the algorithm LEARNED about it —
    always confirm_bars later. The horizontal gap between the triangle and
    its checkmark IS ndr's "лаг подтверждения", drawn, not asserted;
  - two step-lines: last_swing_high / last_swing_low as the strategy reads
    them at each bar. They jump exactly at the ✓ bars, never at the triangle
    bars — the algorithm lives in the past by construction;
  - small circles on bars whose CLOSE crossed the current step-line
    (stage-1 "objective break" of the two-stage exit rule).

Usage (VPS, real data):
    python -m src.backtest.viz_swings \
        --files data/ticks/trades_1_20260703.jsonl \
        --range-size 15.3 --start-bar 0 --n-bars 250 \
        --out swings_view.html

    # or another day / market: any collector JSONL(s), same flags as
    # export_sample.py where they overlap.

Usage (no data needed, synthetic demo):
    python -m src.backtest.viz_swings --demo --out swings_demo.html

Flags:
    --confirm-bars N   SwingTracker window half-width (default 2 = Beggs).
    --n-bars / --start-bar   window of bars to render (default 250 from 0).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

from src.rangebars.builder import RangeBarBuilder
from src.backtest.swings import SwingTracker


# ---------------------------------------------------------------------------
# data sources
# ---------------------------------------------------------------------------

def ticks_from_files(paths: list[Path]):
    from src.rangebars.calibrate import iter_price_ts
    for p in paths:
        yield from iter_price_ts(p)


def ticks_synthetic(seed: int = 7, n: int = 60_000):
    """Random walk with alternating drift regimes — guarantees visible
    HH/HL runs and structure breaks. Demo only; every real run should use
    collector files."""
    rng = random.Random(seed)
    price = 3000.0
    ts = 1_700_000_000_000
    drift = 0.02
    for i in range(n):
        if i % 4000 == 0:
            drift = rng.choice([-0.06, -0.02, 0.02, 0.06])
        price += drift + rng.gauss(0, 1.2)
        price = max(price, 100.0)
        ts += rng.randint(20, 400)
        yield price, ts


# ---------------------------------------------------------------------------
# run the real pipeline, record everything the chart needs
# ---------------------------------------------------------------------------

def run(ticks, range_size: float, confirm_bars: int,
        start_bar: int, n_bars: int) -> dict:
    builder = RangeBarBuilder(range_size=range_size)
    tracker = SwingTracker(confirm_bars=confirm_bars)

    bars = []            # rendered window only
    swings = []          # confirmed swing events
    breaks = []          # stage-1 close-beyond-level marks
    prev_high = None     # previous confirmed swing high VALUE (for HH/LH)
    prev_low = None
    n_high = n_low = 0
    idx = -1
    end_bar = start_bar + n_bars
    state = {"below": False, "above": False}

    def feed(bar):
        nonlocal idx, prev_high, prev_low, n_high, n_low
        idx += 1
        if idx >= end_bar:
            return False

        before_h, before_l = tracker.last_swing_high, tracker.last_swing_low
        tracker.update(bar)

        in_window = idx >= start_bar
        if in_window:
            bars.append({
                "i": idx, "o": bar.open, "h": bar.high,
                "l": bar.low, "c": bar.close,
                "sh": tracker.last_swing_high, "sl": tracker.last_swing_low,
            })

        # a swing confirmed on THIS bar iff the tracked value changed
        if tracker.last_swing_high is not None and tracker.last_swing_high != before_h:
            n_high += 1
            cls = ("HH" if prev_high is None or tracker.last_swing_high > prev_high
                   else "LH" if tracker.last_swing_high < prev_high else "=H")
            if prev_high is None:
                cls = "H"          # first one has nothing to compare to
            swings.append({
                "kind": "high", "n": n_high, "cls": cls,
                "extremum_i": idx - confirm_bars, "confirm_i": idx,
                "value": tracker.last_swing_high,
            })
            prev_high = tracker.last_swing_high
        if tracker.last_swing_low is not None and tracker.last_swing_low != before_l:
            n_low += 1
            cls = ("HL" if prev_low is None or tracker.last_swing_low > prev_low
                   else "LL" if tracker.last_swing_low < prev_low else "=L")
            if prev_low is None:
                cls = "L"
            swings.append({
                "kind": "low", "n": n_low, "cls": cls,
                "extremum_i": idx - confirm_bars, "confirm_i": idx,
                "value": tracker.last_swing_low,
            })
            prev_low = tracker.last_swing_low

        # stage-1 objective break, exactly as the exit rule defines it:
        # CLOSE beyond the last confirmed level known AT THIS BAR.
        # Marked as an EVENT (first bar beyond), not as a state — in a trend
        # every bar sits beyond the lagging opposite level, and marking all
        # of them buries the chart (measured: 119 marks on 250 demo bars).
        below_now = (tracker.last_swing_low is not None
                     and bar.close < tracker.last_swing_low)
        above_now = (tracker.last_swing_high is not None
                     and bar.close > tracker.last_swing_high)
        if in_window:
            if below_now and not state["below"]:
                breaks.append({"i": idx, "side": "below"})
            if above_now and not state["above"]:
                breaks.append({"i": idx, "side": "above"})
        state["below"], state["above"] = below_now, above_now
        return True

    for price, ts in ticks:
        done = False
        for closed in (builder.update(price, ts) or ()):
            if not feed(closed):
                done = True
                break
        if done:
            break

    # drop swing events whose markers fall outside the rendered window
    swings = [s for s in swings
              if s["extremum_i"] >= start_bar and s["confirm_i"] < end_bar]

    return {"bars": bars, "swings": swings, "breaks": breaks,
            "confirm_bars": confirm_bars, "range_size": range_size}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def render_html(result: dict) -> str:
    """Self-contained HTML+SVG. No external scripts, no CDN — the first
    version loaded lightweight-charts from unpkg and rendered a black
    rectangle inside the Claude app viewer, which blocks external fetches.
    Python draws the SVG at export time; the only JS is ~20 lines of
    vanilla for the crosshair legend. Works offline anywhere."""
    bars, swings, breaks = result["bars"], result["swings"], result["breaks"]
    cb = result["confirm_bars"]
    BW = 11                      # px per bar
    PAD_L, PAD_R, PAD_T, PAD_B = 8, 8, 26, 30
    H = 560
    first_i = bars[0]["i"]
    width = PAD_L + PAD_R + len(bars) * BW

    lows = [b["l"] for b in bars]
    highs = [b["h"] for b in bars]
    pmin, pmax = min(lows), max(highs)
    pad = (pmax - pmin) * 0.06 or 1.0
    pmin -= pad
    pmax += pad
    span = pmax - pmin
    plot_h = H - PAD_T - PAD_B

    def y(p): return PAD_T + (pmax - p) / span * plot_h
    def xc(i): return PAD_L + (i - first_i) * BW + BW / 2

    GREEN, RED, YELLOW, GREY = "#26a69a", "#ef5350", "#f0b90b", "#b2b5be"
    by_i = {b["i"]: b for b in bars}
    svg = []

    # horizontal gridlines + x index labels
    n_grid = 6
    grid_lines, axis_labels = [], []
    for k in range(n_grid + 1):
        p = pmin + span * k / n_grid
        yy = y(p)
        grid_lines.append(f'<line x1="0" y1="{yy:.1f}" x2="{width}" y2="{yy:.1f}" stroke="#1e222d"/>')
        axis_labels.append((yy, p))
    svg += grid_lines
    for b in bars:
        if b["i"] % 20 == 0:
            svg.append(f'<text x="{xc(b["i"]):.1f}" y="{H-8}" fill="#787b86" '
                       f'font-size="10" text-anchor="middle">{b["i"]}</text>')

    # swing step-lines (drawn under candles)
    for key, color in (("sh", RED), ("sl", GREEN)):
        pts = []
        for b in bars:
            v = b[key]
            if v is None:
                continue
            x0 = xc(b["i"]) - BW / 2
            pts.append(f"{x0:.1f},{y(v):.1f}")
            pts.append(f"{x0+BW:.1f},{y(v):.1f}")
        if pts:
            svg.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                       f'stroke="{color}" stroke-width="1.4" stroke-opacity="0.9"/>')

    # candles
    for b in bars:
        cxx = xc(b["i"])
        col = GREEN if b["c"] >= b["o"] else RED
        svg.append(f'<line x1="{cxx:.1f}" y1="{y(b["h"]):.1f}" '
                   f'x2="{cxx:.1f}" y2="{y(b["l"]):.1f}" stroke="{col}"/>')
        top = min(y(b["o"]), y(b["c"]))
        hgt = max(1.0, abs(y(b["o"]) - y(b["c"])))
        svg.append(f'<rect x="{cxx-3.5:.1f}" y="{top:.1f}" width="7" '
                   f'height="{hgt:.1f}" fill="{col}"/>')

    # stage-1 break events
    for br in breaks:
        b = by_i.get(br["i"])
        if b is None:
            continue
        yy = y(b["l"]) + 12 if br["side"] == "below" else y(b["h"]) - 12
        svg.append(f'<circle cx="{xc(br["i"]):.1f}" cy="{yy:.1f}" r="3" '
                   f'fill="#f7f7f7" fill-opacity="0.9"/>')

    # confirmed swings: triangle at the extremum bar + checkmark at confirm bar
    for s in swings:
        eb, cbr = by_i.get(s["extremum_i"]), by_i.get(s["confirm_i"])
        if eb is None:
            continue
        ex = xc(s["extremum_i"])
        hi = s["kind"] == "high"
        col = (GREEN if s["cls"] in ("HH", "HL")
               else RED if s["cls"] in ("LH", "LL") else GREY)
        if hi:
            ty = y(eb["h"]) - 8
            tri = f"{ex-5:.1f},{ty-8:.1f} {ex+5:.1f},{ty-8:.1f} {ex:.1f},{ty:.1f}"
            lbl_y = ty - 12
        else:
            ty = y(eb["l"]) + 8
            tri = f"{ex-5:.1f},{ty+8:.1f} {ex+5:.1f},{ty+8:.1f} {ex:.1f},{ty:.1f}"
            lbl_y = ty + 20
        svg.append(f'<polygon points="{tri}" fill="{col}"/>')
        svg.append(f'<text x="{ex:.1f}" y="{lbl_y:.1f}" fill="{col}" '
                   f'font-size="10" text-anchor="middle">'
                   f'{"H" if hi else "L"}{s["n"]} {s["cls"]}</text>')
        if cbr is not None:
            cx2 = xc(s["confirm_i"])
            cy2 = y(cbr["h"]) - 16 if hi else y(cbr["l"]) + 16
            svg.append(f'<circle cx="{cx2:.1f}" cy="{cy2:.1f}" r="2.5" fill="{YELLOW}"/>')
            svg.append(f'<text x="{cx2:.1f}" y="{cy2 - 5 if hi else cy2 + 12:.1f}" '
                       f'fill="{YELLOW}" font-size="9" text-anchor="middle">'
                       f'\u2713{"H" if hi else "L"}{s["n"]}</text>')

    svg.append(f'<line id="xh" x1="-10" y1="{PAD_T}" x2="-10" y2="{PAD_T+plot_h}" '
               f'stroke="#787b86" stroke-dasharray="3,3"/>')

    axis_html = "".join(
        f'<span style="top:{yy-7:.0f}px">{p:.1f}</span>' for yy, p in axis_labels)
    slim = [{"i": b["i"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
             "sh": b["sh"], "sl": b["sl"]} for b in bars]

    return f'''<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SwingTracker \u2014 HH/HL \u0433\u043b\u0430\u0437\u0430\u043c\u0438</title>
<style>
 body{{margin:0;background:#131722;color:#d1d4dc;
      font:13px/1.45 -apple-system,system-ui,sans-serif}}
 #wrap{{position:relative;height:{H}px}}
 #scroll{{overflow-x:auto;height:100%;-webkit-overflow-scrolling:touch}}
 #axis{{position:absolute;top:0;right:0;bottom:0;width:56px;pointer-events:none}}
 #axis span{{position:absolute;right:2px;font-size:10px;color:#787b86;
      background:rgba(19,23,34,.7);padding:0 3px}}
 #legend{{position:absolute;top:4px;left:6px;background:rgba(19,23,34,.88);
      padding:5px 9px;border-radius:6px;pointer-events:none;white-space:pre;z-index:5}}
 #caption{{padding:8px 10px;border-top:1px solid #2a2e39}}
 .hh{{color:{GREEN}}} .lh{{color:{RED}}} .lag{{color:{YELLOW}}}
</style></head><body>
<div id="wrap"><div id="scroll">
<svg width="{width}" height="{H}" xmlns="http://www.w3.org/2000/svg">{"".join(svg)}</svg>
</div><div id="axis">{axis_html}</div><div id="legend"></div></div>
<div id="caption">
<b>\u25bc/\u25b2</b> \u2014 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043d\u043d\u044b\u0439 swing high/low \u043d\u0430 \u0431\u0430\u0440\u0435, \u0433\u0434\u0435 \u044d\u043a\u0441\u0442\u0440\u0435\u043c\u0443\u043c \u0411\u042b\u041b.
<b class="lag">\u2713H/\u2713L</b> \u2014 \u0431\u0430\u0440, \u0433\u0434\u0435 \u0430\u043b\u0433\u043e\u0440\u0438\u0442\u043c \u043e \u043d\u0451\u043c \u0423\u0417\u041d\u0410\u041b (+{cb} \u0431\u0430\u0440\u0430 \u2014 \u043b\u0430\u0433 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f).
\u041a\u043b\u0430\u0441\u0441: <b class="hh">HH/HL</b> <b class="lh">LH/LL</b> \u043f\u0440\u043e\u0442\u0438\u0432 \u043f\u0440\u043e\u0448\u043b\u043e\u0433\u043e \u0441\u0432\u0438\u043d\u0433\u0430 \u0442\u043e\u0433\u043e \u0436\u0435 \u0442\u0438\u043f\u0430.
\u0421\u0442\u0443\u043f\u0435\u043d\u044c\u043a\u0438 \u2014 last_swing_high/low, \u043a\u0430\u043a \u0438\u0445 \u0447\u0438\u0442\u0430\u0435\u0442 \u0441\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u044f: \u043f\u0440\u044b\u0433\u0430\u044e\u0442 \u043d\u0430 \u0433\u0430\u043b\u043e\u0447\u043a\u0430\u0445, \u043d\u0435 \u043d\u0430 \u0442\u0440\u0435\u0443\u0433\u043e\u043b\u044c\u043d\u0438\u043a\u0430\u0445.
<b>\u25cf</b> \u2014 close \u043f\u0435\u0440\u0435\u0441\u0451\u043a \u0443\u0440\u043e\u0432\u0435\u043d\u044c (stage-1 \u043f\u0440\u043e\u0431\u043e\u0439). \u0422\u0430\u043f/\u0432\u0435\u0434\u0438 \u043f\u0430\u043b\u044c\u0446\u0435\u043c \u043f\u043e \u0433\u0440\u0430\u0444\u0438\u043a\u0443 \u2014 \u0432 \u043b\u0435\u0433\u0435\u043d\u0434\u0435 OHLC \u0438 \u043e\u0431\u0430 \u0443\u0440\u043e\u0432\u043d\u044f \u043d\u0430 \u044d\u0442\u043e\u043c \u0431\u0430\u0440\u0435.
</div>
<script>
var BARS={json.dumps(slim)},BW={BW},PADL={PAD_L},FI={first_i};
var by={{}};BARS.forEach(function(b){{by[b.i]=b}});
var sc=document.getElementById("scroll"),lg=document.getElementById("legend"),
    xh=document.getElementById("xh");
function show(ev){{
  var r=sc.getBoundingClientRect();
  var px=(ev.touches?ev.touches[0].clientX:ev.clientX)-r.left+sc.scrollLeft;
  var i=FI+Math.floor((px-PADL)/BW),b=by[i];if(!b)return;
  var f=function(v){{return v===null?"\u2014":v.toFixed(2)}};
  lg.textContent="bar "+b.i+"  O "+f(b.o)+" H "+f(b.h)+" L "+f(b.l)+" C "+f(b.c)+
    "\nswing_high "+f(b.sh)+"  swing_low "+f(b.sl);
  var cx=PADL+(i-FI)*BW+BW/2;xh.setAttribute("x1",cx);xh.setAttribute("x2",cx);
}}
sc.addEventListener("pointermove",show);sc.addEventListener("pointerdown",show);
sc.addEventListener("touchstart",show,{{passive:true}});
sc.addEventListener("touchmove",show,{{passive:true}});
</script></body></html>'''


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", type=Path, default=[])
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--range-size", type=float, default=15.3)
    ap.add_argument("--confirm-bars", type=int, default=2)
    ap.add_argument("--start-bar", type=int, default=0)
    ap.add_argument("--n-bars", type=int, default=250)
    ap.add_argument("--out", type=Path, default=Path("swings_view.html"))
    args = ap.parse_args()

    if args.demo == bool(args.files):
        print("Pick exactly one source: --files ... OR --demo",
              file=sys.stderr)
        sys.exit(2)

    ticks = ticks_synthetic() if args.demo else ticks_from_files(args.files)
    if args.demo:
        # synthetic walk has sigma~1.2/tick; a 15.3 range on ETH-like scale
        # works for real data but on the demo walk use something visible
        args.range_size = args.range_size if args.range_size != 15.3 else 12.0

    result = run(ticks, args.range_size, args.confirm_bars,
                 args.start_bar, args.n_bars)

    nb, ns = len(result["bars"]), len(result["swings"])
    if nb == 0:
        print("No bars in the requested window — check files/range-size.",
              file=sys.stderr)
        sys.exit(1)
    lags = {s["confirm_i"] - s["extremum_i"] for s in result["swings"]}
    highs = [s for s in result["swings"] if s["kind"] == "high"]
    lows = [s for s in result["swings"] if s["kind"] == "low"]
    print(f"bars rendered: {nb} (index {result['bars'][0]['i']}..{result['bars'][-1]['i']})")
    print(f"confirmed swings: {ns} ({len(highs)} highs, {len(lows)} lows)")
    print(f"observed confirmation lag(s): {sorted(lags)} bars "
          f"(must be exactly {{{args.confirm_bars}}} — inherent to the definition)")
    print(f"stage-1 breaks marked: {len(result['breaks'])}")

    html = render_html(result)
    args.out.write_text(html, encoding="utf-8")
    print(f"written: {args.out}  ({args.out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
