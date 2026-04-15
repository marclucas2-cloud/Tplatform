#!/usr/bin/env python3
"""Extended MNQ sweep around best combo (150,250) to confirm robustness.

If the edge is real, nearby combos should also be positive. If it's data mining,
(150,250) is an isolated peak surrounded by losers.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
ROOT = Path(__file__).resolve().parent.parent

MNQ_SPEC = {"mult": 2.0, "tick": 0.25, "commission": 1.24, "slip_ticks_rt": 1}


def load_futures(symbol: str) -> pd.DataFrame:
    f = ROOT / "data" / "futures" / f"{symbol}_1D.parquet"
    df = pd.read_parquet(f)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def backtest(df, sl_pts, tp_pts, ema_period):
    spec = MNQ_SPEC
    slip = spec["slip_ticks_rt"] * spec["tick"] * spec["mult"]
    cost_rt = spec["commission"] + slip
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=ema_period).mean()
    pnls = []
    i = ema_period
    while i < len(df) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]):
            i += 1; continue
        entry_idx = i + 1
        if entry_idx >= len(df): break
        entry_px = float(open_.iloc[entry_idx])
        sl = entry_px - sl_pts
        tp = entry_px + tp_pts
        exit_px = None; exit_idx = None
        for j in range(entry_idx, min(entry_idx + 10, len(df))):
            h = float(high.iloc[j]); l = float(low.iloc[j]); o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl: exit_idx = j; exit_px = o; break
                if o >= tp: exit_idx = j; exit_px = o; break
            if l <= sl and h >= tp: exit_idx = j; exit_px = sl; break
            if l <= sl: exit_idx = j; exit_px = sl; break
            if h >= tp: exit_idx = j; exit_px = tp; break
        if exit_idx is None:
            exit_idx = min(entry_idx + 9, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])
        pnl = (exit_px - entry_px) * spec["mult"] - cost_rt
        pnls.append(pnl)
        i = exit_idx + 1
    n = len(pnls)
    if n < 30:
        return {"n": n, "sharpe": 0, "total": 0}
    arr = np.array(pnls)
    total = float(arr.sum())
    wr = float((arr > 0).mean())
    mu = float(arr.mean())
    sd = float(arr.std())
    span = (df.index[-1] - df.index[0]).days
    tpy = n / span * 365 if span > 0 else 252
    sharpe = mu / sd * np.sqrt(tpy) if sd > 0 else 0
    return {"n": n, "sharpe": round(sharpe, 2), "total": round(total, 0),
            "wr": round(wr, 2)}


def main():
    df = load_futures("MNQ")
    print(f"MNQ: {len(df)} bars")

    # Fine-grained sweep around (150, 250, 50)
    SL_VALUES = [120, 130, 140, 150, 160, 170, 180, 200]
    TP_VALUES = [200, 220, 240, 250, 260, 280, 300]
    EMA_VALUES = [40, 50, 60, 80, 100]

    results = []
    for sl in SL_VALUES:
        for tp in TP_VALUES:
            for ema in EMA_VALUES:
                stats = backtest(df, sl, tp, ema)
                results.append({"sl": sl, "tp": tp, "ema": ema, **stats})

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    df_res.to_csv(ROOT / "reports" / "research" / "sweep_mnq_extended.csv", index=False)

    print(f"\n=== EXTENDED MNQ SWEEP ({len(df_res)} combos) ===")
    print(f"Positives: {(df_res['sharpe'] > 0).sum()}")
    print(f"Sharpe > 0.3: {(df_res['sharpe'] > 0.3).sum()}")
    print(f"Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")

    print("\n=== TOP 20 ===")
    print(df_res.head(20).to_string(index=False))

    # Check if (150, 250, 50) is isolated or surrounded by positives
    center = df_res[(df_res["sl"] == 150) & (df_res["tp"] == 250) & (df_res["ema"] == 50)]
    print(f"\n=== CENTER COMBO (150,250,50): {center.iloc[0]['sharpe'] if len(center) else 'N/A'} ===")

    # Count positives in [120-180] × [220-280] × [40-80] neighborhood
    neighbors = df_res[
        (df_res["sl"].between(130, 170)) &
        (df_res["tp"].between(230, 280)) &
        (df_res["ema"].between(40, 80))
    ]
    neigh_pos = (neighbors["sharpe"] > 0.3).sum()
    print(f"Close neighbors with Sharpe > 0.3: {neigh_pos}/{len(neighbors)}")
    if neigh_pos >= len(neighbors) * 0.6:
        print("ROBUST: many neighbors positive — edge is real")
    elif neigh_pos >= len(neighbors) * 0.3:
        print("BORDERLINE: some neighbors positive — edge may be fragile")
    else:
        print("SUSPECT: neighbors mostly negative — possible data mining")

    return 0


if __name__ == "__main__":
    sys.exit(main())
