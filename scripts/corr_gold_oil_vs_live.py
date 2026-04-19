#!/usr/bin/env python3
"""Correlation check: Gold-Oil Rotation vs existing live candidates.

Compares daily PnL series to decide if gold-oil rotation adds diversification
or just duplicates cross_asset_mom / gold_trend exposure.
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
        if side == "BUY":
            if l <= sl: return j, sl
            if h >= tp: return j, tp
    end = min(eidx + mh - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


def gold_oil_rot(mgc, mcl, lookback=20, min_edge=0.02, hold=10):
    common = mgc.index.intersection(mcl.index)
    mgc_c = mgc["close"].reindex(common)
    mcl_c = mcl["close"].reindex(common)
    mgc_ret = mgc_c.pct_change(lookback)
    mcl_ret = mcl_c.pct_change(lookback)
    trades = []
    last = -100
    for i in range(lookback, len(common) - hold):
        if i - last < hold: continue
        gr = float(mgc_ret.iloc[i]); cr = float(mcl_ret.iloc[i])
        if not (np.isfinite(gr) and np.isfinite(cr)): continue
        spread = gr - cr
        if abs(spread) < min_edge: continue
        sym = "MGC" if spread > 0 else "MCL"
        df = mgc if sym == "MGC" else mcl
        d = common[i]
        di = df.index.get_loc(d)
        if di + hold >= len(df): break
        ep = float(df["open"].iloc[di + 1])
        exit_idx, exit_px = sim(df, di + 1, "BUY", ep, 0.02, 0.04, hold)
        pnl = (exit_px - ep) * SPECS[sym]["mult"] - SPECS[sym]["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def cross_asset(dfs, lookback=20, hold=20):
    common = None
    for df in dfs.values():
        common = df.index if common is None else common.intersection(df.index)
    trades = []
    last = -100
    for i in range(lookback, len(common) - hold):
        if i - last < hold: continue
        rets = {}
        for sym, df in dfs.items():
            d = common[i]
            di = df.index.get_loc(d)
            prev = df.index.get_loc(common[i - lookback])
            rets[sym] = df["close"].iloc[di] / df["close"].iloc[prev] - 1
        winner = max(rets, key=rets.get)
        if rets[winner] < 0.02: continue
        wdf = dfs[winner]
        wi = wdf.index.get_loc(common[i])
        if wi + 1 >= len(wdf): break
        ep = float(wdf["open"].iloc[wi + 1])
        exit_idx, exit_px = sim(wdf, wi + 1, "BUY", ep, 0.015, 0.03, hold)
        pnl = (exit_px - ep) * SPECS[winner]["mult"] - SPECS[winner]["cost"]
        trades.append({"exit_date": wdf.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def gold_trend(mgc, ema=20):
    close = mgc["close"].astype(float)
    ema_s = close.ewm(span=ema).mean()
    trades = []
    i = ema
    while i < len(mgc) - 1:
        if close.iloc[i] <= ema_s.iloc[i] or not np.isfinite(ema_s.iloc[i]):
            i += 1; continue
        eidx = i + 1
        if eidx >= len(mgc): break
        ep = float(mgc["open"].iloc[eidx])
        exit_idx, exit_px = sim(mgc, eidx, "BUY", ep, 0.015, 0.03, 10)
        pnl = (exit_px - ep) * SPECS["MGC"]["mult"] - SPECS["MGC"]["cost"]
        trades.append({"exit_date": mgc.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


def trades_to_daily(trades, full_index):
    s = pd.Series(0.0, index=full_index)
    for t in trades:
        d = pd.Timestamp(t["exit_date"]).normalize()
        if d in s.index:
            s.loc[d] += t["pnl"]
        else:
            idx = s.index.get_indexer([d], method="nearest")[0]
            s.iloc[idx] += t["pnl"]
    return s


def main():
    mes = load("MES"); mnq = load("MNQ"); m2k = load("M2K")
    mgc = load("MGC"); mcl = load("MCL")
    full_index = mes.index

    daily = {}
    daily["gold_oil_rot"] = trades_to_daily(gold_oil_rot(mgc, mcl), full_index)
    daily["cross_asset_mom"] = trades_to_daily(
        cross_asset({"MES": mes, "MNQ": mnq, "M2K": m2k, "MGC": mgc, "MCL": mcl}),
        full_index,
    )
    daily["gold_trend_mgc"] = trades_to_daily(gold_trend(mgc), full_index)
    daily["mes_buy_hold"] = mes["close"].diff().fillna(0) * 5

    df = pd.DataFrame(daily).fillna(0)
    print("=== CORRELATION MATRIX ===")
    print(df.corr().round(3).to_string())
    print()
    print("=== PnL TOTALS ===")
    for name, s in daily.items():
        print(f"  {name:20s} ${s.sum():+,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
