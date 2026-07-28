#!/usr/bin/env bash
# One command: visualize the swing structure, measure regime density, and
# test whether the take population is separable ex ante.
#
#   ./run_diag.sh                      # BTC (market 1), range_size 15.3
#   ./run_diag.sh 0 12.0               # market 0 (ETH), range_size 12.0
#   MAKER=0.5 TAKER=2.0 ./run_diag.sh  # with friction
#
# Writes: diag_out/<market>_<stamp>.log  and  diag_out/swings_<market>.html
# Run from the repo root with the venv active.

set -u
MARKET="${1:-1}"
RANGE="${2:-15.3}"
MAKER="${MAKER:-0.0}"
TAKER="${TAKER:-0.0}"
SLIP="${SLIP:-0.0}"
DATA_DIR="${DATA_DIR:-data/ticks}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="diag_out"
mkdir -p "$OUT"
LOG="$OUT/m${MARKET}_${STAMP}.log"

if [ -z "${VIRTUAL_ENV:-}" ]; then
  echo "!! venv is not active. Run:  source .venv/bin/activate" >&2
  echo "   (a venv-less launch was the 5th click-only bug on this project)" >&2
  exit 2
fi

shopt -s nullglob
FILES=( "$DATA_DIR"/trades_"$MARKET"_*.jsonl )
shopt -u nullglob
if [ ${#FILES[@]} -eq 0 ]; then
  echo "!! no files matching $DATA_DIR/trades_${MARKET}_*.jsonl" >&2
  ls -la "$DATA_DIR" 2>/dev/null | head -20 >&2
  exit 2
fi

{
echo "############################################################"
echo "# market=$MARKET range_size=$RANGE  maker=$MAKER taker=$TAKER slip=$SLIP"
echo "# ${#FILES[@]} file(s):"
printf '#   %s\n' "${FILES[@]}"
echo "# commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "# date:   $(date -Is)"
echo "############################################################"

echo
echo "############ 1/3  swing visualizer -> HTML ############"
python -m src.backtest.viz_swings \
    --files "${FILES[@]}" \
    --range-size "$RANGE" \
    --n-bars 250 \
    --out "$OUT/swings_m${MARKET}.html"

echo
echo "############ 2/3  regime density ############"
python diag_regime.py --range-size "$RANGE" "${FILES[@]}"

echo
echo "############ 3/3  take vs rest, ex ante ############"
python diag_take_vs_rest.py \
    --market "$MARKET" \
    --range-size "$RANGE" \
    --maker-bps "$MAKER" \
    --taker-bps "$TAKER" \
    --slippage-ticks "$SLIP" \
    "${FILES[@]}"

echo
echo "############ done ############"
} 2>&1 | tee "$LOG"

echo
echo "log:  $LOG"
echo "html: $OUT/swings_m${MARKET}.html"
echo
echo "To view the chart, from your side of the tunnel:"
echo "  python -m http.server 8899 --bind 127.0.0.1 --directory $OUT"
echo "  then open http://127.0.0.1:8899/swings_m${MARKET}.html"
