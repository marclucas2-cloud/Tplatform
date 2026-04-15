#!/usr/bin/env python3
"""Backtest portefeuille IB 10Y (2015-2026) — V1/V2/V3 comparaison.

Utilise les fichiers *_LONG.parquet qui couvrent ~11 ans:
  - MES, MNQ, MGC, MCL: 2015-01-02 -> 2026-04-09
  - M2K: 2017-07-10 -> 2026-04-09 (exclu du backtest, demarre trop tard)

Mode V3 (retenu par l'user): first-refusal + NO gold_trend.
Compare aussi V1 baseline et V2 pour mesurer la robustesse sur 10 ans.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse V2 helpers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ib_portfolio_v2 import (
    SPECS,
    INITIAL_EQUITY,
    RISK_BUDGET_PCT,
    MAX_SYMBOLS,
    sim_exit,
    cross_asset_top_pick,
    signal_cross_asset,
    signal_gold_trend,
    signal_gold_oil,
    run_portfolio,
    print_result,
)

ROOT = Path(__file__).resolve().parent.parent


def load_long(sym):
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_LONG.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def main():
    print("Chargement data LONG (10Y)...")
    # Use LONG files for all 5 symbols. M2K starts 2017-07 so will restrict
    # the universe slightly, but cross_asset_momentum handles missing data.
    dfs = {}
    for sym in SPECS.keys():
        dfs[sym] = load_long(sym)
        print(f"  {sym}: {len(dfs[sym]):5d} bars  {dfs[sym].index.min().date()} -> {dfs[sym].index.max().date()}")

    # Common index: intersection of MES/MNQ/MGC/MCL (exclude M2K to get full history)
    # M2K will be None for early dates — cross_asset_momentum skips missing assets
    common_all = dfs["MES"].index
    for sym in ["MNQ", "MGC", "MCL"]:
        common_all = common_all.intersection(dfs[sym].index)
    print(f"\nCommon period: {common_all[0].date()} -> {common_all[-1].date()} ({len(common_all)} bars)")
    years = (common_all[-1] - common_all[0]).days / 365.25
    print(f"Duration: {years:.1f} years")

    # V1 baseline
    r1 = run_portfolio(dfs, common_all, use_first_refusal=False, label="V1 BASELINE 10Y")
    # V2 first-refusal
    r2 = run_portfolio(dfs, common_all, use_first_refusal=True, label="V2 OPT1 FirstRefusal 10Y")
    # V3 first-refusal + no gold_trend
    r3 = run_portfolio(dfs, common_all, use_first_refusal=True, disable_gold_trend=True,
                       label="V3 OPT2 NoGoldTrend 10Y")

    print_result(r1)
    print_result(r2)
    print_result(r3)

    # Compare
    print("\n" + "=" * 80)
    print("  COMPARAISON V1 vs V2 vs V3 — 10 ans")
    print("=" * 80)

    def fmt_year(r, y):
        return f"${r['per_year'].loc[y,'total']:+,.0f}" if y in r['per_year'].index else "n/a"

    rows = [
        ("PnL total",
         f"${r1['total_pnl']:+,.0f}", f"${r2['total_pnl']:+,.0f}", f"${r3['total_pnl']:+,.0f}"),
        ("Final equity",
         f"${r1['final_equity']:,.0f}", f"${r2['final_equity']:,.0f}", f"${r3['final_equity']:,.0f}"),
        ("CAGR",
         f"{r1['roc_annual']*100:.1f}%", f"{r2['roc_annual']*100:.1f}%", f"{r3['roc_annual']*100:.1f}%"),
        ("Sharpe",
         f"{r1['sharpe']:.2f}", f"{r2['sharpe']:.2f}", f"{r3['sharpe']:.2f}"),
        ("Max DD",
         f"{r1['max_dd']*100:.1f}%", f"{r2['max_dd']*100:.1f}%", f"{r3['max_dd']*100:.1f}%"),
        ("Trades",
         f"{r1['n_trades']}", f"{r2['n_trades']}", f"{r3['n_trades']}"),
        ("Win rate",
         f"{r1['wr']*100:.1f}%", f"{r2['wr']*100:.1f}%", f"{r3['wr']*100:.1f}%"),
    ]
    print(f"{'':22s} {'V1 baseline':>14s} {'V2 Opt1':>14s} {'V3 NoGoldTrend':>16s}")
    for row in rows:
        print(f"{row[0]:22s} {row[1]:>14s} {row[2]:>14s} {row[3]:>16s}")

    # Per year comparison
    print(f"\n{'Annee':<8s} {'V1':>14s} {'V2':>14s} {'V3':>14s}")
    all_years = sorted(set(r1['per_year'].index) | set(r2['per_year'].index) | set(r3['per_year'].index))
    for y in all_years:
        print(f"{y:<8d} {fmt_year(r1,y):>14s} {fmt_year(r2,y):>14s} {fmt_year(r3,y):>14s}")

    # Save
    out = ROOT / "reports" / "research"
    r3['df_trades'].to_csv(out / "ib_portfolio_10y_v3_trades.csv", index=False)
    print(f"\n[ok] V3 trades saved to {out}/ib_portfolio_10y_v3_trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
