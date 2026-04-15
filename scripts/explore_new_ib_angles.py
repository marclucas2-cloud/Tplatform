#!/usr/bin/env python3
"""Explore new IBKR-tradable strategies angles not previously tested.

Three angles:
  1. Dual-EMA crossover (classic trend with proper params)
  2. Mean reversion RSI2 on MES (Connors, never tested on futures)
  3. Relative Strength MES vs MNQ (rotate long on winner)
  4. Momentum of Momentum (accelerating trends)
  5. Volatility contraction breakout (BB squeeze)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    "MES": {"mult": 5.0, "tick": 0.25, "cost_rt": 2.49},
    "MNQ": {"mult": 2.0, "tick": 0.25, "cost_rt": 1.74},
    "M2K": {"mult": 5.0, "tick": 0.10, "cost_rt": 1.74},
}


def load(sym):
    f = ROOT / "data" / "futures" / f"{sym}_1D.parquet"
    df = pd.read_parquet(f)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def compute_stats(pnls, label=""):
    n = len(pnls)
    if n < 20:
        return {"label": label, "n": n, "sharpe": 0, "total": 0}
    arr = np.array(pnls)
    total = float(arr.sum())
    wr = float((arr > 0).mean())
    mu = float(arr.mean())
    sd = float(arr.std())
    sharpe = mu / sd * np.sqrt(252) if sd > 0 else 0
    cum = arr.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {
        "label": label,
        "n": n, "wr": round(wr, 2), "total": round(total, 0),
        "avg": round(mu, 2),
        "sharpe": round(sharpe, 2),
        "mdd": round(mdd, 0),
    }


def simulate_sl_tp(df, entry_idx, side, entry_px, sl, tp, max_hold=10):
    high = df["high"].values
    low = df["low"].values
    open_ = df["open"].values
    close = df["close"].values
    for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
        h, l, o = float(high[j]), float(low[j]), float(open_[j])
        if j > entry_idx:
            if side == "BUY":
                if o <= sl: return j, o
                if o >= tp: return j, o
            else:
                if o >= sl: return j, o
                if o <= tp: return j, o
        if side == "BUY":
            sl_hit = l <= sl
            tp_hit = h >= tp
            if sl_hit and tp_hit: return j, sl
            if sl_hit: return j, sl
            if tp_hit: return j, tp
        else:
            sl_hit = h >= sl
            tp_hit = l <= tp
            if sl_hit and tp_hit: return j, sl
            if sl_hit: return j, sl
            if tp_hit: return j, tp
    end_idx = min(entry_idx + max_hold - 1, len(df) - 1)
    return end_idx, float(close[end_idx])


# ==================================================================
# Strat 1 — Dual EMA crossover (10/30) on MES
# ==================================================================
def dual_ema_crossover(df, sym, fast=10, slow=30, sl_pts=50, tp_pts=100):
    spec = SPECS[sym]
    cost = spec["cost_rt"]
    ema_f = df["close"].ewm(span=fast).mean()
    ema_s = df["close"].ewm(span=slow).mean()
    pnls = []
    position = None
    for i in range(slow, len(df) - 1):
        bull = ema_f.iloc[i] > ema_s.iloc[i]
        prev_bull = ema_f.iloc[i - 1] > ema_s.iloc[i - 1]

        if position is None:
            if bull and not prev_bull:
                # Enter long
                entry_idx = i + 1
                if entry_idx >= len(df): break
                entry_px = float(df["open"].iloc[entry_idx])
                sl = entry_px - sl_pts
                tp = entry_px + tp_pts
                exit_idx, exit_px = simulate_sl_tp(df, entry_idx, "BUY", entry_px, sl, tp, max_hold=20)
                pnl = (exit_px - entry_px) * spec["mult"] - cost
                pnls.append(pnl)
    return pnls


# ==================================================================
# Strat 2 — RSI2 mean reversion on MES
# ==================================================================
def rsi2_mr(df, sym, rsi_entry=10, rsi_exit=65, sl_pct=0.01, max_hold=5):
    spec = SPECS[sym]
    cost = spec["cost_rt"]
    close = df["close"]
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    avg_up = up.rolling(2).mean()
    avg_dn = dn.rolling(2).mean()
    rs = avg_up / avg_dn.replace(0, np.nan)
    rsi2 = 100 - 100 / (1 + rs)
    sma200 = close.rolling(200).mean()

    pnls = []
    i = 200
    while i < len(df) - 1:
        if close.iloc[i] > sma200.iloc[i] and rsi2.iloc[i] < rsi_entry:
            entry_idx = i + 1
            if entry_idx >= len(df): break
            entry_px = float(df["open"].iloc[entry_idx])
            sl = entry_px * (1 - sl_pct)

            exit_px = None
            exit_idx = None
            for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
                if df["low"].iloc[j] <= sl:
                    exit_idx = j; exit_px = sl; break
                if rsi2.iloc[j] > rsi_exit:
                    exit_idx = j; exit_px = float(df["close"].iloc[j]); break
            if exit_idx is None:
                exit_idx = min(entry_idx + max_hold - 1, len(df) - 1)
                exit_px = float(df["close"].iloc[exit_idx])

            pnl_pts = exit_px - entry_px
            pnl = pnl_pts * spec["mult"] - cost
            pnls.append(pnl)
            i = exit_idx + 1
        else:
            i += 1
    return pnls


# ==================================================================
# Strat 3 — Relative Strength MES vs MNQ (rotate)
# ==================================================================
def rs_mes_mnq(mes, mnq, lookback=5, hold=5):
    """Long the stronger of MES/MNQ over lookback period."""
    mes_spec = SPECS["MES"]
    mnq_spec = SPECS["MNQ"]
    common = mes.index.intersection(mnq.index)
    mes_c = mes["close"].reindex(common)
    mnq_c = mnq["close"].reindex(common)
    mes_ret = mes_c.pct_change(lookback)
    mnq_ret = mnq_c.pct_change(lookback)

    pnls = []
    last = -100
    for i in range(lookback, len(common) - hold):
        if i - last < hold:
            continue
        mr = float(mes_ret.iloc[i])
        nr = float(mnq_ret.iloc[i])
        if not (np.isfinite(mr) and np.isfinite(nr)):
            continue
        if abs(mr - nr) < 0.005:
            continue
        date = common[i]
        if mr > nr:
            sym = "MES"
            spec = mes_spec
            entry_px = float(mes_c.iloc[i])
            exit_px = float(mes_c.iloc[i + hold])
        else:
            sym = "MNQ"
            spec = mnq_spec
            entry_px = float(mnq_c.iloc[i])
            exit_px = float(mnq_c.iloc[i + hold])
        pnl = (exit_px - entry_px) * spec["mult"] - spec["cost_rt"]
        pnls.append(pnl)
        last = i
    return pnls


# ==================================================================
# Strat 4 — Volatility Contraction Breakout (BB Squeeze)
# ==================================================================
def bb_squeeze(df, sym, period=20, z_entry=1.5, sl_pts=50, tp_pts=100):
    spec = SPECS[sym]
    cost = spec["cost_rt"]
    close = df["close"]
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    bb_width = (upper - lower) / mid
    bb_width_ma = bb_width.rolling(100).mean()
    is_squeeze = bb_width < bb_width_ma * 0.7

    pnls = []
    for i in range(100, len(df) - 1):
        if not bool(is_squeeze.iloc[i - 1]):
            continue
        # Break upside
        if close.iloc[i] > upper.iloc[i]:
            entry_idx = i + 1
            if entry_idx >= len(df): break
            entry_px = float(df["open"].iloc[entry_idx])
            sl = entry_px - sl_pts
            tp = entry_px + tp_pts
            exit_idx, exit_px = simulate_sl_tp(df, entry_idx, "BUY", entry_px, sl, tp, max_hold=10)
            pnl = (exit_px - entry_px) * spec["mult"] - cost
            pnls.append(pnl)
    return pnls


# ==================================================================
# Strat 5 — Momentum of Momentum (MoM)
# ==================================================================
def mom_of_mom(df, sym, short=10, long=50, sl_pct=0.015, tp_pct=0.03):
    """Buy when short-term momentum > long-term momentum AND both > 0."""
    spec = SPECS[sym]
    cost = spec["cost_rt"]
    close = df["close"]
    mom_short = close.pct_change(short)
    mom_long = close.pct_change(long)

    pnls = []
    last = -100
    for i in range(long, len(df) - 1):
        if i - last < 3:
            continue
        ms = float(mom_short.iloc[i])
        ml = float(mom_long.iloc[i])
        if not (np.isfinite(ms) and np.isfinite(ml)):
            continue
        if ms > ml and ms > 0.02 and ml > 0:
            entry_idx = i + 1
            if entry_idx >= len(df): break
            entry_px = float(df["open"].iloc[entry_idx])
            sl = entry_px * (1 - sl_pct)
            tp = entry_px * (1 + tp_pct)
            exit_idx, exit_px = simulate_sl_tp(df, entry_idx, "BUY", entry_px, sl, tp, max_hold=15)
            pnl_pts = exit_px - entry_px
            pnl = pnl_pts * spec["mult"] - cost
            pnls.append(pnl)
            last = exit_idx
    return pnls


def main():
    print("Loading data…")
    mes = load("MES")
    mnq = load("MNQ")
    m2k = load("M2K")

    results = []

    # Strat 1: Dual EMA crossover
    for sym, df in [("MES", mes), ("MNQ", mnq), ("M2K", m2k)]:
        for sl, tp in [(30, 60), (50, 100), (80, 150)]:
            pnls = dual_ema_crossover(df, sym, sl_pts=sl, tp_pts=tp)
            s = compute_stats(pnls, f"dual_ema_{sym}_{sl}_{tp}")
            results.append(s)

    # Strat 2: RSI2 MR
    for sym, df in [("MES", mes), ("MNQ", mnq), ("M2K", m2k)]:
        pnls = rsi2_mr(df, sym)
        s = compute_stats(pnls, f"rsi2_{sym}")
        results.append(s)

    # Strat 3: RS MES vs MNQ
    for lb in [3, 5, 10]:
        for hold in [3, 5, 10]:
            pnls = rs_mes_mnq(mes, mnq, lookback=lb, hold=hold)
            s = compute_stats(pnls, f"rs_mes_mnq_{lb}_{hold}")
            results.append(s)

    # Strat 4: BB Squeeze
    for sym, df in [("MES", mes), ("MNQ", mnq)]:
        for sl, tp in [(50, 100), (80, 150)]:
            pnls = bb_squeeze(df, sym, sl_pts=sl, tp_pts=tp)
            s = compute_stats(pnls, f"bb_squeeze_{sym}_{sl}_{tp}")
            results.append(s)

    # Strat 5: Momentum of momentum
    for sym, df in [("MES", mes), ("MNQ", mnq)]:
        pnls = mom_of_mom(df, sym)
        s = compute_stats(pnls, f"mom_mom_{sym}")
        results.append(s)

    # Report
    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    print("\n=== ALL RESULTS (sorted by Sharpe) ===")
    print(df_res[df_res["n"] >= 20].to_string(index=False))
    print()
    print(f"Combos with Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")
    print(f"Combos with Sharpe > 1.0: {(df_res['sharpe'] > 1.0).sum()}")

    df_res.to_csv(ROOT / "reports" / "research" / "explore_new_angles.csv", index=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
