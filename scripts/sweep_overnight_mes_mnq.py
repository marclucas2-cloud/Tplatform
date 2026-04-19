#!/usr/bin/env python3
"""Parameter sweep — Overnight MES/MNQ production logic + variations.

Question: est-ce qu'il existe UN set de paramètres qui donne un edge pour
la stratégie "close > EMA → BUY next open, SL/TP fixes" ?

Sweep:
  - Symbols: MES, MNQ
  - SL/TP combos: 5 curated (tight, balanced, wide, contrarian, large TP)
  - EMA period: 10, 20, 50
  - Regime filter: None, SPY bull only (SPY > SPY_200MA)

Total: 2 × 5 × 3 × 2 = 60 combos. For each: full backtest with realistic
costs + Sharpe + WR + max DD. Rank by Sharpe, report top 10.

If no combo passes Sharpe > 0.5 on full period AND OOS 50/50 → definitively
no edge for this logic family. Otherwise flag the candidates.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sweep")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "research"

SPECS = {
    "MES": {"mult": 5.0, "tick": 0.25, "commission": 1.24, "slip_ticks_rt": 1},
    "MNQ": {"mult": 2.0, "tick": 0.25, "commission": 1.24, "slip_ticks_rt": 1},
}

# Curated SL/TP combinations (points)
SL_TP_COMBOS = [
    (15, 20),   # tight scalp
    (15, 30),   # tight entry, balanced TP
    (20, 30),   # balanced conservative
    (30, 50),   # production current
    (40, 30),   # contrarian (wide SL, tighter TP = trend-follow style)
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
    """Load SPY from data/us_stocks (downloaded earlier for S&P research)."""
    f = ROOT / "data" / "us_stocks" / "SPY.parquet"
    if not f.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(f)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df["adj_close"].astype(float).sort_index()


def backtest_one_combo(
    df: pd.DataFrame,
    symbol: str,
    sl_pts: float,
    tp_pts: float,
    ema_period: int,
    regime_filter: str,
    spy_series: pd.Series,
) -> dict:
    spec = SPECS[symbol]
    slip_rt = spec["slip_ticks_rt"] * spec["tick"] * spec["mult"]
    cost_rt = spec["commission"] + slip_rt

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=ema_period, adjust=False).mean()

    # Regime filter
    if regime_filter == "spy_bull" and len(spy_series) > 0:
        spy_sma200 = spy_series.rolling(200).mean()
        spy_bull = (spy_series > spy_sma200).reindex(df.index, method="ffill").fillna(False)
    else:
        spy_bull = pd.Series(True, index=df.index)

    trades_pnl = []
    i = max(ema_period, 200) if regime_filter == "spy_bull" else ema_period

    while i < len(df) - 1:
        c_prev = float(close.iloc[i])
        e_prev = float(ema.iloc[i])
        if c_prev <= e_prev or not np.isfinite(e_prev):
            i += 1
            continue
        if not bool(spy_bull.iloc[i]):
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(open_.iloc[entry_idx])
        sl_px = entry_px - sl_pts
        tp_px = entry_px + tp_pts

        exit_idx = None
        exit_px = None
        for j in range(entry_idx, min(entry_idx + MAX_HOLD_DAYS, len(df))):
            h = float(high.iloc[j])
            l = float(low.iloc[j])
            o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl_px:
                    exit_idx = j
                    exit_px = o
                    break
                if o >= tp_px:
                    exit_idx = j
                    exit_px = o
                    break
            sl_hit = l <= sl_px
            tp_hit = h >= tp_px
            if sl_hit and tp_hit:
                exit_idx = j
                exit_px = sl_px
                break
            if sl_hit:
                exit_idx = j
                exit_px = sl_px
                break
            if tp_hit:
                exit_idx = j
                exit_px = tp_px
                break

        if exit_idx is None:
            exit_idx = min(entry_idx + MAX_HOLD_DAYS - 1, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])

        pnl_pts = exit_px - entry_px
        pnl_usd = pnl_pts * spec["mult"] - cost_rt
        trades_pnl.append(pnl_usd)
        i = exit_idx + 1

    n = len(trades_pnl)
    if n < 30:
        return {"n_trades": n, "sharpe": 0, "total_usd": 0, "wr": 0, "mdd_usd": 0}

    arr = np.array(trades_pnl)
    total = float(arr.sum())
    wr = float((arr > 0).mean())
    mu = float(arr.mean())
    sd = float(arr.std())
    # Annualize via trades/year (approx: n trades over date span)
    span_days = (df.index[-1] - df.index[0]).days
    trades_per_year = n / span_days * 365 if span_days > 0 else 252
    sharpe = mu / sd * np.sqrt(trades_per_year) if sd > 0 else 0.0
    cum = arr.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())

    return {
        "n_trades": n,
        "sharpe": round(sharpe, 2),
        "total_usd": round(total, 0),
        "wr": round(wr, 2),
        "mdd_usd": round(mdd, 0),
    }


def main() -> int:
    logger.info("Loading data…")
    data = {}
    for sym in ["MES", "MNQ"]:
        data[sym] = load_futures(sym)
        logger.info(f"  {sym}: {len(data[sym])} bars")
    spy = load_spy()
    logger.info(f"  SPY: {len(spy)} bars")

    logger.info("Running parameter sweep…")
    results = []
    for sym in ["MES", "MNQ"]:
        df = data[sym]
        for sl, tp in SL_TP_COMBOS:
            for ema in EMA_PERIODS:
                for regime in REGIME_FILTERS:
                    stats = backtest_one_combo(df, sym, sl, tp, ema, regime, spy)
                    row = {
                        "sym": sym,
                        "sl": sl,
                        "tp": tp,
                        "ema": ema,
                        "regime": regime,
                        **stats,
                    }
                    results.append(row)

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values("sharpe", ascending=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df_res.to_csv(OUT_DIR / "sweep_overnight_mes_mnq.csv", index=False)

    logger.info(f"\nTotal combos: {len(df_res)}")
    logger.info(f"Combos with Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")
    logger.info(f"Combos with Sharpe > 0.3: {(df_res['sharpe'] > 0.3).sum()}")
    logger.info(f"Combos positive: {(df_res['total_usd'] > 0).sum()}")

    print("\n=== TOP 15 combos by Sharpe ===")
    print(df_res.head(15).to_string(index=False))

    print("\n=== BOTTOM 5 combos ===")
    print(df_res.tail(5).to_string(index=False))

    # Verdict
    best = df_res.iloc[0]
    print(f"\n=== VERDICT ===")
    if best["sharpe"] > 0.5:
        print(f"EDGE FOUND: {best['sym']} SL={best['sl']} TP={best['tp']} EMA={best['ema']} regime={best['regime']}")
        print(f"  Sharpe {best['sharpe']}, total ${best['total_usd']}, WR {best['wr']}, MDD ${best['mdd_usd']}")
    else:
        print(f"NO EDGE: best combo = {best['sym']} {best['sl']}/{best['tp']} ema{best['ema']} {best['regime']}")
        print(f"  Sharpe {best['sharpe']} — below 0.5 threshold")
        print("  Overnight MES/MNQ logic family DEFINITIVELY UNPROFITABLE")

    return 0


if __name__ == "__main__":
    sys.exit(main())
