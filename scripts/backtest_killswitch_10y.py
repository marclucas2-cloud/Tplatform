#!/usr/bin/env python3
"""Compare V2 portefeuille avec et sans regime kill-switch sur 10Y.

Kill-switch regle:
  - Track rolling 90j peak equity
  - Pause si DD current < -15% vs peak
  - Resume si DD remonte > -8% (hysteresis)
  - Pas de nouvelles entrees pendant la pause (sorties normales continuent)

Teste aussi plusieurs seuils pour voir lequel offre le meilleur trade-off.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ib_portfolio_v2 import run_portfolio, SPECS
from backtest_ib_portfolio_10y import load_long

ROOT = Path(__file__).resolve().parent.parent


def main():
    print("Loading 10Y LONG data...")
    dfs = {sym: load_long(sym) for sym in SPECS.keys()}
    common_all = dfs["MES"].index
    for sym in ["MNQ", "MGC", "MCL"]:
        common_all = common_all.intersection(dfs[sym].index)
    print(f"Period: {common_all[0].date()} -> {common_all[-1].date()}")
    print()

    configs = [
        ("V2 baseline (no kill-switch)",
         dict(regime_kill_switch=False)),
        ("V2 + KS pause=-15% resume=-8% (90j)",
         dict(regime_kill_switch=True, ks_pause_threshold=-0.15, ks_resume_threshold=-0.08, ks_window=90)),
        ("V2 + KS pause=-10% resume=-5% (90j)",
         dict(regime_kill_switch=True, ks_pause_threshold=-0.10, ks_resume_threshold=-0.05, ks_window=90)),
        ("V2 + KS pause=-20% resume=-10% (90j)",
         dict(regime_kill_switch=True, ks_pause_threshold=-0.20, ks_resume_threshold=-0.10, ks_window=90)),
        ("V2 + KS pause=-15% resume=-8% (60j)",
         dict(regime_kill_switch=True, ks_pause_threshold=-0.15, ks_resume_threshold=-0.08, ks_window=60)),
    ]

    results = []
    for label, kwargs in configs:
        print(f"Running: {label}")
        r = run_portfolio(dfs, common_all, use_first_refusal=True, apply_slippage=True,
                          label=label, **kwargs)
        results.append((label, r))

    print()
    print("=" * 95)
    print("  IMPACT DU KILL-SWITCH REGIME — V2 10Y (first-refusal + slippage)")
    print("=" * 95)
    print(f"{'Config':<45s} {'CAGR':>8s} {'Sharpe':>8s} {'MaxDD':>8s} {'Trades':>8s} {'Paused':>8s}")
    print("-" * 95)
    for label, r in results:
        paused = r.get('days_paused', 0)
        print(f"{label:<45s} "
              f"{r['roc_annual']*100:>7.1f}% "
              f"{r['sharpe']:>8.2f} "
              f"{r['max_dd']*100:>7.1f}% "
              f"{r['n_trades']:>8d} "
              f"{paused:>7d}d")

    # Per year impact for the -15%/-8% variant (user's chosen config)
    ks_result = results[1][1]  # V2 + KS pause=-15% resume=-8% 90j
    base_result = results[0][1]
    print()
    print("=" * 80)
    print("  Par annee — V2 baseline vs V2 + kill-switch (-15%/-8% 90j)")
    print("=" * 80)
    print(f"{'Annee':<8s} {'V2 base':>14s} {'V2 + KS':>14s} {'Diff':>14s}")
    all_years = sorted(set(base_result['per_year'].index) | set(ks_result['per_year'].index))
    for y in all_years:
        base_y = base_result['per_year'].loc[y, 'total'] if y in base_result['per_year'].index else 0
        ks_y = ks_result['per_year'].loc[y, 'total'] if y in ks_result['per_year'].index else 0
        diff = ks_y - base_y
        print(f"{y:<8d} {'$+'+f'{base_y:,.0f}' if base_y>=0 else '$'+f'{base_y:,.0f}':>14s} "
              f"{'$+'+f'{ks_y:,.0f}' if ks_y>=0 else '$'+f'{ks_y:,.0f}':>14s} "
              f"{'$+'+f'{diff:,.0f}' if diff>=0 else '$'+f'{diff:,.0f}':>14s}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
