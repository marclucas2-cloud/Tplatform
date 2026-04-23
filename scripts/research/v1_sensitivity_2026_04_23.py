"""Sensitivity + robustness check on v1_mes_mr_vix_spike winner candidate.

Parameters grid:
  consec: {2, 3, 4}
  hold:   {2, 3, 4, 5}
  vix_th: {15, 18, 20, 22}

Also: compare with mes_monday_long_oc existing paper sleeve (same data).
"""
from __future__ import annotations
import sys
sys.path.insert(0, '.')
from scripts.research.decorrelated_variants_v2_2026_04_23 import (
    load_futures_long, load_vix_1d, compute_metrics, wf_5splits,
    v1_mes_mr_vix_spike,
)
import pandas as pd

def main():
    mes = load_futures_long("MES")
    vix = load_vix_1d()

    print("=" * 90)
    print("v1_mes_mr_vix_spike SENSITIVITY GRID")
    print("=" * 90)
    print(f"{'consec':>6}{'hold':>6}{'vix':>6}  "
          f"{'trades':>7}{'Sharpe':>8}{'CAGR':>8}{'DD':>8}"
          f"{'Calmar':>8}{'WF':>8}")

    best = None
    best_score = -999
    for c in [2, 3, 4]:
        for h in [2, 3, 4, 5]:
            for vth in [15, 18, 20, 22]:
                r = v1_mes_mr_vix_spike(mes, vix, consec=c, hold=h, vix_min=vth)
                m = compute_metrics(r, r.attrs.get("trades"))
                wf = wf_5splits(r)
                if m.get("error"):
                    continue
                score_tuple = (
                    m["sharpe"],
                    m["calmar"],
                    wf["ratio"],
                    m["n_trades"] or 0,
                )
                print(f"{c:>6}{h:>6}{vth:>6}  "
                      f"{m.get('n_trades', '-'):>7}"
                      f"{m['sharpe']:>8.2f}"
                      f"{m['cagr_pct']:>8.1f}"
                      f"{m['max_dd_pct']:>8.1f}"
                      f"{m['calmar']:>8.2f}"
                      f"{wf['ratio']:>8.2f}")
                # score: sharpe prioritaire + WF ratio tiebreak + min trades 30
                if (m["sharpe"] > 0.5 and wf["ratio"] >= 0.6
                        and (m["n_trades"] or 0) >= 30 and m["max_dd_pct"] > -25):
                    score = m["sharpe"] * wf["ratio"] + m["calmar"] * 0.2
                    if score > best_score:
                        best_score = score
                        best = (c, h, vth, m, wf)

    print("\n--- BEST CONFIG ---")
    if best:
        c, h, vth, m, wf = best
        print(f"consec={c}, hold={h}, vix_th={vth}")
        print(f"  metrics: {m}")
        print(f"  WF: {wf}")
    else:
        print("No config met threshold (Sharpe>0.5, WF>=0.6, trades>=30, DD>-25%).")


if __name__ == "__main__":
    main()
