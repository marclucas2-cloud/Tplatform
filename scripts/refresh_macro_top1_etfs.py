#!/usr/bin/env python3
"""Refresh quotidien du cache ETF lu par macro_top1_rotation paper runner.

Target: data/research/target_alpha_us_sectors_2026_04_24_prices.parquet
Universe: 8 macro ETFs + 11 sector ETFs (utilises par macro_top1 + research).

Methode (alignee sur refresh_futures_parquet.py):
  1. Charge le parquet existant (preserve DatetimeIndex valide, drop "datetime" legacy)
  2. Telecharge yfinance les derniers ~45 jours
  3. Concatene les nouvelles barres
  4. Sauve atomiquement (write tmp + rename)

Safe to run quotidiennement via cron. Si yfinance fail sur un symbole, les autres
continuent. Retourne 0 si >= N-2 / N symboles maj.

Cron VPS suggere:
  35 21 * * * /opt/trading-platform/.venv/bin/python /opt/trading-platform/scripts/refresh_macro_top1_etfs.py >> /opt/trading-platform/logs/data_refresh/macro_top1_cron.log 2>&1
(5 min apres refresh_futures_parquet, meme creneau 21:30-21:35 UTC apres close US)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "research" / "target_alpha_us_sectors_2026_04_24_prices.parquet"

# Universe = same as scripts/research/target_alpha_us_sectors_and_new_assets_2026_04_24.py
SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY"]
MACRO_ETFS = ["TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]
UNIVERSE = sorted(set(SECTOR_ETFS + MACRO_ETFS))


def load_existing(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    has_valid_dt_index = (
        isinstance(df.index, pd.DatetimeIndex) and df.index.notna().any()
    )
    if has_valid_dt_index:
        pass
    elif "datetime" in df.columns:
        df.index = pd.to_datetime(df["datetime"], errors="coerce")
    else:
        df.index = pd.to_datetime(df.index, errors="coerce")
    if "datetime" in df.columns:
        df = df.drop(columns=["datetime"])
    df = df[df.index.notna()]
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_yf_panel(symbols: list[str], days_back: int = 45) -> pd.DataFrame | None:
    """Download yfinance close panel for `symbols`. Returns DataFrame index=date, cols=symbols."""
    try:
        import yfinance as yf
    except ImportError:
        print("[err] yfinance not installed in venv")
        return None
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    try:
        raw = yf.download(
            symbols,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        print(f"[err] yfinance batch download: {e}")
        return None
    if raw is None or raw.empty:
        return None

    frames = {}
    for sym in symbols:
        if isinstance(raw.columns, pd.MultiIndex):
            if sym not in raw.columns.get_level_values(0):
                continue
            close = raw[sym]["Close"].copy()
        else:
            # Single symbol case
            close = raw["Close"].copy() if "Close" in raw.columns else None
            if close is None:
                continue
        close.index = pd.to_datetime(close.index)
        if close.index.tz is not None:
            close.index = close.index.tz_localize(None)
        close = close.dropna()
        frames[sym] = close.rename(sym)
    if not frames:
        return None
    return pd.DataFrame(frames).sort_index().dropna(how="all")


def main() -> int:
    print(f"Refreshing {len(UNIVERSE)} ETFs from yfinance...")
    print(f"Cache path: {CACHE_PATH}")

    existing = load_existing(CACHE_PATH)
    fresh = fetch_yf_panel(UNIVERSE, days_back=45)
    if fresh is None or fresh.empty:
        print("[err] yfinance returned no data")
        return 1

    if existing is None:
        combined = fresh
        n_added = len(fresh)
    else:
        last_existing = existing.index.max()
        new_rows = fresh[fresh.index > last_existing]
        if len(new_rows) == 0:
            print(f"[ok] cache already up-to-date (last {last_existing.date()})")
            return 0
        # Concat: align columns (existing may have slightly different col set)
        combined = pd.concat([existing, new_rows], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        n_added = len(new_rows)

    # Defense en profondeur: drop legacy "datetime" col si jamais reapparait
    if "datetime" in combined.columns:
        combined = combined.drop(columns=["datetime"])

    # Atomic write
    tmp = CACHE_PATH.with_suffix(".parquet.tmp")
    combined.to_parquet(tmp)
    tmp.replace(CACHE_PATH)

    print(f"[ok] +{n_added} bars, last={combined.index.max().date()}, total_rows={len(combined)}")
    print(f"[ok] cols={len(combined.columns)} symbols={list(combined.columns)[:5]}...")

    # Sanity check: macro top1 universe doit etre present
    missing = [s for s in MACRO_ETFS + SECTOR_ETFS if s not in combined.columns]
    if missing:
        print(f"[warn] {len(missing)} missing symbols: {missing}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
