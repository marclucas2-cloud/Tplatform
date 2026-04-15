#!/usr/bin/env python3
"""Wave 3 exploration: cross-asset momentum, dow effects, volatility regime.

Tests:
  1. Monthly cross-asset momentum (MES vs MNQ vs MGC vs MCL)
  2. Day-of-week effects (Tue/Wed/Thu as entry day)
  3. Volatility regime switching (low vol buy, high vol avoid)
  4. MES/MNQ pairs z-score (spread mean reversion)
  5. Sector rotation via RS weekly ranking
  6. Thursday close → Monday close variant
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


def stats(pnls, label, min_n=30):
    n = len(pnls)
    if n < min_n:
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


def sim_pct(df, eidx, side, epx, sl_pct, tp_pct, max_hold):
    sl = epx * (1 - sl_pct) if side == "BUY" else epx * (1 + sl_pct)
    tp = epx * (1 + tp_pct) if side == "BUY" else epx * (1 - tp_pct)
    for j in range(eidx, min(eidx + max_hold, len(df))):
        h = float(df["high"].iloc[j])
        l = float(df["low"].iloc[j])
        o = float(df["open"].iloc[j])
        if j > eidx:
            if side == "BUY":
                if o <= sl: return j, o
                if o >= tp: return j, o
            else:
                if o >= sl: return j, o
                if o <= tp: return j, o
        if side == "BUY":
            if l <= sl and h >= tp: return j, sl
            if l <= sl: return j, sl
            if h >= tp: return j, tp
        else:
            if h >= sl and l <= tp: return j, sl
            if h >= sl: return j, sl
            if l <= tp: return j, tp
    end = min(eidx + max_hold - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


# 1. Monthly cross-asset momentum top-N
def cross_asset_mom(dfs, lookback=20, hold=20):
    """Each month, long the top-momentum of (MES, MNQ, M2K, MGC, MCL)."""
    common = None
    for sym, df in dfs.items():
        if common is None:
            common = df.index
        else:
            common = common.intersection(df.index)
    if common is None or len(common) < 100:
        return []

    pnls = []
    last_rebal = -100
    for i in range(lookback, len(common) - hold):
        if i - last_rebal < hold:
            continue
        # Compute returns over lookback for each asset
        rets = {}
        for sym, df in dfs.items():
            d = common[i]
            pd_idx = df.index.get_loc(d)
            prev_idx = df.index.get_loc(common[i - lookback])
            rets[sym] = df["close"].iloc[pd_idx] / df["close"].iloc[prev_idx] - 1
        # Pick winner
        winner = max(rets, key=rets.get)
        if rets[winner] < 0.02:  # require min 2% momentum
            continue
        winner_df = dfs[winner]
        widx = winner_df.index.get_loc(common[i])
        if widx + hold >= len(winner_df): break
        entry_px = float(winner_df["open"].iloc[widx + 1]) if widx + 1 < len(winner_df) else float(winner_df["close"].iloc[widx])
        exit_px = float(winner_df["close"].iloc[widx + hold])
        spec = SPECS[winner]
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
        last_rebal = i
    return pnls


# 2. Day-of-week: Wednesday entry
def wed_entry(df, sym, sl_pct=0.015, tp_pct=0.025):
    spec = SPECS[sym]
    pnls = []
    for i in range(1, len(df) - 2):
        if df.index[i].weekday() != 2:  # Wednesday
            continue
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(df["open"].iloc[entry_idx])
        exit_idx, exit_px = sim_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=2)
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
    return pnls


# 3. Tuesday entry
def tue_entry(df, sym, sl_pct=0.015, tp_pct=0.025):
    spec = SPECS[sym]
    pnls = []
    for i in range(1, len(df) - 2):
        if df.index[i].weekday() != 1:  # Tuesday
            continue
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(df["open"].iloc[entry_idx])
        exit_idx, exit_px = sim_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=2)
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
    return pnls


# 4. MES/MNQ pairs z-score trade
def mes_mnq_pairs(mes, mnq, lookback=20, z_entry=2.0, z_exit=0.5, max_hold=10):
    common = mes.index.intersection(mnq.index)
    mes_c = mes["close"].reindex(common)
    mnq_c = mnq["close"].reindex(common)
    # Log spread
    log_mes = np.log(mes_c)
    log_mnq = np.log(mnq_c)
    spread = log_mes - log_mnq
    mean = spread.rolling(lookback).mean()
    std = spread.rolling(lookback).std()
    z = (spread - mean) / std

    mes_spec = SPECS["MES"]
    pnls = []
    in_pos = False
    entry_mes_idx = None
    side_mes = None
    for i in range(lookback, len(common) - 1):
        zi = float(z.iloc[i]) if np.isfinite(z.iloc[i]) else 0
        if not in_pos:
            if zi > z_entry:
                side_mes = "SELL"  # short MES
                in_pos = True
                entry_mes_idx = mes.index.get_loc(common[i])
            elif zi < -z_entry:
                side_mes = "BUY"
                in_pos = True
                entry_mes_idx = mes.index.get_loc(common[i])
        else:
            hold_days = i - lookback  # simplified
            if abs(zi) < z_exit or hold_days > max_hold:
                cur_mes_idx = mes.index.get_loc(common[i])
                entry_px = float(mes["close"].iloc[entry_mes_idx])
                exit_px = float(mes["close"].iloc[cur_mes_idx])
                if side_mes == "BUY":
                    pnl = (exit_px - entry_px) * mes_spec["mult"] - mes_spec["cost_rt"]
                else:
                    pnl = (entry_px - exit_px) * mes_spec["mult"] - mes_spec["cost_rt"]
                pnls.append(pnl)
                in_pos = False
    return pnls


# 5. Thursday close → Monday close
def thu_mon(df, sym, sl_pct=0.025, tp_pct=0.04):
    spec = SPECS[sym]
    pnls = []
    for i in range(1, len(df) - 4):
        if df.index[i].weekday() != 3:  # Thursday
            continue
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(df["open"].iloc[entry_idx])
        exit_idx, exit_px = sim_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=3)
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
    return pnls


# 6. Volatility regime filter (low ATR buy)
def low_vol_long(df, sym, atr_period=14, atr_percentile=30, sl_pct=0.015, tp_pct=0.025):
    spec = SPECS[sym]
    close = df["close"]
    high = df["high"]
    low = df["low"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    atr_pct_rank = atr.rolling(100).apply(lambda x: (x.iloc[-1] < x.quantile(atr_percentile / 100)) * 100 if len(x) > 50 else 0)

    pnls = []
    last = -100
    for i in range(100, len(df) - 1):
        if i - last < 3: continue
        if atr.iloc[i] > atr.iloc[max(0, i - 50):i].quantile(atr_percentile / 100):
            continue
        # Low vol regime → buy
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(df["open"].iloc[entry_idx])
        exit_idx, exit_px = sim_pct(df, entry_idx, "BUY", entry_px, sl_pct, tp_pct, max_hold=5)
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
        last = exit_idx
    return pnls


def main():
    print("Loading data…")
    dfs = {sym: load(sym) for sym in ["MES", "MNQ", "M2K", "MGC", "MCL"]}

    results = []

    # Cross-asset monthly momentum
    pnls = cross_asset_mom(dfs, lookback=20, hold=20)
    s = stats(pnls, "cross_asset_mom_20_20", min_n=20)
    if s: results.append(s)

    pnls = cross_asset_mom(dfs, lookback=60, hold=20)
    s = stats(pnls, "cross_asset_mom_60_20", min_n=20)
    if s: results.append(s)

    # Day-of-week effects
    for day_name, fn in [("tue", tue_entry), ("wed", wed_entry), ("thu", thu_mon)]:
        for sym, df in dfs.items():
            if sym not in ("MES", "MNQ", "M2K"): continue
            pnls = fn(df, sym)
            s = stats(pnls, f"{day_name}_{sym}")
            if s: results.append(s)

    # MES/MNQ pairs
    pnls = mes_mnq_pairs(dfs["MES"], dfs["MNQ"])
    s = stats(pnls, "mes_mnq_pairs")
    if s: results.append(s)

    # Low vol long
    for sym in ["MES", "MNQ"]:
        pnls = low_vol_long(dfs[sym], sym)
        s = stats(pnls, f"low_vol_long_{sym}")
        if s: results.append(s)

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    print("\n=== WAVE 3 RESULTS ===")
    print(df_res.to_string(index=False))
    print()
    print(f"Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")

    df_res.to_csv(ROOT / "reports" / "research" / "explore_wave3.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
