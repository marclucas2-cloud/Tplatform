#!/usr/bin/env python3
"""Walk-Forward validation du PORTEFEUILLE V2 IB (first-refusal + slippage).

5 fenetres roulantes sur 10Y (2015-2026). Chaque fenetre:
  - IS: 70% des bars
  - OOS: 30% des bars
  - Pas de ré-optimisation de parametres (les strats sont deja fixees).
  - Mesure la robustesse du COMPORTEMENT du portefeuille dans differents regimes.

Ce WF ne valide pas les strats individuelles (deja WF) mais le SYSTEME:
  - Interactions (first-refusal, risk budget, GUARD2)
  - Costs + slippage
  - Tenue dans bulls, bears, crisis
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ib_portfolio_v2 import run_portfolio, SPECS
from backtest_ib_portfolio_10y import load_long

ROOT = Path(__file__).resolve().parent.parent


def slice_dfs(dfs, start, end):
    """Slice all DataFrames to [start, end] date range."""
    out = {}
    for sym, df in dfs.items():
        mask = (df.index >= start) & (df.index <= end)
        out[sym] = df[mask]
    return out


def compute_metrics_from_result(r):
    return {
        "n_trades": r['n_trades'],
        "wr": r['wr'],
        "total_pnl": r['total_pnl'],
        "cagr": r['roc_annual'],
        "sharpe": r['sharpe'],
        "max_dd": r['max_dd'],
    }


def run_window(dfs, is_start, is_end, oos_start, oos_end):
    """Run V2 portfolio on IS and OOS windows separately."""
    # Intersection for IS
    is_dfs = slice_dfs(dfs, is_start, is_end)
    is_common = is_dfs["MES"].index
    for sym in ["MNQ", "MGC", "MCL"]:
        is_common = is_common.intersection(is_dfs[sym].index)
    if len(is_common) < 100:
        return None, None

    is_result = run_portfolio(is_dfs, is_common, use_first_refusal=True,
                              apply_slippage=True, label=f"IS {is_start.date()}..{is_end.date()}")

    # Intersection for OOS
    oos_dfs = slice_dfs(dfs, oos_start, oos_end)
    oos_common = oos_dfs["MES"].index
    for sym in ["MNQ", "MGC", "MCL"]:
        oos_common = oos_common.intersection(oos_dfs[sym].index)
    if len(oos_common) < 50:
        return is_result, None

    oos_result = run_portfolio(oos_dfs, oos_common, use_first_refusal=True,
                               apply_slippage=True, label=f"OOS {oos_start.date()}..{oos_end.date()}")
    return is_result, oos_result


def main():
    print("Loading 10Y LONG data (MCL pathological bars dropped)...")
    dfs = {sym: load_long(sym) for sym in SPECS.keys()}

    common_all = dfs["MES"].index
    for sym in ["MNQ", "MGC", "MCL"]:
        common_all = common_all.intersection(dfs[sym].index)

    print(f"Full period: {common_all[0].date()} -> {common_all[-1].date()} ({len(common_all)} bars)")

    # 5 rolling windows, 70/30 IS/OOS
    n_windows = 5
    total_bars = len(common_all)
    window_bars = total_bars // n_windows
    is_bars = int(window_bars * 0.70)
    oos_bars = window_bars - is_bars

    print(f"Window size: {window_bars} bars | IS: {is_bars} | OOS: {oos_bars}")
    print()

    results = []
    for w in range(n_windows):
        is_start_idx = w * window_bars
        is_end_idx = is_start_idx + is_bars - 1
        oos_start_idx = is_end_idx + 1
        oos_end_idx = min(oos_start_idx + oos_bars - 1, total_bars - 1)

        is_start = common_all[is_start_idx]
        is_end = common_all[is_end_idx]
        oos_start = common_all[oos_start_idx]
        oos_end = common_all[oos_end_idx]

        print(f"--- Window {w+1}/5 ---")
        print(f"IS:  {is_start.date()} -> {is_end.date()}  ({is_bars} bars)")
        print(f"OOS: {oos_start.date()} -> {oos_end.date()}  ({oos_end_idx - oos_start_idx + 1} bars)")

        is_r, oos_r = run_window(dfs, is_start, is_end, oos_start, oos_end)
        if is_r is None or oos_r is None:
            print("  SKIP (insufficient data)")
            continue

        is_m = compute_metrics_from_result(is_r)
        oos_m = compute_metrics_from_result(oos_r)

        row = {
            "window": w + 1,
            "is_period": f"{is_start.date()}..{is_end.date()}",
            "oos_period": f"{oos_start.date()}..{oos_end.date()}",
            "is_n": is_m["n_trades"],
            "is_sharpe": round(is_m["sharpe"], 2),
            "is_pnl": round(is_m["total_pnl"], 0),
            "is_cagr": round(is_m["cagr"] * 100, 1),
            "is_maxdd": round(is_m["max_dd"] * 100, 1),
            "oos_n": oos_m["n_trades"],
            "oos_sharpe": round(oos_m["sharpe"], 2),
            "oos_pnl": round(oos_m["total_pnl"], 0),
            "oos_cagr": round(oos_m["cagr"] * 100, 1),
            "oos_maxdd": round(oos_m["max_dd"] * 100, 1),
            "oos_profitable": oos_m["total_pnl"] > 0,
        }
        results.append(row)
        print(f"  IS:  n={is_m['n_trades']} sharpe={is_m['sharpe']:.2f} "
              f"pnl=${is_m['total_pnl']:+,.0f} cagr={is_m['cagr']*100:.1f}% "
              f"DD={is_m['max_dd']*100:.1f}%")
        print(f"  OOS: n={oos_m['n_trades']} sharpe={oos_m['sharpe']:.2f} "
              f"pnl=${oos_m['total_pnl']:+,.0f} cagr={oos_m['cagr']*100:.1f}% "
              f"DD={oos_m['max_dd']*100:.1f}%")
        print()

    if not results:
        print("No results")
        return 1

    df = pd.DataFrame(results)
    print("=" * 100)
    print("  WALK-FORWARD RESULTS — V2 portefeuille (first-refusal + slippage realiste)")
    print("=" * 100)
    cols = ["window", "is_period", "oos_period",
            "is_n", "is_sharpe", "is_pnl", "is_cagr", "is_maxdd",
            "oos_n", "oos_sharpe", "oos_pnl", "oos_cagr", "oos_maxdd"]
    print(df[cols].to_string(index=False))

    oos_profitable = df["oos_profitable"].sum()
    mean_oos_sharpe = df[df["oos_n"] >= 5]["oos_sharpe"].mean()
    median_oos_cagr = df["oos_cagr"].median()
    worst_oos_dd = df["oos_maxdd"].min()

    print(f"\n=== GATES ===")
    print(f"OOS profitable windows:  {oos_profitable}/{len(results)}")
    print(f"OOS mean Sharpe:         {mean_oos_sharpe:.2f}")
    print(f"OOS median CAGR:         {median_oos_cagr:.1f}%")
    print(f"Worst OOS DD:            {worst_oos_dd:.1f}%")
    print()
    print(f"Gate V15.3:")
    gate_oos_wins = oos_profitable >= 3
    gate_oos_sharpe = mean_oos_sharpe > 0.3
    print(f"  OOS profitable >= 3/5:  {'PASS' if gate_oos_wins else 'FAIL'} ({oos_profitable}/5)")
    print(f"  OOS mean Sharpe > 0.3:  {'PASS' if gate_oos_sharpe else 'FAIL'} ({mean_oos_sharpe:.2f})")
    overall = gate_oos_wins and gate_oos_sharpe
    print(f"  OVERALL:                {'PASS' if overall else 'FAIL'}")

    out = ROOT / "reports" / "research"
    df.to_csv(out / "ib_portfolio_wf_v2.csv", index=False)
    print(f"\n[ok] WF results saved to {out}/ib_portfolio_wf_v2.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
