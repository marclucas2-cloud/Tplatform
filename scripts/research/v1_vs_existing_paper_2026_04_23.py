"""Compare v1_mes_mr_vix_spike winner vs existing MES paper sleeves
(mes_monday_long_oc, mes_wednesday_long_oc) to ensure true decorrelation
and novelty — not a disguised version of an existing paper.
"""
from __future__ import annotations
import sys
sys.path.insert(0, '.')
from scripts.research.decorrelated_variants_v2_2026_04_23 import (
    load_futures_long, load_vix_1d, compute_metrics, wf_5splits,
    v1_mes_mr_vix_spike,
)
import pandas as pd
import numpy as np

def backtest_mes_monday_long_oc(mes: pd.DataFrame, comm: float = 0.62,
                                 slip_ticks: float = 1.0, tick_val: float = 1.25) -> pd.Series:
    df = mes.copy()
    df["dow"] = df.index.dayofweek  # 0=Mon
    o = df["open"]; c = df["close"]
    # Open->Close on Monday only
    sig_mon = (df["dow"] == 0).astype(float)
    # return is close[t]/open[t] - 1 on Monday
    daily = pd.Series(0.0, index=df.index)
    daily[sig_mon > 0] = c[sig_mon > 0] / o[sig_mon > 0] - 1
    # Costs: 2 ticks total (entry + exit)
    cost_pct = sig_mon * ((comm * 2 + 2 * slip_ticks * tick_val) / c)
    net = daily - cost_pct
    net.attrs["trades"] = int((sig_mon > 0).sum())
    return net

def backtest_mes_wed_long_oc(mes: pd.DataFrame, comm: float = 0.62,
                              slip_ticks: float = 1.0, tick_val: float = 1.25) -> pd.Series:
    df = mes.copy()
    df["dow"] = df.index.dayofweek
    o = df["open"]; c = df["close"]
    sig = (df["dow"] == 2).astype(float)
    daily = pd.Series(0.0, index=df.index)
    daily[sig > 0] = c[sig > 0] / o[sig > 0] - 1
    cost_pct = sig * ((comm * 2 + 2 * slip_ticks * tick_val) / c)
    net = daily - cost_pct
    net.attrs["trades"] = int((sig > 0).sum())
    return net

def main():
    mes = load_futures_long("MES")
    vix = load_vix_1d()
    print("=" * 80)
    print("v1 vs Existing Paper Sleeves — Comparison + Correlation")
    print("=" * 80)

    v1 = v1_mes_mr_vix_spike(mes, vix, consec=3, hold=4, vix_min=15)
    print(f"\nv1_mes_mr_vix_spike (consec=3/hold=4/vix15): {compute_metrics(v1, v1.attrs.get('trades'))}")
    print(f"  WF: {wf_5splits(v1)}")

    mon = backtest_mes_monday_long_oc(mes)
    print(f"\nmes_monday_long_oc: {compute_metrics(mon, mon.attrs.get('trades'))}")
    print(f"  WF: {wf_5splits(mon)}")

    wed = backtest_mes_wed_long_oc(mes)
    print(f"\nmes_wednesday_long_oc: {compute_metrics(wed, wed.attrs.get('trades'))}")
    print(f"  WF: {wf_5splits(wed)}")

    # Correlation of daily returns
    df = pd.DataFrame({
        "v1_mes_mr_vix_spike": v1,
        "mes_monday_long_oc": mon,
        "mes_wednesday_long_oc": wed,
    }).dropna(how="all")
    print("\nCorrelation:")
    print(df.corr(min_periods=100).round(3))

    # Overlap: days where both strats are LONG
    v1_long = (v1 != 0).astype(int)
    mon_long = (mon != 0).astype(int)
    overlap_mon = (v1_long & mon_long).sum()
    mon_total = mon_long.sum()
    v1_total = v1_long.sum()
    print(f"\nOverlap v1 x mon (both long same day): {overlap_mon} days")
    print(f"  v1 long days: {v1_total}, mon long days: {mon_total}")
    print(f"  overlap ratio vs v1: {overlap_mon/v1_total:.2%}")
    print(f"  overlap ratio vs mon: {overlap_mon/mon_total:.2%}")


if __name__ == "__main__":
    main()
