#!/usr/bin/env python3
"""Deep-dive new angles: multi-timeframe momentum, gold-equity mean reversion,
MCL contango, MES short setups, cross-asset momentum.

All tested with realistic costs + require >= 40 trades for statistical validity.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    "MES": {"mult": 5.0, "cost_rt": 2.49},
    "MNQ": {"mult": 2.0, "cost_rt": 1.74},
    "M2K": {"mult": 5.0, "cost_rt": 1.74},
    "MGC": {"mult": 10.0, "cost_rt": 2.49},
    "MCL": {"mult": 100.0, "cost_rt": 2.49},
}


def load(sym):
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def stats(pnls, label):
    n = len(pnls)
    if n < 40:
        return None
    arr = np.array(pnls)
    total = float(arr.sum())
    wr = float((arr > 0).mean())
    mu = float(arr.mean())
    sd = float(arr.std())
    sharpe = mu / sd * np.sqrt(252) if sd > 0 else 0
    return {
        "label": label, "n": n, "wr": round(wr, 2),
        "total": round(total, 0), "sharpe": round(sharpe, 2),
    }


def simulate_pct(df, entry_idx, side, entry_px, sl_pct, tp_pct, max_hold=10):
    sl_abs = entry_px * (1 - sl_pct) if side == "BUY" else entry_px * (1 + sl_pct)
    tp_abs = entry_px * (1 + tp_pct) if side == "BUY" else entry_px * (1 - tp_pct)
    for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
        h = float(df["high"].iloc[j])
        l = float(df["low"].iloc[j])
        o = float(df["open"].iloc[j])
        if j > entry_idx:
            if side == "BUY":
                if o <= sl_abs: return j, o
                if o >= tp_abs: return j, o
            else:
                if o >= sl_abs: return j, o
                if o <= tp_abs: return j, o
        if side == "BUY":
            if l <= sl_abs and h >= tp_abs: return j, sl_abs
            if l <= sl_abs: return j, sl_abs
            if h >= tp_abs: return j, tp_abs
        else:
            if h >= sl_abs and l <= tp_abs: return j, sl_abs
            if h >= sl_abs: return j, sl_abs
            if l <= tp_abs: return j, tp_abs
    end = min(entry_idx + max_hold - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


# ==================================================================
# Multi-timeframe momentum (daily + weekly confirmation)
# ==================================================================
def multi_tf_mom(df, sym, fast_daily=10, weekly_confirm=3, sl_pct=0.015, tp_pct=0.03):
    spec = SPECS[sym]
    close = df["close"]
    # Weekly resample
    weekly = close.resample("W").last().dropna()
    weekly_ret = weekly.pct_change(weekly_confirm)
    weekly_bull = (weekly_ret > 0).reindex(df.index, method="ffill").fillna(False)

    # Daily momentum
    daily_mom = close.pct_change(fast_daily)

    pnls = []
    last = -100
    for i in range(100, len(df) - 1):
        if i - last < 5:
            continue
        d = close.index[i]
        if not bool(weekly_bull.iloc[i]):
            continue
        dm = float(daily_mom.iloc[i])
        if not np.isfinite(dm) or dm < 0.01:
            continue
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(df["open"].iloc[entry_idx])
        exit_idx, exit_px = simulate_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=10)
        pnl_pts = exit_px - entry_px
        pnl = pnl_pts * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
        last = exit_idx
    return pnls


# ==================================================================
# Gold/Equity mean reversion (spread trade)
# ==================================================================
def gold_equity_mr(mes, mgc, lookback=20, z_entry=2.0, z_exit=0.5, sl_pct=0.02):
    common = mes.index.intersection(mgc.index)
    mes_c = mes["close"].reindex(common)
    mgc_c = mgc["close"].reindex(common)
    # Normalized ratio (log)
    ratio = np.log(mes_c) - np.log(mgc_c)
    mean = ratio.rolling(lookback).mean()
    std = ratio.rolling(lookback).std()
    z = (ratio - mean) / std

    mes_spec = SPECS["MES"]
    mgc_spec = SPECS["MGC"]
    pnls = []
    last = -100
    for i in range(lookback, len(common) - 1):
        if i - last < 3:
            continue
        zi = float(z.iloc[i]) if np.isfinite(z.iloc[i]) else 0
        # Reversion trade: spread too high → short MES, long MGC
        if zi > z_entry:
            # Short MES
            entry_idx = common[i + 1] if i + 1 < len(common) else None
            if entry_idx is None: continue
            mes_idx = mes.index.get_loc(entry_idx)
            entry_px = float(mes["open"].iloc[mes_idx])
            exit_idx, exit_px = simulate_pct(mes, mes_idx, "SELL", entry_px, sl_pct, sl_pct * 1.5)
            pnl = (entry_px - exit_px) * mes_spec["mult"] - mes_spec["cost_rt"]
            pnls.append(pnl)
            last = i
        elif zi < -z_entry:
            # Long MES
            entry_idx = common[i + 1] if i + 1 < len(common) else None
            if entry_idx is None: continue
            mes_idx = mes.index.get_loc(entry_idx)
            entry_px = float(mes["open"].iloc[mes_idx])
            exit_idx, exit_px = simulate_pct(mes, mes_idx, "BUY", entry_px, sl_pct, sl_pct * 1.5)
            pnl = (exit_px - entry_px) * mes_spec["mult"] - mes_spec["cost_rt"]
            pnls.append(pnl)
            last = i
    return pnls


# ==================================================================
# Friday-effect (close Friday, buy Monday open)
# ==================================================================
def friday_monday(df, sym, sl_pct=0.02, tp_pct=0.03):
    spec = SPECS[sym]
    pnls = []
    for i in range(1, len(df) - 2):
        day = df.index[i].weekday()  # Monday=0, Friday=4
        if day != 4:  # Friday
            continue
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(df["open"].iloc[entry_idx])
        exit_idx, exit_px = simulate_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=2)
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
    return pnls


# ==================================================================
# End-of-Month MES rally
# ==================================================================
def eom_rally(df, sym, sl_pct=0.015, tp_pct=0.025):
    spec = SPECS[sym]
    pnls = []
    for i in range(1, len(df) - 5):
        d = df.index[i]
        # Last 3 business days of month
        next_d = df.index[i + 1] if i + 1 < len(df) else None
        if next_d and next_d.month != d.month:
            # d is last day of month
            entry_idx = max(0, i - 2)
            if entry_idx >= len(df) - 5: continue
            entry_px = float(df["open"].iloc[entry_idx])
            exit_idx, exit_px = simulate_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=5)
            pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
            pnls.append(pnl)
    return pnls


# ==================================================================
# VIX contraction + MES long (low VIX = risk-on)
# ==================================================================
def low_vix_long(mes, vix, vix_low=15, sl_pct=0.015, tp_pct=0.025):
    common = mes.index.intersection(vix.index)
    spec = SPECS["MES"]
    pnls = []
    last = -100
    for i in range(20, len(common) - 1):
        if i - last < 3: continue
        vix_val = float(vix["close"].loc[common[i]])
        if vix_val > vix_low:
            continue
        mes_idx = mes.index.get_loc(common[i])
        entry_idx = mes_idx + 1
        if entry_idx >= len(mes): break
        entry_px = float(mes["open"].iloc[entry_idx])
        exit_idx, exit_px = simulate_pct(mes, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=5)
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
        last = i
    return pnls


def main():
    print("Loading data…")
    mes = load("MES"); mnq = load("MNQ"); m2k = load("M2K")
    mgc = load("MGC"); mcl = load("MCL")
    try:
        vix = load("VIX")
    except Exception:
        vix = None

    results = []

    # Multi-TF momentum
    for sym, df in [("MES", mes), ("MNQ", mnq), ("M2K", m2k)]:
        pnls = multi_tf_mom(df, sym)
        s = stats(pnls, f"multi_tf_mom_{sym}")
        if s: results.append(s)

    # Gold/Equity MR
    pnls = gold_equity_mr(mes, mgc)
    s = stats(pnls, "gold_equity_mr")
    if s: results.append(s)

    # Friday-Monday
    for sym, df in [("MES", mes), ("MNQ", mnq), ("M2K", m2k)]:
        pnls = friday_monday(df, sym)
        s = stats(pnls, f"friday_monday_{sym}")
        if s: results.append(s)

    # EOM rally
    for sym, df in [("MES", mes), ("MNQ", mnq), ("M2K", m2k)]:
        pnls = eom_rally(df, sym)
        s = stats(pnls, f"eom_rally_{sym}")
        if s: results.append(s)

    # Low VIX long
    if vix is not None:
        pnls = low_vix_long(mes, vix)
        s = stats(pnls, "low_vix_long_mes")
        if s: results.append(s)

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    print("\n=== DEEP ANGLES RESULTS (n >= 40) ===")
    print(df_res.to_string(index=False))
    print()
    print(f"Candidates Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")

    df_res.to_csv(ROOT / "reports" / "research" / "explore_deep_angles.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
