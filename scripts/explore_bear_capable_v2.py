#!/usr/bin/env python3
"""Bear-capable exploration V2: EU indices + commodity rotation + spreads.

Goal: find more strats positive in 2022 AND 2026 bear years, orthogonal to MES.

Tested:
  1. EU index trend follow (DAX, CAC40, ESTX50, MIB)
  2. EU overnight (buy close, sell open)
  3. MGC-MCL commodity rotation (relative strength)
  4. Gold-Oil divergence
  5. MCL mean reversion
  6. DAX-MES spread
  7. MCL trend follow only
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
    "DAX":   {"mult": 1.0, "cost": 3.0},
    "CAC40": {"mult": 10.0, "cost": 3.0},
    "ESTX50":{"mult": 10.0, "cost": 2.0},
    "MIB":   {"mult": 5.0, "cost": 3.0},
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


def stats(trades, label):
    if not trades: return None
    df = pd.DataFrame(trades)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    n = len(df)
    if n < 15: return None
    total = df["pnl"].sum()
    arr = df["pnl"].values
    sharpe = arr.mean() / arr.std() * np.sqrt(252) if arr.std() > 0 else 0
    wr = (arr > 0).mean()
    years = {}
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        d = df[df.exit_date.dt.year == y]
        years[y] = float(d.pnl.sum()) if len(d) > 0 else 0
    return {
        "label": label, "n": n, "wr": round(wr, 2),
        "total": round(total, 0), "sharpe": round(sharpe, 2),
        **{f"y{y}": round(v, 0) for y, v in years.items()},
        "bear_ok": years.get(2022, 0) > 0 and years.get(2026, 0) > 0,
    }


def eu_trend(df, sym, ema=20, sl_pct=0.015, tp_pct=0.03, mh=10):
    spec = SPECS[sym]
    close = df["close"].astype(float)
    ema_s = close.ewm(span=ema).mean()
    trades = []
    i = ema
    while i < len(df) - 1:
        if close.iloc[i] <= ema_s.iloc[i] or not np.isfinite(ema_s.iloc[i]):
            i += 1; continue
        eidx = i + 1
        if eidx >= len(df): break
        ep = float(df["open"].iloc[eidx])
        exit_idx, exit_px = sim(df, eidx, "BUY", ep, sl_pct, tp_pct, mh)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


def eu_overnight(df, sym, sl_pct=0.015, tp_pct=0.025, mh=1):
    """Long EU index at close, exit next open."""
    spec = SPECS[sym]
    trades = []
    for i in range(20, len(df) - 1):
        # Buy close
        ep = float(df["close"].iloc[i])
        # Exit next open
        next_open = float(df["open"].iloc[i + 1])
        pnl = (next_open - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[i + 1], "pnl": pnl})
    return trades


def gold_oil_rotation(mgc, mcl, lookback=20, min_edge=0.03, hold=10):
    """Rotate long between MGC and MCL based on momentum spread."""
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
        if spread > 0:
            # Gold winning → long MGC
            df = mgc; sym = "MGC"
        else:
            df = mcl; sym = "MCL"
        d = common[i]
        di = df.index.get_loc(d)
        if di + hold >= len(df): break
        ep = float(df["open"].iloc[di + 1])
        exit_idx, exit_px = sim(df, di + 1, "BUY", ep, 0.02, 0.04, hold)
        spec = SPECS[sym]
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def mcl_trend(df, ema=20, sl_pct=0.02, tp_pct=0.04, mh=10):
    """Crude oil trend follow."""
    spec = SPECS["MCL"]
    close = df["close"].astype(float)
    ema_s = close.ewm(span=ema).mean()
    trades = []
    i = ema
    while i < len(df) - 1:
        if close.iloc[i] <= ema_s.iloc[i] or not np.isfinite(ema_s.iloc[i]):
            i += 1; continue
        eidx = i + 1
        if eidx >= len(df): break
        ep = float(df["open"].iloc[eidx])
        exit_idx, exit_px = sim(df, eidx, "BUY", ep, sl_pct, tp_pct, mh)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1
    return trades


def mcl_mean_rev(df, period=10, z_thr=-1.5, sl_pct=0.025, tp_pct=0.03, mh=5):
    """Long MCL when oversold."""
    spec = SPECS["MCL"]
    close = df["close"].astype(float)
    mean = close.rolling(period).mean()
    std = close.rolling(period).std()
    z = (close - mean) / std
    trades = []
    last = -100
    for i in range(period + 5, len(df) - 1):
        if i - last < 3: continue
        if not np.isfinite(z.iloc[i]): continue
        if z.iloc[i] > z_thr: continue  # need oversold
        eidx = i + 1
        if eidx >= len(df): break
        ep = float(df["open"].iloc[eidx])
        exit_idx, exit_px = sim(df, eidx, "BUY", ep, sl_pct, tp_pct, mh)
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def eu_vs_us(dax, mes, lookback=10, threshold=0.02, hold=5):
    """Long DAX when DAX momentum > MES momentum by threshold."""
    common = dax.index.intersection(mes.index)
    dax_c = dax["close"].reindex(common)
    mes_c = mes["close"].reindex(common)
    dax_ret = dax_c.pct_change(lookback)
    mes_ret = mes_c.pct_change(lookback)
    trades = []
    last = -100
    for i in range(lookback, len(common) - hold):
        if i - last < hold: continue
        dr = float(dax_ret.iloc[i]); mr = float(mes_ret.iloc[i])
        if not (np.isfinite(dr) and np.isfinite(mr)): continue
        if dr - mr < threshold: continue
        d = common[i]
        di = dax.index.get_loc(d)
        if di + hold >= len(dax): break
        ep = float(dax["open"].iloc[di + 1]) if di + 1 < len(dax) else float(dax["close"].iloc[di])
        exit_idx, exit_px = sim(dax, di + 1, "BUY", ep, 0.015, 0.03, hold)
        spec = SPECS["DAX"]
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": dax.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def eu_best_of_4(dfs, lookback=20, hold=10):
    """Rotate among 4 EU indices based on 20-day momentum."""
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
            prev = df.index.get_loc(common[i - lookback]) if common[i - lookback] in df.index else None
            if prev is None: continue
            rets[sym] = df["close"].iloc[di] / df["close"].iloc[prev] - 1
        if not rets: continue
        winner = max(rets, key=rets.get)
        if rets[winner] < 0.02: continue
        wdf = dfs[winner]
        d = common[i]
        wi = wdf.index.get_loc(d)
        if wi + 1 >= len(wdf): break
        ep = float(wdf["open"].iloc[wi + 1])
        exit_idx, exit_px = sim(wdf, wi + 1, "BUY", ep, 0.015, 0.03, hold)
        spec = SPECS[winner]
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({"exit_date": wdf.index[exit_idx], "pnl": pnl})
        last = i
    return trades


def main():
    print("Loading data…")
    mes = load("MES"); mgc = load("MGC"); mcl = load("MCL")
    dax = load("DAX"); cac = load("CAC40")
    estx = load("ESTX50"); mib = load("MIB")

    results = []

    # 1. EU trend follow
    for sym, df in [("DAX", dax), ("CAC40", cac), ("ESTX50", estx), ("MIB", mib)]:
        for ema in [20, 50]:
            trades = eu_trend(df, sym, ema=ema)
            s = stats(trades, f"eu_trend_{sym}_ema{ema}")
            if s: results.append(s)

    # 2. EU overnight
    for sym, df in [("DAX", dax), ("CAC40", cac), ("ESTX50", estx), ("MIB", mib)]:
        trades = eu_overnight(df, sym)
        s = stats(trades, f"eu_overnight_{sym}")
        if s: results.append(s)

    # 3. Gold-Oil rotation
    for min_edge in [0.02, 0.03, 0.05]:
        trades = gold_oil_rotation(mgc, mcl, min_edge=min_edge)
        s = stats(trades, f"gold_oil_rot_{int(min_edge*100)}")
        if s: results.append(s)

    # 4. MCL trend
    for ema in [20, 50]:
        trades = mcl_trend(mcl, ema=ema)
        s = stats(trades, f"mcl_trend_ema{ema}")
        if s: results.append(s)

    # 5. MCL mean reversion
    for z in [-1.5, -2.0]:
        trades = mcl_mean_rev(mcl, z_thr=z)
        s = stats(trades, f"mcl_mr_z{abs(z)}")
        if s: results.append(s)

    # 6. EU vs US decorrelation (DAX outperforming MES)
    for thr in [0.02, 0.03]:
        trades = eu_vs_us(dax, mes, threshold=thr)
        s = stats(trades, f"dax_beats_mes_t{int(thr*100)}")
        if s: results.append(s)

    # 7. Best of 4 EU rotation
    trades = eu_best_of_4({"DAX": dax, "CAC40": cac, "ESTX50": estx, "MIB": mib})
    s = stats(trades, "eu_best_of_4_mom20")
    if s: results.append(s)

    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("No results"); return 1

    df_res = df_res.sort_values("sharpe", ascending=False)
    print("\n=== BEAR-CAPABLE SEARCH V2 ===")
    print(df_res[["label", "n", "sharpe", "total", "y2022", "y2026", "bear_ok"]].to_string(index=False))
    print()
    bear_passers = df_res[df_res["bear_ok"]]
    print(f"Bear_ok (positive 2022 AND 2026): {len(bear_passers)}")
    if len(bear_passers) > 0:
        print("\n=== TRUE ALPHA CANDIDATES ===")
        print(bear_passers[["label", "n", "sharpe", "total", "y2021", "y2022", "y2023", "y2024", "y2025", "y2026"]].to_string(index=False))

    df_res.to_csv(ROOT / "reports" / "research" / "bear_capable_search_v2.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
