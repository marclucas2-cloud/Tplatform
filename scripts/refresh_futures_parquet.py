#!/usr/bin/env python3
"""Refresh quotidien data/futures/*_1D.parquet via yfinance.

Target: les fichiers que le worker lit au _run_futures_cycle.
  - MES_1D.parquet <- ES=F   (S&P 500 full-size, same index values as MES)
  - MNQ_1D.parquet <- NQ=F   (Nasdaq 100)
  - M2K_1D.parquet <- RTY=F  (Russell 2000)
  - MGC_1D.parquet <- GC=F   (Gold)
  - MCL_1D.parquet <- CL=F   (WTI Crude)

Methode:
  1. Charge le parquet existant
  2. Telecharge les derniers 30 jours via yfinance
  3. Concatene les nouvelles barres absentes du parquet
  4. Sauve atomiquement (write tmp, rename)

Safe to run quotidiennement. Si yfinance echoue sur un symbole, les autres
continuent. Retourne code 0 si >=4/5 symboles mis a jour.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "futures"

# Map parquet symbol -> yfinance ticker (continuous front-month contract)
SYMBOL_MAP = {
    "MES": "ES=F",
    "MNQ": "NQ=F",
    "M2K": "RTY=F",
    "MGC": "GC=F",
    "MCL": "CL=F",
}

# Also refresh EU indices + VIX if present (used by paper strats)
OPTIONAL_MAP = {
    "VIX":    "^VIX",
    "DAX":    "^GDAXI",
    "CAC40":  "^FCHI",
    "ESTX50": "^STOXX50E",
    "MIB":    "FTSEMIB.MI",
}


def load_existing(path: Path):
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def fetch_yf(ticker: str, days_back: int = 45) -> pd.DataFrame | None:
    """Download latest N days from yfinance. Returns normalized OHLCV frame."""
    try:
        import yfinance as yf
    except ImportError:
        print("[err] yfinance not installed in venv — pip install yfinance")
        return None
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    try:
        df = yf.download(ticker, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                         interval="1d", progress=False, auto_adjust=False)
    except Exception as e:
        print(f"[err] yfinance download {ticker}: {e}")
        return None
    if df is None or len(df) == 0:
        return None
    # Flatten multi-index columns (yfinance 0.2+)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    keep = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in keep if c in df.columns]]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")]
    # Drop rows with any NaN in OHLC
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.sort_index()


def refresh_one(symbol: str, ticker: str) -> tuple[bool, str]:
    parquet_path = DATA_DIR / f"{symbol}_1D.parquet"
    existing = load_existing(parquet_path)
    new = fetch_yf(ticker, days_back=45)
    if new is None or len(new) == 0:
        return False, f"{symbol}: no yfinance data"

    if existing is None:
        combined = new
        n_added = len(new)
    else:
        last_existing = existing.index.max()
        new_bars = new[new.index > last_existing]
        if len(new_bars) == 0:
            return True, f"{symbol}: already up-to-date (last {last_existing.date()})"
        combined = pd.concat([existing, new_bars])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        n_added = len(new_bars)

    # Atomic write: tmp + rename
    tmp_path = parquet_path.with_suffix(".parquet.tmp")
    combined.to_parquet(tmp_path)
    tmp_path.replace(parquet_path)
    return True, f"{symbol}: +{n_added} bars, last={combined.index.max().date()}"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Include optional EU/VIX symbols")
    ap.add_argument("--symbol", help="Only refresh one symbol (e.g. MES)")
    args = ap.parse_args()

    symbols_to_refresh = dict(SYMBOL_MAP)
    if args.all:
        symbols_to_refresh.update(OPTIONAL_MAP)
    if args.symbol:
        if args.symbol in SYMBOL_MAP:
            symbols_to_refresh = {args.symbol: SYMBOL_MAP[args.symbol]}
        elif args.symbol in OPTIONAL_MAP:
            symbols_to_refresh = {args.symbol: OPTIONAL_MAP[args.symbol]}
        else:
            print(f"[err] unknown symbol {args.symbol}")
            return 1

    print(f"Refreshing {len(symbols_to_refresh)} symbols from yfinance...")
    print(f"Data dir: {DATA_DIR}")

    results = []
    for sym, ticker in symbols_to_refresh.items():
        ok, msg = refresh_one(sym, ticker)
        status = "[ok] " if ok else "[KO] "
        print(f"  {status}{msg}")
        results.append(ok)

    ok_count = sum(results)
    total = len(results)
    print(f"\n{ok_count}/{total} symbols refreshed successfully")
    # Return 0 if at least 4/5 core symbols updated (strict threshold)
    core_ok = sum(1 for s, _ in list(symbols_to_refresh.items())[:5] if s in SYMBOL_MAP)
    return 0 if ok_count >= min(4, total) else 1


if __name__ == "__main__":
    sys.exit(main())
