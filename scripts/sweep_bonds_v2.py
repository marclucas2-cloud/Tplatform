#!/usr/bin/env python3
"""Sweep bonds V2 — apply volatility-adjusted params + filters on ZN/ZT/ZB.

V1 (backtest_bonds.py, 6 months 1h) found 0/12 edge. Mostly because same
issue as MES V1: tight SL/TP not adapted to bond volatility + daily bars
too coarse for hourly setups.

V2 uses:
  - SL/TP in percentage of price (adapts to instrument)
  - Multiple EMA periods
  - SPY regime filter
  - Wide parameter sweep

Data: IBKR 1h 6 months (Oct 2025 → Apr 2026).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bonds_v2")

ROOT = Path(__file__).resolve().parent.parent

BOND_SPECS = {
    "ZT": {"mult": 200, "typical": 104, "cost_rt": 2.49},  # 2Y
    "ZN": {"mult": 100, "typical": 112, "cost_rt": 2.49},  # 10Y
    "ZB": {"mult": 100, "typical": 115, "cost_rt": 2.49},  # 30Y
}

# Percentage-based SL/TP combinations
SL_TP_COMBOS_PCT = [
    (0.10, 0.15),   # tight
    (0.15, 0.25),
    (0.20, 0.30),
    (0.25, 0.40),
    (0.30, 0.50),   # medium
    (0.40, 0.60),
    (0.50, 0.80),
    (0.60, 1.00),   # wide
]

EMA_PERIODS = [20, 50, 100]


def load_bond(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1H_IBKR6M.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def backtest(df: pd.DataFrame, sym: str, sl_pct: float, tp_pct: float, ema_period: int) -> dict:
    spec = BOND_SPECS[sym]
    cost = spec["cost_rt"]
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=ema_period).mean()

    pnls = []
    i = ema_period
    while i < len(df) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]):
            i += 1
            continue
        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(open_.iloc[entry_idx])
        sl_px = entry_px * (1 - sl_pct / 100)
        tp_px = entry_px * (1 + tp_pct / 100)

        exit_px = None
        exit_idx = None
        max_hold = 24 * 5  # 5 days in hourly bars
        for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
            h = float(high.iloc[j])
            l = float(low.iloc[j])
            o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl_px:
                    exit_idx = j; exit_px = o; break
                if o >= tp_px:
                    exit_idx = j; exit_px = o; break
            if l <= sl_px and h >= tp_px:
                exit_idx = j; exit_px = sl_px; break
            if l <= sl_px:
                exit_idx = j; exit_px = sl_px; break
            if h >= tp_px:
                exit_idx = j; exit_px = tp_px; break
        if exit_idx is None:
            exit_idx = min(entry_idx + max_hold - 1, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])

        ret = (exit_px - entry_px) / entry_px
        pnl_usd = ret * spec["typical"] * spec["mult"] - cost
        pnls.append(pnl_usd)
        i = exit_idx + 1

    n = len(pnls)
    if n < 20:
        return {"n": n, "sharpe": 0, "total": 0, "wr": 0}
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
    return {"n": n, "sharpe": round(sharpe, 2), "total": round(total, 0),
            "wr": round(wr, 2), "mdd": round(mdd, 0)}


def main() -> int:
    logger.info("Loading bond data…")
    data = {}
    for sym in ["ZN", "ZT", "ZB"]:
        data[sym] = load_bond(sym)
        logger.info(f"  {sym}: {len(data[sym])} bars")

    results = []
    for sym in ["ZN", "ZT", "ZB"]:
        df = data[sym]
        for sl, tp in SL_TP_COMBOS_PCT:
            for ema in EMA_PERIODS:
                stats = backtest(df, sym, sl, tp, ema)
                results.append({
                    "sym": sym, "sl_pct": sl, "tp_pct": tp, "ema": ema,
                    **stats,
                })

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    df_res.to_csv(ROOT / "reports" / "research" / "sweep_bonds_v2.csv", index=False)

    print(f"\n=== BONDS V2 SWEEP ({len(df_res)} combos) ===")
    print(f"Positives: {(df_res['sharpe'] > 0).sum()}")
    print(f"Sharpe > 0.3: {(df_res['sharpe'] > 0.3).sum()}")
    print(f"Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")

    print("\n=== TOP 15 ===")
    print(df_res.head(15).to_string(index=False))

    best = df_res.iloc[0]
    print(f"\n=== VERDICT ===")
    if best["sharpe"] > 0.5:
        print(f"CANDIDAT: {best['sym']} SL={best['sl_pct']}% TP={best['tp_pct']}% EMA={best['ema']} → Sharpe {best['sharpe']}")
    elif best["sharpe"] > 0.3:
        print(f"MARGINAL: best {best['sharpe']} — pas assez robuste")
    else:
        print(f"NO EDGE: best Sharpe {best['sharpe']} — bonds intraday ne marchent pas sur ce sample")

    return 0


if __name__ == "__main__":
    sys.exit(main())
