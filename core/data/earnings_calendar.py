"""Earnings calendar fetcher via yfinance.

Source unique pour PEAD runtime (pead_runner.py) et research (script discovery).
Coherence backtest/live garantie : meme source de donnees.

Storage : data/us_research/earnings_history.parquet (format yfinance natif)
Cache : refresh seulement si mtime > 24h ou force=True.

API simple :
    refresh_earnings(tickers, lookback_days=30) -> int  # nb new events ajoutes
    get_recent_earnings(tickers, target_date, window_days=2) -> dict
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
EARNINGS_PARQUET = ROOT / "data" / "us_research" / "earnings_history.parquet"
CACHE_TTL_HOURS = 24


def _load_existing() -> pd.DataFrame:
    """Load existing earnings parquet, return empty df if missing/corrupt."""
    if not EARNINGS_PARQUET.exists():
        return pd.DataFrame(columns=["Earnings Date", "EPS Estimate", "Reported EPS", "Surprise(%)", "symbol"])
    try:
        return pd.read_parquet(EARNINGS_PARQUET)
    except Exception as exc:
        logger.warning(f"earnings parquet corrupted ({exc}), starting fresh")
        return pd.DataFrame(columns=["Earnings Date", "EPS Estimate", "Reported EPS", "Surprise(%)", "symbol"])


def _is_cache_fresh() -> bool:
    """True if parquet exists and mtime < CACHE_TTL_HOURS old."""
    if not EARNINGS_PARQUET.exists():
        return False
    age_hours = (pd.Timestamp.now() - pd.Timestamp.fromtimestamp(EARNINGS_PARQUET.stat().st_mtime)).total_seconds() / 3600
    return age_hours < CACHE_TTL_HOURS


def refresh_earnings(tickers: list[str], force: bool = False) -> int:
    """Fetch earnings_history pour chaque ticker via yfinance, merger au parquet.

    Args:
        tickers: liste de tickers SP500
        force: ignore le cache TTL et force le fetch

    Returns:
        Nombre d'evenements ajoutes/mis a jour.
    """
    if not force and _is_cache_fresh():
        logger.debug("earnings cache fresh, skip refresh")
        return 0

    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance non installe (pip install yfinance)")
        return 0

    existing = _load_existing()
    new_rows = []
    for tk in tickers:
        try:
            t = yf.Ticker(tk)
            hist = t.earnings_history
            if hist is None or hist.empty:
                continue
            df = hist.reset_index() if hist.index.name else hist.copy()
            # yfinance peut retourner colonnes legerement differentes selon version
            rename_map = {}
            for c in df.columns:
                low = c.lower()
                if "date" in low and "earnings" in low:
                    rename_map[c] = "Earnings Date"
                elif "estimate" in low:
                    rename_map[c] = "EPS Estimate"
                elif "reported" in low:
                    rename_map[c] = "Reported EPS"
                elif "surprise" in low and "%" in c:
                    rename_map[c] = "Surprise(%)"
            df = df.rename(columns=rename_map)
            keep = [c for c in ["Earnings Date", "EPS Estimate", "Reported EPS", "Surprise(%)"] if c in df.columns]
            if not keep:
                continue
            df = df[keep].copy()
            df["symbol"] = tk
            new_rows.append(df)
        except Exception as exc:
            logger.warning(f"yfinance fetch {tk}: {exc}")

    if not new_rows:
        logger.info("earnings refresh: 0 new events")
        return 0

    combined = pd.concat([existing] + new_rows, ignore_index=True)
    # dedupe : meme symbol + meme date
    combined["Earnings Date"] = pd.to_datetime(combined["Earnings Date"], errors="coerce", utc=True)
    combined = combined.dropna(subset=["Earnings Date"])
    combined = combined.drop_duplicates(subset=["symbol", "Earnings Date"], keep="last")
    n_new = len(combined) - len(existing)
    EARNINGS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(EARNINGS_PARQUET, index=False)
    logger.info(f"earnings refresh: +{n_new} events, total {len(combined)}")
    return n_new


def get_recent_earnings(
    tickers: list[str], target_date: date, window_days: int = 2
) -> dict[str, dict[str, Any]]:
    """Retourne les earnings publiees dans [target_date - window_days, target_date].

    PEAD: on entre J+1 apres earnings. Avec target_date=today et window=2,
    on capture les earnings d'hier (after-market) et avant-hier.

    Returns:
        {ticker: {date: ISO, surprise_pct: float, eps_estimate: float, eps_actual: float}}
        Vide si pas d'earnings dans la fenetre.
    """
    df = _load_existing()
    if df.empty:
        return {}
    df = df[df["symbol"].isin(tickers)].copy()
    if df.empty:
        return {}

    df["Earnings Date"] = pd.to_datetime(df["Earnings Date"], errors="coerce", utc=True)
    df = df.dropna(subset=["Earnings Date"])
    df["earn_date"] = df["Earnings Date"].dt.tz_convert("America/New_York").dt.date

    cutoff_lo = target_date - timedelta(days=window_days)
    df = df[(df["earn_date"] >= cutoff_lo) & (df["earn_date"] <= target_date)]
    df = df.dropna(subset=["Surprise(%)"])

    out: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        out[row["symbol"]] = {
            "date": row["earn_date"].isoformat(),
            "surprise_pct": float(row["Surprise(%)"]),
            "eps_estimate": float(row["EPS Estimate"]) if pd.notna(row["EPS Estimate"]) else None,
            "eps_actual": float(row["Reported EPS"]) if pd.notna(row["Reported EPS"]) else None,
        }
    return out
