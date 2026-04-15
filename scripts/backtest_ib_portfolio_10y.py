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
    df = df.sort_index()
    # Drop bars with impossible prices (MCL 2020-04-20/21 WTI negative crash).
    # These are real historical prints but cannot be used by signal logic that
    # divides by price or computes percentage moves across them.
    bad = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if bad.any():
        print(f"  {sym}: drop {int(bad.sum())} pathological bars (non-positive prices)")
        df = df[~bad]
    return df


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

    # V1 baseline (no slippage)
    r1 = run_portfolio(dfs, common_all, use_first_refusal=False, label="V1 BASELINE 10Y (no slip)")
    # V2 first-refusal (no slippage)
    r2 = run_portfolio(dfs, common_all, use_first_refusal=True, label="V2 OPT1 10Y (no slip)")
    # V2 with realistic slippage
    r2s = run_portfolio(dfs, common_all, use_first_refusal=True, apply_slippage=True,
                        label="V2 OPT1 10Y (+ slippage 2 ticks)")
    # V1 with realistic slippage (for comparison)
    r1s = run_portfolio(dfs, common_all, use_first_refusal=False, apply_slippage=True,
                        label="V1 BASELINE 10Y (+ slippage 2 ticks)")

    print_result(r1)
    print_result(r2)
    print_result(r1s)
    print_result(r2s)

    # Compare — focus on V1 vs V2, with/without slippage
    print("\n" + "=" * 88)
    print("  COMPARAISON 10Y — impact data fix MCL + slippage")
    print("=" * 88)

    def fmt_year(r, y):
        return f"${r['per_year'].loc[y,'total']:+,.0f}" if y in r['per_year'].index else "n/a"

    configs = [("V1 no-slip", r1), ("V2 no-slip", r2), ("V1 +slip", r1s), ("V2 +slip", r2s)]

    rows = [
        ("PnL total",    [f"${r['total_pnl']:+,.0f}" for _, r in configs]),
        ("Final equity", [f"${r['final_equity']:,.0f}" for _, r in configs]),
        ("CAGR",         [f"{r['roc_annual']*100:.1f}%" for _, r in configs]),
        ("Sharpe",       [f"{r['sharpe']:.2f}" for _, r in configs]),
        ("Max DD",       [f"{r['max_dd']*100:.1f}%" for _, r in configs]),
        ("Trades",       [f"{r['n_trades']}" for _, r in configs]),
        ("Win rate",     [f"{r['wr']*100:.1f}%" for _, r in configs]),
    ]
    headers = [name for name, _ in configs]
    print(f"{'':20s} " + " ".join(f"{h:>14s}" for h in headers))
    for label, vals in rows:
        print(f"{label:20s} " + " ".join(f"{v:>14s}" for v in vals))

    print(f"\nImpact slippage V2:")
    slip_impact_pnl = r2s['total_pnl'] - r2['total_pnl']
    slip_impact_cagr = (r2s['roc_annual'] - r2['roc_annual']) * 100
    print(f"  PnL: {slip_impact_pnl:+,.0f} ({slip_impact_pnl / r2['total_pnl'] * 100:+.1f}%)")
    print(f"  CAGR: {slip_impact_cagr:+.1f} pts")

    # Per year comparison (V2 with slippage = decision-grade config)
    print(f"\n{'Annee':<8s} {'V2 no-slip':>14s} {'V2 +slip':>14s}")
    all_years = sorted(set(r2['per_year'].index) | set(r2s['per_year'].index))
    for y in all_years:
        print(f"{y:<8d} {fmt_year(r2,y):>14s} {fmt_year(r2s,y):>14s}")

    # Save
    out = ROOT / "reports" / "research"
    r2s['df_trades'].to_csv(out / "ib_portfolio_10y_v2_slip_trades.csv", index=False)
    print(f"\n[ok] V2+slip trades saved to {out}/ib_portfolio_10y_v2_slip_trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
