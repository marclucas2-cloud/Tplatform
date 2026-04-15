#!/usr/bin/env python3
"""Sweep focused MNQ avec SL/TP ajustes a sa volatilite (pas hardcode MES).

Le sweep precedent utilisait SL=30/TP=50 points (calibre MES ~$5/pt).
Sur MNQ (~$2/pt, prix ~25000, ATR ~100-200 pts/jour), ces stops sont
TROP tight → tous les trades stop sur du noise intraday.

Ce sweep teste :
  - SL en % du prix (plus correct pour differences de volatilite)
  - Ou SL en points proportionnel MNQ (60-200 pts)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sweep_mnq")

ROOT = Path(__file__).resolve().parent.parent

MNQ_SPEC = {"mult": 2.0, "tick": 0.25, "commission": 1.24, "slip_ticks_rt": 1}

# Curated MNQ combos — SL/TP in points (MNQ typical price ~25000)
SL_TP_COMBOS = [
    (50, 75),   # 0.2% / 0.3%
    (60, 100),  # 0.24% / 0.4%
    (80, 120),  # 0.32% / 0.48%
    (100, 150), # 0.4% / 0.6%
    (120, 200), # 0.48% / 0.8%
    (150, 250), # 0.6% / 1.0%
    (80, 160),  # 0.32% / 0.64% (1:2 ratio)
    (100, 200), # 0.4% / 0.8% (1:2 ratio)
]

EMA_PERIODS = [10, 20, 50]
REGIME_FILTERS = ["none", "spy_bull"]
MAX_HOLD_DAYS = 10


def load_futures(symbol: str) -> pd.DataFrame:
    f = ROOT / "data" / "futures" / f"{symbol}_1D.parquet"
    df = pd.read_parquet(f)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def load_spy() -> pd.Series:
    f = ROOT / "data" / "us_stocks" / "SPY.parquet"
    if not f.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(f)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df["adj_close"].astype(float).sort_index()


def backtest(df, sl_pts, tp_pts, ema_period, regime_filter, spy_series) -> dict:
    spec = MNQ_SPEC
    slip = spec["slip_ticks_rt"] * spec["tick"] * spec["mult"]
    cost_rt = spec["commission"] + slip

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=ema_period, adjust=False).mean()

    if regime_filter == "spy_bull" and len(spy_series) > 0:
        spy_sma = spy_series.rolling(200).mean()
        spy_bull = (spy_series > spy_sma).reindex(df.index, method="ffill").fillna(False)
    else:
        spy_bull = pd.Series(True, index=df.index)

    pnls = []
    i = max(ema_period, 200) if regime_filter == "spy_bull" else ema_period
    while i < len(df) - 1:
        c = float(close.iloc[i])
        e = float(ema.iloc[i])
        if c <= e or not np.isfinite(e):
            i += 1
            continue
        if not bool(spy_bull.iloc[i]):
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(open_.iloc[entry_idx])
        sl = entry_px - sl_pts
        tp = entry_px + tp_pts

        exit_px = None
        exit_idx = None
        for j in range(entry_idx, min(entry_idx + MAX_HOLD_DAYS, len(df))):
            h = float(high.iloc[j])
            l = float(low.iloc[j])
            o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl:
                    exit_idx = j; exit_px = o; break
                if o >= tp:
                    exit_idx = j; exit_px = o; break
            sl_hit = l <= sl
            tp_hit = h >= tp
            if sl_hit and tp_hit:
                exit_idx = j; exit_px = sl; break
            if sl_hit:
                exit_idx = j; exit_px = sl; break
            if tp_hit:
                exit_idx = j; exit_px = tp; break
        if exit_idx is None:
            exit_idx = min(entry_idx + MAX_HOLD_DAYS - 1, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])

        pnl_pts = exit_px - entry_px
        pnl_usd = pnl_pts * spec["mult"] - cost_rt
        pnls.append(pnl_usd)
        i = exit_idx + 1

    n = len(pnls)
    if n < 30:
        return {"n_trades": n, "sharpe": 0, "total": 0}
    arr = np.array(pnls)
    total = float(arr.sum())
    wr = float((arr > 0).mean())
    mu = float(arr.mean())
    sd = float(arr.std())
    span_days = (df.index[-1] - df.index[0]).days
    tpy = n / span_days * 365 if span_days > 0 else 252
    sharpe = mu / sd * np.sqrt(tpy) if sd > 0 else 0
    cum = arr.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {
        "n_trades": n,
        "sharpe": round(sharpe, 2),
        "total": round(total, 0),
        "wr": round(wr, 2),
        "mdd": round(mdd, 0),
    }


def walk_forward(df, sl_pts, tp_pts, ema_period, regime_filter, spy_series,
                 n_windows=5) -> dict:
    """Split trades chronologically, compute OOS Sharpe per window."""
    spec = MNQ_SPEC
    slip = spec["slip_ticks_rt"] * spec["tick"] * spec["mult"]
    cost_rt = spec["commission"] + slip

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=ema_period, adjust=False).mean()

    if regime_filter == "spy_bull" and len(spy_series) > 0:
        spy_sma = spy_series.rolling(200).mean()
        spy_bull = (spy_series > spy_sma).reindex(df.index, method="ffill").fillna(False)
    else:
        spy_bull = pd.Series(True, index=df.index)

    trades = []
    i = max(ema_period, 200) if regime_filter == "spy_bull" else ema_period
    while i < len(df) - 1:
        c = float(close.iloc[i])
        e = float(ema.iloc[i])
        if c <= e or not np.isfinite(e):
            i += 1; continue
        if not bool(spy_bull.iloc[i]):
            i += 1; continue
        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(open_.iloc[entry_idx])
        sl = entry_px - sl_pts
        tp = entry_px + tp_pts
        exit_px = None
        exit_idx = None
        for j in range(entry_idx, min(entry_idx + MAX_HOLD_DAYS, len(df))):
            h = float(high.iloc[j]); l = float(low.iloc[j]); o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl: exit_idx = j; exit_px = o; break
                if o >= tp: exit_idx = j; exit_px = o; break
            if l <= sl and h >= tp: exit_idx = j; exit_px = sl; break
            if l <= sl: exit_idx = j; exit_px = sl; break
            if h >= tp: exit_idx = j; exit_px = tp; break
        if exit_idx is None:
            exit_idx = min(entry_idx + MAX_HOLD_DAYS - 1, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])
        pnl = (exit_px - entry_px) * spec["mult"] - cost_rt
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1

    if len(trades) < 50:
        return {"windows": [], "n_prof": 0}

    df_tr = pd.DataFrame(trades).sort_values("exit_date").reset_index(drop=True)
    n = len(df_tr)
    slice_size = n // (n_windows + 1)
    is_size = int(slice_size * 1.5)

    windows = []
    for wi in range(n_windows):
        oos_start = (wi + 1) * slice_size
        is_start = max(0, oos_start - is_size)
        oos_end = oos_start + slice_size
        if oos_end > n:
            break
        is_slice = df_tr.iloc[is_start:oos_start]
        oos_slice = df_tr.iloc[oos_start:oos_end]

        def _sh(arr):
            if len(arr) < 2 or arr.std() == 0:
                return 0
            return float(arr.mean() / arr.std() * np.sqrt(252))

        windows.append({
            "is_sh": _sh(is_slice["pnl"].values),
            "oos_sh": _sh(oos_slice["pnl"].values),
            "oos_pnl": float(oos_slice["pnl"].sum()),
            "oos_prof": bool(oos_slice["pnl"].sum() > 0),
        })

    n_prof = sum(1 for w in windows if w["oos_prof"])
    return {"windows": windows, "n_prof": n_prof}


def main() -> int:
    logger.info("Loading MNQ + SPY…")
    df = load_futures("MNQ")
    spy = load_spy()
    logger.info(f"MNQ: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}")
    logger.info(f"SPY: {len(spy)} bars")

    logger.info("Running MNQ focused sweep (volatility-adjusted SL/TP)…")
    results = []
    for sl, tp in SL_TP_COMBOS:
        for ema in EMA_PERIODS:
            for regime in REGIME_FILTERS:
                stats = backtest(df, sl, tp, ema, regime, spy)
                results.append({
                    "sl": sl, "tp": tp, "ema": ema, "regime": regime,
                    **stats,
                })

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    ROOT.joinpath("reports", "research").mkdir(parents=True, exist_ok=True)
    df_res.to_csv(ROOT / "reports" / "research" / "sweep_mnq_voladj.csv", index=False)

    print("\n=== MNQ FOCUSED SWEEP (Sharpe > 0) ===")
    positives = df_res[df_res["sharpe"] > 0]
    if len(positives) > 0:
        print(positives.to_string(index=False))
    else:
        print("  AUCUN combo positif")

    print("\n=== TOP 10 ALL ===")
    print(df_res.head(10).to_string(index=False))

    print(f"\nTotal combos: {len(df_res)}")
    print(f"Positives: {(df_res['sharpe'] > 0).sum()}")
    print(f"Sharpe > 0.3: {(df_res['sharpe'] > 0.3).sum()}")
    print(f"Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")

    # Walk-forward on best combo
    if len(df_res) > 0:
        best = df_res.iloc[0]
        print(f"\n=== WALK-FORWARD on best combo ===")
        print(f"SL={best['sl']} TP={best['tp']} ema={best['ema']} regime={best['regime']}")
        wf = walk_forward(df, int(best['sl']), int(best['tp']),
                          int(best['ema']), str(best['regime']), spy)
        if wf["windows"]:
            for i, w in enumerate(wf["windows"]):
                print(f"  W{i+1}: IS Sh {w['is_sh']:.2f} | OOS Sh {w['oos_sh']:.2f} | "
                      f"OOS PnL ${w['oos_pnl']:.0f} {'PROF' if w['oos_prof'] else 'LOSS'}")
            print(f"  Profitable OOS: {wf['n_prof']}/{len(wf['windows'])}")
        else:
            print("  insufficient trades for WF")

    print("\n=== VERDICT ===")
    if (df_res["sharpe"] > 0.5).sum() > 0:
        print("CANDIDATS TROUVES: Sharpe > 0.5 sur certaines combinaisons — a investiguer")
    elif (df_res["sharpe"] > 0.3).sum() > 0:
        print("MARGINAL: Sharpe > 0.3 mais pas 0.5 — probablement pas d'edge exploitable")
    else:
        print("NO EDGE: MNQ Overnight DEFINITIVELY UNPROFITABLE (aucun combo > 0.3)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
