#!/usr/bin/env python3
"""V2 Overnight MES — sweep avec filtres additionnels.

V1 (60 combos, sweep_overnight_mes_mnq.py) n'a trouve aucun edge:
max Sharpe 0.07 sur MES meme famille de stratégies.

V2 ajoute :
  - SL/TP ranges plus larges (comme MNQ: 20-100 pts)
  - Filtre VIX (trader seulement quand VIX < seuil)
  - Filtre régime (SPY > SMA200)
  - Filtre ADX (trending market seulement)
  - Filtre dual-day (2 jours consécutifs haussiers)

Question: un de ces filtres sauve-t-il l'edge MES Overnight ?

Output: reports/research/sweep_mes_v2.csv + verdict per filter.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mes_v2")

ROOT = Path(__file__).resolve().parent.parent

MES_SPEC = {"mult": 5.0, "tick": 0.25, "commission": 1.24, "slip_ticks_rt": 1}

# Wider SL/TP range than V1
SL_TP_COMBOS = [
    (20, 30),   # V1 best-similar
    (30, 50),   # V1 baseline
    (40, 60),
    (50, 75),
    (50, 100),
    (60, 120),
    (80, 120),
    (80, 160),
    (100, 150),
    (100, 200),
]

EMA_PERIODS = [20, 50]

# Filter options
VIX_FILTERS = [None, 20, 25, 30]  # None = no filter, else only when VIX < threshold
REGIMES = ["none", "spy_bull"]
ADX_FILTERS = [None, 20]  # None = no filter, else only when ADX > threshold
DUAL_DAY = [False, True]  # Require 2 consecutive days above EMA


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


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ADX indicator."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    dm_plus = high.diff().where(high.diff() > low.diff().abs(), 0).clip(lower=0)
    dm_minus = (-low.diff()).where((-low.diff()) > high.diff().abs(), 0).clip(lower=0)

    di_plus = 100 * (dm_plus.rolling(period).sum() / atr.replace(0, np.nan))
    di_minus = 100 * (dm_minus.rolling(period).sum() / atr.replace(0, np.nan))

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.rolling(period).mean()
    return adx


def backtest(
    df: pd.DataFrame,
    vix: pd.DataFrame | None,
    spy: pd.Series,
    sl_pts: float,
    tp_pts: float,
    ema_period: int,
    vix_filter: float | None,
    regime: str,
    adx_filter: float | None,
    dual_day: bool,
    max_hold: int = 10,
) -> dict:
    spec = MES_SPEC
    slip = spec["slip_ticks_rt"] * spec["tick"] * spec["mult"]
    cost_rt = spec["commission"] + slip

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=ema_period, adjust=False).mean()

    # Filters
    if regime == "spy_bull" and len(spy) > 0:
        spy_sma = spy.rolling(200).mean()
        spy_bull = (spy > spy_sma).reindex(df.index, method="ffill").fillna(False)
    else:
        spy_bull = pd.Series(True, index=df.index)

    if vix_filter is not None and vix is not None:
        vix_val = vix["close"].reindex(df.index, method="ffill")
        vix_ok = vix_val < vix_filter
    else:
        vix_ok = pd.Series(True, index=df.index)

    if adx_filter is not None:
        adx = compute_adx(df)
        adx_ok = (adx > adx_filter).fillna(False)
    else:
        adx_ok = pd.Series(True, index=df.index)

    pnls = []
    start = max(ema_period, 200) if regime == "spy_bull" else max(ema_period, 15)
    i = start
    while i < len(df) - 1:
        c = float(close.iloc[i])
        e = float(ema.iloc[i])
        if c <= e or not np.isfinite(e):
            i += 1
            continue
        # Dual-day filter
        if dual_day and i > 0:
            prev_c = float(close.iloc[i - 1])
            prev_e = float(ema.iloc[i - 1])
            if not (prev_c > prev_e and np.isfinite(prev_e)):
                i += 1
                continue
        if not bool(spy_bull.iloc[i]):
            i += 1
            continue
        if not bool(vix_ok.iloc[i]):
            i += 1
            continue
        if not bool(adx_ok.iloc[i]):
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
        for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
            h = float(high.iloc[j])
            l = float(low.iloc[j])
            o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl:
                    exit_idx = j; exit_px = o; break
                if o >= tp:
                    exit_idx = j; exit_px = o; break
            if l <= sl and h >= tp:
                exit_idx = j; exit_px = sl; break
            if l <= sl:
                exit_idx = j; exit_px = sl; break
            if h >= tp:
                exit_idx = j; exit_px = tp; break
        if exit_idx is None:
            exit_idx = min(entry_idx + max_hold - 1, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])

        pnl_pts = exit_px - entry_px
        pnl_usd = pnl_pts * spec["mult"] - cost_rt
        pnls.append(pnl_usd)
        i = exit_idx + 1

    n = len(pnls)
    if n < 30:
        return {"n": n, "sharpe": 0, "total": 0, "wr": 0, "mdd": 0}
    arr = np.array(pnls)
    total = float(arr.sum())
    wr = float((arr > 0).mean())
    mu = float(arr.mean())
    sd = float(arr.std())
    span = (df.index[-1] - df.index[0]).days
    tpy = n / span * 365 if span > 0 else 252
    sharpe = mu / sd * np.sqrt(tpy) if sd > 0 else 0
    cum = arr.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {"n": n, "sharpe": round(sharpe, 2), "total": round(total, 0),
            "wr": round(wr, 2), "mdd": round(mdd, 0)}


def main() -> int:
    logger.info("Loading data…")
    mes = load_futures("MES")
    spy = load_spy()
    try:
        vix = load_futures("VIX")
    except Exception:
        vix = None
    logger.info(f"MES: {len(mes)} bars")
    logger.info(f"SPY: {len(spy)} bars")
    logger.info(f"VIX: {len(vix) if vix is not None else 0} bars")

    logger.info(f"\nRunning V2 MES sweep...")
    total_combos = len(SL_TP_COMBOS) * len(EMA_PERIODS) * len(VIX_FILTERS) * len(REGIMES) * len(ADX_FILTERS) * len(DUAL_DAY)
    logger.info(f"Total combos: {total_combos}")

    results = []
    count = 0
    for sl, tp in SL_TP_COMBOS:
        for ema in EMA_PERIODS:
            for vf in VIX_FILTERS:
                for reg in REGIMES:
                    for af in ADX_FILTERS:
                        for dd in DUAL_DAY:
                            count += 1
                            stats = backtest(mes, vix, spy, sl, tp, ema, vf, reg, af, dd)
                            results.append({
                                "sl": sl, "tp": tp, "ema": ema,
                                "vix_max": vf or "any",
                                "regime": reg,
                                "adx_min": af or "any",
                                "dual_day": dd,
                                **stats,
                            })
                            if count % 50 == 0:
                                logger.info(f"  {count}/{total_combos}")

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values("sharpe", ascending=False)
    ROOT.joinpath("reports", "research").mkdir(parents=True, exist_ok=True)
    df_res.to_csv(ROOT / "reports" / "research" / "sweep_mes_v2.csv", index=False)

    print(f"\n=== V2 MES SWEEP RESULTS ({len(df_res)} combos) ===")
    print(f"Positives: {(df_res['sharpe'] > 0).sum()}")
    print(f"Sharpe > 0.3: {(df_res['sharpe'] > 0.3).sum()}")
    print(f"Sharpe > 0.5: {(df_res['sharpe'] > 0.5).sum()}")
    print(f"Sharpe > 0.7: {(df_res['sharpe'] > 0.7).sum()}")
    print(f"Sharpe > 1.0: {(df_res['sharpe'] > 1.0).sum()}")

    print("\n=== TOP 15 combos ===")
    top15 = df_res.head(15)
    print(top15.to_string(index=False))

    best = df_res.iloc[0]
    print(f"\n=== VERDICT V2 ===")
    if best["sharpe"] > 1.0:
        print(f"STRONG EDGE FOUND: Sharpe {best['sharpe']} with filters")
        print(f"  SL={best['sl']} TP={best['tp']} EMA={best['ema']} VIX<{best['vix_max']} "
              f"regime={best['regime']} ADX>{best['adx_min']} dual={best['dual_day']}")
    elif best["sharpe"] > 0.5:
        print(f"MODERATE EDGE: Sharpe {best['sharpe']} — candidat paper")
        print(f"  SL={best['sl']} TP={best['tp']} EMA={best['ema']} VIX<{best['vix_max']} "
              f"regime={best['regime']} ADX>{best['adx_min']} dual={best['dual_day']}")
    elif best["sharpe"] > 0.3:
        print(f"MARGINAL: Sharpe {best['sharpe']} — pas d'edge exploitable")
    else:
        print(f"NO EDGE FOUND: best Sharpe {best['sharpe']} — MES Overnight DEFINITIVELY DEAD")

    return 0


if __name__ == "__main__":
    sys.exit(main())
