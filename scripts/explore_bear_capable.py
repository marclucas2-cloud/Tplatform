#!/usr/bin/env python3
"""Chercher specifiquement des strats profitables EN BEAR MARKET.

Test sur periode 2022 (MES -19.3%) et 2026 YTD (-7.4%) comme sanity check.
Gate additionnel: la strat doit etre positive en 2022 AND 2026.

Strats testees:
  1. SHORT MES quand below EMA200 (bear trend follow)
  2. VIX spike > 90p → LONG MES next day (contrarian)
  3. Gap down fade (gap < -1% → LONG)
  4. Gold long quand VIX > 20
  5. MGC trend follow only
  6. MCL trend follow only
  7. SHORT MNQ on sharp rallies (RSI > 80)
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


def stats_with_years(trades, label):
    if not trades: return None
    df = pd.DataFrame(trades)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    n = len(df)
    if n < 20: return None
    total = df["pnl"].sum()
    arr = df["pnl"].values
    sharpe = arr.mean() / arr.std() * np.sqrt(252) if arr.std() > 0 else 0
    wr = (arr > 0).mean()
    years = {}
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        d = df[df.exit_date.dt.year == y]
        if len(d) > 0:
            years[y] = float(d.pnl.sum())
        else:
            years[y] = 0
    return {
        "label": label, "n": n, "wr": round(wr, 2),
        "total": round(total, 0), "sharpe": round(sharpe, 2),
        **{f"y{y}": round(v, 0) for y, v in years.items()},
        "bear_ok": years.get(2022, 0) > 0 and years.get(2026, 0) > 0,
    }


# ==================================================================
# 1. SHORT MES when below EMA200 (bear trend follow)
# ==================================================================
def short_below_ema200(df, sym, sl_pct=0.02, tp_pct=0.04):
    spec = SPECS[sym]
    close = df["close"]
    ema200 = close.ewm(span=200).mean()
    trades = []
    last = -100
    for i in range(200, len(df) - 1):
        if i - last < 5: continue
        if close.iloc[i] >= ema200.iloc[i]: continue
        eidx = i + 1
        if eidx >= len(df): break
        ep = float(df["open"].iloc[eidx])
        exit_idx, exit_px = sim(df, eidx, "SELL", ep, sl_pct, tp_pct, 10)
        pnl = (ep - exit_px) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        last = exit_idx
    return trades


# ==================================================================
# 2. VIX spike long MES (contrarian)
# ==================================================================
def vix_spike_long(mes, vix, vix_threshold_percentile=85, sl_pct=0.025, tp_pct=0.04):
    spec = SPECS["MES"]
    common = mes.index.intersection(vix.index)
    vix_series = vix["close"].reindex(common)
    # Rolling 90d percentile
    vix_thresh = vix_series.rolling(90).quantile(vix_threshold_percentile / 100)

    trades = []
    last = -100
    for i in range(90, len(common) - 1):
        if i - last < 3: continue
        vx = float(vix_series.iloc[i])
        vt = float(vix_thresh.iloc[i]) if np.isfinite(vix_thresh.iloc[i]) else 100
        if vx < vt: continue
        # VIX spike → buy MES contrarian
        mes_idx = mes.index.get_loc(common[i])
        if mes_idx + 1 >= len(mes): break
        ep = float(mes["open"].iloc[mes_idx + 1])
        exit_idx, exit_px = sim(mes, mes_idx + 1, "BUY", ep, sl_pct, tp_pct, 10)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": mes.index[exit_idx], "pnl": pnl})
        last = i
    return trades


# ==================================================================
# 3. Gap down fade MES (contrarian)
# ==================================================================
def gap_down_fade(df, sym, gap_threshold=0.01, sl_pct=0.02, tp_pct=0.015):
    spec = SPECS[sym]
    trades = []
    for i in range(1, len(df) - 1):
        gap = (df["open"].iloc[i] - df["close"].iloc[i - 1]) / df["close"].iloc[i - 1]
        if gap >= -gap_threshold: continue  # need significant gap down
        ep = float(df["open"].iloc[i])
        exit_idx, exit_px = sim(df, i, "BUY", ep, sl_pct, tp_pct, 2)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
    return trades


# ==================================================================
# 4. Gold long when VIX > 20 (flight-to-quality)
# ==================================================================
def gold_vix_long(mgc, vix, vix_min=20, sl_pct=0.02, tp_pct=0.03):
    spec = SPECS["MGC"]
    common = mgc.index.intersection(vix.index)
    vix_series = vix["close"].reindex(common)

    trades = []
    last = -100
    for i in range(20, len(common) - 1):
        if i - last < 5: continue
        vx = float(vix_series.iloc[i])
        if vx < vix_min: continue
        mgc_idx = mgc.index.get_loc(common[i])
        if mgc_idx + 1 >= len(mgc): break
        ep = float(mgc["open"].iloc[mgc_idx + 1])
        exit_idx, exit_px = sim(mgc, mgc_idx + 1, "BUY", ep, sl_pct, tp_pct, 10)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": mgc.index[exit_idx], "pnl": pnl})
        last = i
    return trades


# ==================================================================
# 5. Gold trend follow (EMA20 filter)
# ==================================================================
def gold_trend(mgc, ema_period=20, sl_pct=0.015, tp_pct=0.03):
    spec = SPECS["MGC"]
    close = mgc["close"]
    ema = close.ewm(span=ema_period).mean()
    trades = []
    i = ema_period
    while i < len(mgc) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]):
            i += 1; continue
        eidx = i + 1
        if eidx >= len(mgc): break
        ep = float(mgc["open"].iloc[eidx])
        exit_idx, exit_px = sim(mgc, eidx, "BUY", ep, sl_pct, tp_pct, 10)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": mgc.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


# ==================================================================
# 6. Short MES when overbought (RSI > 80)
# ==================================================================
def short_overbought(df, sym, period=14, rsi_threshold=80, sl_pct=0.02, tp_pct=0.03):
    spec = SPECS[sym]
    close = df["close"]
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    avg_up = up.rolling(period).mean()
    avg_dn = dn.rolling(period).mean()
    rs = avg_up / avg_dn.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    trades = []
    last = -100
    for i in range(period + 5, len(df) - 1):
        if i - last < 3: continue
        if rsi.iloc[i] <= rsi_threshold: continue
        eidx = i + 1
        if eidx >= len(df): break
        ep = float(df["open"].iloc[eidx])
        exit_idx, exit_px = sim(df, eidx, "SELL", ep, sl_pct, tp_pct, 5)
        pnl = (ep - exit_px) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def main():
    print("Loading data…")
    mes = load("MES")
    mnq = load("MNQ")
    mgc = load("MGC")
    mcl = load("MCL")
    try:
        vix = load("VIX")
    except Exception:
        vix = None

    results = []

    # 1. Short below EMA200
    for sym, df in [("MES", mes), ("MNQ", mnq)]:
        trades = short_below_ema200(df, sym)
        s = stats_with_years(trades, f"short_ema200_{sym}")
        if s: results.append(s)

    # 2. VIX spike long MES
    if vix is not None:
        for pct in [80, 85, 90]:
            trades = vix_spike_long(mes, vix, vix_threshold_percentile=pct)
            s = stats_with_years(trades, f"vix_spike_long_mes_p{pct}")
            if s: results.append(s)

    # 3. Gap down fade
    for sym, df in [("MES", mes), ("MNQ", mnq)]:
        for gap_thr in [0.01, 0.015, 0.02]:
            trades = gap_down_fade(df, sym, gap_threshold=gap_thr)
            s = stats_with_years(trades, f"gap_down_fade_{sym}_{int(gap_thr*1000)}")
            if s: results.append(s)

    # 4. Gold VIX long
    if vix is not None:
        for vm in [18, 20, 25]:
            trades = gold_vix_long(mgc, vix, vix_min=vm)
            s = stats_with_years(trades, f"gold_vix_long_mgc_v{vm}")
            if s: results.append(s)

    # 5. Gold trend
    trades = gold_trend(mgc)
    s = stats_with_years(trades, "gold_trend_mgc")
    if s: results.append(s)

    # 6. Short overbought
    for sym, df in [("MES", mes), ("MNQ", mnq)]:
        for rt in [75, 80, 85]:
            trades = short_overbought(df, sym, rsi_threshold=rt)
            s = stats_with_years(trades, f"short_rsi{rt}_{sym}")
            if s: results.append(s)

    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("No results")
        return 1

    df_res = df_res.sort_values("sharpe", ascending=False)

    print("\n=== BEAR-CAPABLE SEARCH ===")
    print(df_res[["label", "n", "sharpe", "total", "y2022", "y2026", "bear_ok"]].to_string(index=False))
    print()
    bear_passers = df_res[df_res["bear_ok"]]
    print(f"Strats positive in BOTH 2022 AND 2026 (bear years): {len(bear_passers)}")
    if len(bear_passers) > 0:
        print("\n=== TRUE ALPHA CANDIDATES (bear_ok=True) ===")
        print(bear_passers[["label", "n", "sharpe", "total", "y2022", "y2026"]].to_string(index=False))

    df_res.to_csv(ROOT / "reports" / "research" / "bear_capable_search.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
