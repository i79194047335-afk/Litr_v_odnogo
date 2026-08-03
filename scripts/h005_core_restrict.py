#!/usr/bin/env python3
"""H-005: is the 31 Jul → 1 Aug rank correlation carried by the transient
population or by a persistent core?

Reuses `pnl_persistence.compare` unchanged — the correlation and null-band maths
stay in the tested tool (11 tests). This script only chooses *which accounts*
go in, which is the whole question.

Criterion is fixed in `hypotheses/H-005.md` before this ran:
  compositional  -> restricting to the 5-day core drops rho into the null band
                    on >= 2 of 3 markets
  persistence    -> rho stays above p95 on >= 2 of 3 markets
  mixed (1 each) -> unresolved, no verdict

    scripts/h005_core_restrict.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.pnl_persistence import compare, day_pnl  # noqa: E402

MARKETS = (0, 2, 24)
ALL_DAYS = ("20260729", "20260730", "20260731", "20260801", "20260802")
PAIR = ("20260731", "20260801")
MIN_FILLS = 50


def core_accounts(market: int, days, min_fills: int) -> set[int]:
    """Accounts clearing min_fills on EVERY day — the stable core.

    Deliberately stricter than `window_pnl`, which pools fills across days: a
    core member here has to show up every single day, which is the population
    in which per-trader persistence should be easiest to see.
    """
    per_day = [set(day_pnl(market, d, min_fills)) for d in days]
    core = per_day[0]
    for s in per_day[1:]:
        core &= s
    return core


def run(market: int) -> dict:
    a_full = day_pnl(market, PAIR[0], MIN_FILLS)
    b_full = day_pnl(market, PAIR[1], MIN_FILLS)
    full = compare(a_full, b_full, *PAIR)

    core = core_accounts(market, ALL_DAYS, MIN_FILLS)
    a_core = {k: v for k, v in a_full.items() if k in core}
    b_core = {k: v for k, v in b_full.items() if k in core}

    if len(set(a_core) & set(b_core)) < 10:
        return {
            "market": market, "core_size": len(core),
            "rho_full": full.spearman, "p95_full": full.spearman_null_p95,
            "rho_core": None, "p95_core": None,
            "note": "core too small to correlate (<10 common accounts)",
        }

    restricted = compare(a_core, b_core, *PAIR)

    # Побочная проверка: ранговая корреляция может держаться на устойчивом
    # порядке убыточных счетов. Считаем, сколько из ядра прибыльны в оба дня.
    both = sorted(set(a_core) & set(b_core))
    pos_both = sum(1 for k in both if a_core[k] > 0 and b_core[k] > 0)
    neg_both = sum(1 for k in both if a_core[k] < 0 and b_core[k] < 0)

    return {
        "market": market,
        "core_size": len(core),
        "common_full": full.common,
        "common_core": restricted.common,
        "rho_full": full.spearman,
        "p95_full": full.spearman_null_p95,
        "rho_core": restricted.spearman,
        "p95_core": restricted.spearman_null_p95,
        "pos_both": pos_both,
        "neg_both": neg_both,
        "n_both": len(both),
    }


def main() -> int:
    print(f"H-005: ограничение на ядро, пара {PAIR[0]} -> {PAIR[1]}, "
          f"min_fills={MIN_FILLS}")
    print(f"ядро = счета, прошедшие порог в КАЖДЫЙ из дней {ALL_DAYS[0]}..{ALL_DAYS[-1]}\n")

    verdicts = []
    for m in MARKETS:
        r = run(m)
        print(f"--- market {m} ---")
        print(f"  ядро: {r['core_size']} счетов")
        if r.get("rho_core") is None:
            print(f"  {r['note']}")
            verdicts.append("unresolved")
            print()
            continue
        print(f"  вся популяция: общих {r['common_full']:4d}  "
              f"rho={r['rho_full']:+.4f}  (нулевой p95 {r['p95_full']:+.4f})")
        print(f"  только ядро:   общих {r['common_core']:4d}  "
              f"rho={r['rho_core']:+.4f}  (нулевой p95 {r['p95_core']:+.4f})")

        above = r["rho_core"] > r["p95_core"]
        verdicts.append("persistence" if above else "compositional")
        print(f"  -> на ядре {'ВЫШЕ' if above else 'внутри'} нулевой полосы")
        print(f"  прибыльны в оба дня: {r['pos_both']}/{r['n_both']}, "
              f"убыточны в оба: {r['neg_both']}/{r['n_both']}")
        print()

    comp = verdicts.count("compositional")
    pers = verdicts.count("persistence")
    print("=" * 60)
    print(f"рынков за композицию: {comp}, за устойчивость: {pers}")
    if comp >= 2:
        print("ВЕРДИКТ: композиционный механизм (B подтверждена)")
    elif pers >= 2:
        print("ВЕРДИКТ: устойчивость участников (B опровергнута)")
    else:
        print("ВЕРДИКТ: не разрешено — рынки противоречат, 5 дней мало")
    return 0


if __name__ == "__main__":
    sys.exit(main())
