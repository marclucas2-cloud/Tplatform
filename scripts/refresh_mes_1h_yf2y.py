#!/usr/bin/env python3
"""Refresh data/futures/MES_1H_YF2Y.parquet via yfinance (ES=F, 1h, 2Y).

B6 iter3 (2026-04-19). Ce fichier est consomme par:
  - core/worker/cycles/paper_cycles.py:run_btc_asia_mes_leadlag_paper_cycle
  - scripts/research/backtest_t3a_mes_btc_leadlag.py
  - scripts/research/backtest_futures_intraday_mr.py

Sans ce refresh, btc_asia_mes_leadlag paper log `data files missing` a
chaque tick 10h30-10h59 Paris -> pas de paper journal -> promotion_gate
bloque 30j.

yfinance limite: 1h interval = max 730j (2Y) backfill.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PARQUET = ROOT / "data" / "futures" / "MES_1H_YF2Y.parquet"
TICKER = "ES=F"  # continuous front-month S&P E-mini, same index values as MES


def fetch_yf_1h(ticker: str, days: int = 720) -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except ImportError:
        print("[err] yfinance not installed")
        return None
    try:
        # yfinance 1h limit ~730d — use period="2y" shorthand for safety
        df = yf.download(
            ticker,
            period=f"{min(days, 729)}d",
            interval="1h",
            progress=False,
            auto_adjust=False,
        )
    except Exception as e:
        print(f"[err] yfinance {ticker}: {e}")
        return None
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    keep = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in keep if c in df.columns]]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.sort_index()


def main() -> int:
    PARQUET.parent.mkdir(parents=True, exist_ok=True)

    existing: pd.DataFrame | None = None
    if PARQUET.exists():
        existing = pd.read_parquet(PARQUET)
        existing.columns = [c.lower() for c in existing.columns]
        existing.index = pd.to_datetime(existing.index)
        if existing.index.tz is not None:
            existing.index = existing.index.tz_localize(None)

    new = fetch_yf_1h(TICKER, days=730)
    if new is None or len(new) == 0:
        print("[err] yfinance returned no data — aborting without overwrite")
        return 1

    if existing is None:
        combined = new
        added = len(new)
    else:
        new_bars = new[new.index > existing.index.max()]
        if len(new_bars) == 0:
            print(f"{PARQUET.name}: already up-to-date (last {existing.index.max()})")
            return 0
        combined = pd.concat([existing, new_bars])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        added = len(new_bars)

    # Drop anything older than 750 days to keep file bounded
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=750)
    combined = combined[combined.index >= cutoff]

    tmp = PARQUET.with_suffix(".parquet.tmp")
    combined.to_parquet(tmp)
    tmp.replace(PARQUET)
    print(
        f"{PARQUET.name}: +{added} bars, rows={len(combined)}, "
        f"first={combined.index.min()}, last={combined.index.max()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
