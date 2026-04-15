#!/usr/bin/env python3
"""Correlation matrix of all tested strats (daily PnL series).

Builds a daily PnL series for each strat and computes Pearson correlation.
Helps portfolio construction — avoid doubling exposure on same edge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    "MES": {"mult": 5.0, "cost": 2.49},
    "MNQ": {"mult": 2.0, "cost": 1.74},
    "M2K": {"mult": 5.0, "cost": 1.74},
    "MGC": {"mult": 10.0, "cost": 2.49},
    "MCL": {"mult": 100.0, "cost": 2.49},
}


def load(sym):
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def sim(df, eidx, side, epx, sl_pct, tp_pct, mh):
    sl = epx * (1 - sl_pct) if side == "BUY" else epx * (1 + sl_pct)
    tp = epx * (1 + tp_pct) if side == "BUY" else epx * (1 - tp_pct)
    for j in range(eidx, min(eidx + mh, len(df))):
        h = float(df["high"].iloc[j]); l = float(df["low"].iloc[j]); o = float(df["open"].iloc[j])
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
    end = min(eidx + mh - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


def sim_pts(df, eidx, side, epx, sl_pts, tp_pts, mh):
    return sim(df, eidx, side, epx, sl_pts / epx, tp_pts / epx, mh)


def trades_to_daily(trades, full_index):
    """Convert trade list to daily PnL series aligned on full_index."""
    s = pd.Series(0.0, index=full_index)
    for t in trades:
        d = pd.Timestamp(t["exit_date"]).normalize()
        if d in s.index:
            s[d] += t["pnl"]
        else:
            # Find nearest
            nearest = s.index[s.index.get_indexer([d], method="nearest")[0]]
            s[nearest] += t["pnl"]
    return s


# Strat implementations (simplified)
def strat_v2_mes(mes):
    close = mes["close"].astype(float)
    ema = close.ewm(span=50).mean()
    trades = []
    i = 50
    while i < len(mes) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]): i += 1; continue
        eidx = i + 1
        if eidx >= len(mes): break
        ep = float(mes["open"].iloc[eidx])
        exit_idx, exit_px = sim_pts(mes, eidx, "BUY", ep, 60, 120, 10)
        pnl = (exit_px - ep) * 5 - 2.49
        trades.append({"exit_date": mes.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


def strat_mnq_overnight(mnq):
    close = mnq["close"].astype(float)
    ema = close.ewm(span=40).mean()
    trades = []
    i = 40
    while i < len(mnq) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]): i += 1; continue
        eidx = i + 1
        if eidx >= len(mnq): break
        ep = float(mnq["open"].iloc[eidx])
        exit_idx, exit_px = sim_pts(mnq, eidx, "BUY", ep, 140, 300, 10)
        pnl = (exit_px - ep) * 2 - 1.74
        trades.append({"exit_date": mnq.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


def strat_rs_mes_mnq(mes, mnq):
    common = mes.index.intersection(mnq.index)
    mes_c = mes["close"].reindex(common)
    mnq_c = mnq["close"].reindex(common)
    mes_ret = mes_c.pct_change(3)
    mnq_ret = mnq_c.pct_change(3)
    trades = []
    last = -100
    for i in range(3, len(common) - 5):
        if i - last < 5: continue
        mr = float(mes_ret.iloc[i]); nr = float(mnq_ret.iloc[i])
        if not (np.isfinite(mr) and np.isfinite(nr)): continue
        if abs(mr - nr) < 0.005: continue
        if mr > nr:
            entry = float(mes_c.iloc[i]); exit = float(mes_c.iloc[i + 5])
            pnl = (exit - entry) * 5 - 2.49
        else:
            entry = float(mnq_c.iloc[i]); exit = float(mnq_c.iloc[i + 5])
            pnl = (exit - entry) * 2 - 1.74
        trades.append({"exit_date": common[i + 5], "pnl": pnl})
        last = i
    return trades


def strat_gold_trend(mgc):
    close = mgc["close"].astype(float)
    ema = close.ewm(span=20).mean()
    trades = []
    i = 20
    while i < len(mgc) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]): i += 1; continue
        eidx = i + 1
        if eidx >= len(mgc): break
        ep = float(mgc["open"].iloc[eidx])
        exit_idx, exit_px = sim(mgc, eidx, "BUY", ep, 0.015, 0.03, 10)
        pnl = (exit_px - ep) * 10 - 2.49
        trades.append({"exit_date": mgc.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


def strat_cross_asset_mom(dfs):
    common = None
    for df in dfs.values():
        common = df.index if common is None else common.intersection(df.index)
    trades = []
    last = -100
    for i in range(20, len(common) - 20):
        if i - last < 20: continue
        rets = {}
        for sym, df in dfs.items():
            d = common[i]
            pd_idx = df.index.get_loc(d)
            prev = df.index.get_loc(common[i - 20])
            rets[sym] = df["close"].iloc[pd_idx] / df["close"].iloc[prev] - 1
        winner = max(rets, key=rets.get)
        if rets[winner] < 0.02: continue
        wdf = dfs[winner]
        widx = wdf.index.get_loc(common[i])
        if widx + 20 >= len(wdf): break
        entry = float(wdf["open"].iloc[widx + 1]) if widx + 1 < len(wdf) else float(wdf["close"].iloc[widx])
        exit = float(wdf["close"].iloc[widx + 20])
        spec = SPECS[winner]
        pnl = (exit - entry) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": common[i + 20] if i + 20 < len(common) else common[-1], "pnl": pnl})
        last = i
    return trades


def strat_friday_monday_mnq(mnq):
    trades = []
    for i in range(1, len(mnq) - 2):
        if mnq.index[i].weekday() != 4: continue
        eidx = i + 1
        if eidx >= len(mnq): break
        ep = float(mnq["open"].iloc[eidx])
        exit_idx, exit_px = sim(mnq, eidx, "BUY", ep, 0.02, 0.03, 2)
        pnl = (exit_px - ep) * 2 - 1.74
        trades.append({"exit_date": mnq.index[exit_idx], "pnl": pnl})
    return trades


def strat_thursday_rally(df, sym):
    spec = SPECS[sym]
    trades = []
    for i in range(1, len(df) - 4):
        if df.index[i].weekday() != 3: continue
        eidx = i + 1
        if eidx >= len(df): break
        ep = float(df["open"].iloc[eidx])
        exit_idx, exit_px = sim(df, eidx, "BUY", ep, 0.025, 0.04, 3)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
    return trades


def strat_multi_tf_mom(mes):
    close = mes["close"]
    weekly = close.resample("W").last().dropna()
    weekly_ret = weekly.pct_change(3)
    weekly_bull = (weekly_ret > 0).reindex(mes.index, method="ffill").fillna(False).astype(bool)
    daily_mom = close.pct_change(10)
    trades = []
    last = -100
    for i in range(100, len(mes) - 1):
        if i - last < 5: continue
        if not bool(weekly_bull.iloc[i]): continue
        dm = float(daily_mom.iloc[i])
        if not np.isfinite(dm) or dm < 0.01: continue
        eidx = i + 1
        if eidx >= len(mes): break
        ep = float(mes["open"].iloc[eidx])
        exit_idx, exit_px = sim(mes, eidx, "BUY", ep, 0.015, 0.03, 10)
        pnl = (exit_px - ep) * 5 - 2.49
        trades.append({"exit_date": mes.index[exit_idx], "pnl": pnl})
        last = exit_idx
    return trades


def strat_gold_vix_long(mgc, vix):
    common = mgc.index.intersection(vix.index)
    vix_s = vix["close"].reindex(common)
    trades = []
    last = -100
    for i in range(20, len(common) - 1):
        if i - last < 5: continue
        if float(vix_s.iloc[i]) < 18: continue
        midx = mgc.index.get_loc(common[i])
        if midx + 1 >= len(mgc): break
        ep = float(mgc["open"].iloc[midx + 1])
        exit_idx, exit_px = sim(mgc, midx + 1, "BUY", ep, 0.02, 0.03, 10)
        pnl = (exit_px - ep) * 10 - 2.49
        trades.append({"exit_date": mgc.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def main():
    print("Loading data…")
    mes = load("MES"); mnq = load("MNQ"); m2k = load("M2K")
    mgc = load("MGC"); mcl = load("MCL")
    try:
        vix = load("VIX")
    except Exception:
        vix = None

    # Common full index (use MES as reference)
    full_index = mes.index

    print("Computing per-strat daily PnL…")
    daily_pnls = {}
    daily_pnls["v2_mes"] = trades_to_daily(strat_v2_mes(mes), full_index)
    daily_pnls["mnq_overnight"] = trades_to_daily(strat_mnq_overnight(mnq), full_index)
    daily_pnls["rs_mes_mnq"] = trades_to_daily(strat_rs_mes_mnq(mes, mnq), full_index)
    daily_pnls["gold_trend"] = trades_to_daily(strat_gold_trend(mgc), full_index)
    daily_pnls["cross_asset_mom"] = trades_to_daily(
        strat_cross_asset_mom({"MES": mes, "MNQ": mnq, "M2K": m2k, "MGC": mgc, "MCL": mcl}),
        full_index,
    )
    daily_pnls["friday_mon_mnq"] = trades_to_daily(strat_friday_monday_mnq(mnq), full_index)
    daily_pnls["thursday_mes"] = trades_to_daily(strat_thursday_rally(mes, "MES"), full_index)
    daily_pnls["thursday_mnq"] = trades_to_daily(strat_thursday_rally(mnq, "MNQ"), full_index)
    daily_pnls["multi_tf_mom"] = trades_to_daily(strat_multi_tf_mom(mes), full_index)
    if vix is not None:
        daily_pnls["gold_vix_long"] = trades_to_daily(strat_gold_vix_long(mgc, vix), full_index)

    # Buy-and-hold MES as benchmark
    mes_ret = mes["close"].diff().fillna(0) * 5  # approx daily PnL 1 contract
    daily_pnls["mes_buy_hold"] = mes_ret

    # Build DataFrame
    df = pd.DataFrame(daily_pnls).fillna(0)

    print("\n=== CORRELATION MATRIX (Pearson) ===")
    corr = df.corr().round(2)
    print(corr.to_string())

    print("\n=== CORRELATION TO MES BUY-AND-HOLD (beta indicator) ===")
    beta_corr = df.corrwith(df["mes_buy_hold"]).round(3).sort_values()
    print(beta_corr.to_string())
    print()
    print("Interpretation:")
    print("  corr > 0.7 → pure beta (just moves with market)")
    print("  0.3 < corr < 0.7 → partial beta (some alpha possible)")
    print("  corr < 0.3 → decorrelated (real alpha candidate)")

    # Strats per-year breakdown
    print("\n=== PnL CUMULÉ PAR STRAT ===")
    for name, s in daily_pnls.items():
        total = s.sum()
        positive_days = (s > 0).sum()
        print(f"{name:20s} total=${total:+8.0f} days_positive={positive_days}")

    corr.to_csv(ROOT / "reports" / "research" / "correlation_matrix.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
