#!/usr/bin/env python3
"""Re-WF us_sector_ls_40_5 sur ETF SPDR + borrow 1%/an (mission 2026-04-30).

Test des dettes registry:
  - shorts_sectors_pdt_rule_borrow_cost  -> borrow 1% annuel modelise
  - re_wf_etf_data_requis_vs_500_tickers -> univers 11 ETF SPDR vs 500 tickers

Variantes testees:
  - 1L/1S top/bottom (replique exacte du 40_5 mais sur ETF)
  - 3L/3S top3/bottom3 (diversification edge)
  - 5L/5S top5/bottom5 (max diversification sur 11 ETF)
  - 3 lookbacks: 20, 40, 60

Borrow cost: 1.0% annuel (vs 0.5% du run du 24/04). Realiste pour secteurs SPDR.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "research" / "target_alpha_us_sectors_2026_04_24_prices.parquet"
OUT = ROOT / "reports" / "research" / "_alpaca_etf_rewfs_2026-04-30_metrics.json"

SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
RT_COST_PCT = 0.0010  # 10 bps
BORROW_ANNUAL = 0.0100  # 1% as requested by Marc (ed2)
BORROW_DAILY = BORROW_ANNUAL / 252.0
LEG_NOTIONAL = 1_000.0


def sharpe(pnl: pd.Series) -> float:
    pnl = pnl.dropna()
    if len(pnl) < 5 or pnl.std() == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(252))


def max_dd_pct(pnl: pd.Series, init: float = 10_000.0) -> float:
    if len(pnl) == 0:
        return 0.0
    eq = init + pnl.cumsum()
    peak = eq.cummax()
    return float(((eq - peak) / peak).min()) * 100


def variant_ls(prices: pd.DataFrame, lookback: int, hold_days: int, n_leg: int, label: str) -> pd.Series:
    """L/S sector momentum: top n_leg longs, bottom n_leg shorts, equal weight."""
    rets = prices.pct_change()
    mom = (1.0 + rets).rolling(lookback).apply(np.prod, raw=True) - 1.0
    target = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns, dtype=float)
    for i, dt in enumerate(prices.index):
        if i < lookback:
            continue
        if (i - lookback) % hold_days != 0:
            continue
        ranks = mom.loc[dt].dropna().sort_values()
        if len(ranks) < 2 * n_leg:
            continue
        vec = pd.Series(0.0, index=prices.columns)
        for s in ranks.index[-n_leg:]:
            vec[s] = 1.0 / n_leg
        for s in ranks.index[:n_leg]:
            vec[s] = -1.0 / n_leg
        target.loc[dt] = vec
    target = target.ffill(limit=max(hold_days - 1, 0)).fillna(0.0)
    pos = target.shift(1).fillna(0.0)
    cost = pos.diff().abs().fillna(pos.abs()) * LEG_NOTIONAL * RT_COST_PCT
    borrow = pos.clip(upper=0).abs() * LEG_NOTIONAL * BORROW_DAILY
    pnl = (pos * rets * LEG_NOTIONAL).sum(axis=1) - cost.sum(axis=1) - borrow.sum(axis=1)
    pnl.name = label
    return pnl


def walk_forward(pnl: pd.Series, n_splits: int = 5) -> dict:
    pnl = pnl.dropna()
    if len(pnl) < 400:
        return {"n_splits": 0, "validated": False, "ratio": 0.0, "windows": []}
    step = len(pnl) // (n_splits + 1)
    windows = []
    for i in range(1, n_splits + 1):
        oos = pnl.iloc[i * step:min((i + 1) * step, len(pnl))]
        if len(oos) < 40:
            continue
        sd = oos.std()
        sr = float(oos.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
        total = float(oos.sum())
        windows.append({
            "window": i,
            "sharpe": round(sr, 3),
            "pnl": round(total, 2),
            "profitable": bool(total > 0 and sr > 0.2),
        })
    profitable = sum(1 for w in windows if w["profitable"])
    ratio = profitable / len(windows) if windows else 0.0
    median_sharpe = float(np.median([w["sharpe"] for w in windows])) if windows else 0.0
    return {
        "n_splits": len(windows),
        "profitable": profitable,
        "ratio": round(ratio, 2),
        "median_sharpe": round(median_sharpe, 3),
        "validated": ratio >= 0.5,
        "windows": windows,
    }


def grade(sharpe_val: float, wf_ratio: float, dd: float) -> str:
    """Grade S/A/B/C/REJECTED based on standalone metrics."""
    if sharpe_val < 0 or wf_ratio < 0.4:
        return "REJECTED"
    if sharpe_val >= 1.0 and wf_ratio >= 0.8 and dd > -10:
        return "S"
    if sharpe_val >= 0.7 and wf_ratio >= 0.6 and dd > -15:
        return "A"
    if sharpe_val >= 0.4 and wf_ratio >= 0.5:
        return "B"
    return "C"


def main() -> int:
    print("=== RE-WF us_sector_ls on ETF SPDR + borrow 1% ===")
    if not CACHE.exists():
        print(f"FATAL: cache not found at {CACHE}")
        return 1
    prices = pd.read_parquet(CACHE)
    prices = prices[[c for c in SECTORS if c in prices.columns]].dropna(how="all")
    print(f"Universe: {prices.shape[1]} ETF, {prices.shape[0]} days "
          f"({prices.index.min().date()} -> {prices.index.max().date()})")

    configs = []
    for n_leg in [1, 3, 5]:
        for lookback in [20, 40, 60]:
            for hold_days in [5, 10]:
                configs.append((n_leg, lookback, hold_days))

    results = {}
    for n_leg, lb, hd in configs:
        label = f"etf_ls_{n_leg}L{n_leg}S_{lb}_{hd}"
        pnl = variant_ls(prices, lb, hd, n_leg, label)
        st = {
            "n_days": len(pnl.dropna()),
            "total_pnl": round(float(pnl.sum()), 2),
            "sharpe": round(sharpe(pnl), 3),
            "max_dd_pct": round(max_dd_pct(pnl), 2),
        }
        wf = walk_forward(pnl)
        gr = grade(st["sharpe"], wf["ratio"], st["max_dd_pct"])
        results[label] = {
            "params": {"n_leg": n_leg, "lookback": lb, "hold_days": hd},
            "standalone": st,
            "wf": wf,
            "grade": gr,
        }
        print(f"{label:30s} | Sh={st['sharpe']:+.2f} | DD={st['max_dd_pct']:+.1f}% | "
              f"WF={wf['profitable']}/{wf['n_splits']} ({wf['ratio']:.0%}) | "
              f"PnL=${st['total_pnl']:+.0f} | grade={gr}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "mission": "Re-WF us_sector_ls on ETF SPDR + borrow 1%/an",
        "date": "2026-04-30",
        "universe": SECTORS,
        "n_days": len(prices),
        "borrow_annual_pct": BORROW_ANNUAL * 100,
        "rt_cost_pct": RT_COST_PCT * 100,
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved -> {OUT}")

    # Best + verdict
    valid = [(k, v) for k, v in results.items() if v["grade"] != "REJECTED"]
    if not valid:
        print("\nVERDICT: REJECTED (no config beats grade C)")
        return 0
    best = max(valid, key=lambda kv: (kv[1]["wf"]["ratio"], kv[1]["standalone"]["sharpe"]))
    print(f"\nBest non-rejected: {best[0]} grade={best[1]['grade']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
