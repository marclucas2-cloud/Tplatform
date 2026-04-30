"""Earnings calendar fetcher via Finnhub free tier.

Source unique pour PEAD runtime (pead_runner.py).
Token Finnhub via env var FINNHUB_API_KEY (cf .env).

Free tier limits : 60 calls/min. Comme on bulk via /calendar/earnings sans
filtre symbol (toute la window), ca tient large.

Storage : data/us_research/earnings_history.parquet (format compatible discoverer)
Cache : refresh seulement si mtime > 24h ou force=True.

Format parquet : Earnings Date (UTC tz-aware), EPS Estimate, Reported EPS,
Surprise(%), symbol. Surprise calcule = (actual - estimate) / |estimate| * 100.

Historique :
- v1 2026-04-30 : yfinance (cassé : limite 4 trimestres + format diff)
- v2 2026-04-30 : Finnhub free tier (couvre fenetre arbitraire + surprise structuree)

API simple :
    refresh_earnings(tickers, force=False) -> int  # nb new events ajoutes
    get_recent_earnings(tickers, target_date, window_days=2) -> dict
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
EARNINGS_PARQUET = ROOT / "data" / "us_research" / "earnings_history.parquet"
CACHE_TTL_HOURS = 24
FINNHUB_BASE = "https://finnhub.io/api/v1"
DEFAULT_REFRESH_LOOKBACK_DAYS = 30
DEFAULT_REFRESH_LOOKAHEAD_DAYS = 7


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


def _fetch_finnhub_window(date_from: str, date_to: str, token: str) -> list[dict]:
    """Fetch /calendar/earnings sur une fenetre, retourne tous les events.

    Pas de filtre symbol cote API : on filtre cote Python (1 seul call HTTP).
    """
    import requests
    url = f"{FINNHUB_BASE}/calendar/earnings"
    params = {"from": date_from, "to": date_to, "token": token}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json() or {}
    return data.get("earningsCalendar", []) or []


def refresh_earnings(
    tickers: list[str],
    force: bool = False,
    lookback_days: int = DEFAULT_REFRESH_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_REFRESH_LOOKAHEAD_DAYS,
) -> int:
    """Fetch earnings calendar via Finnhub, filtrer sur tickers, merger au parquet.

    Args:
        tickers: liste de symbols a garder (le call API recupere toute la window
                 sans filtre symbol, plus efficient cote API)
        force: ignore le cache TTL et force le fetch
        lookback_days: combien de jours dans le passe regarder
        lookahead_days: combien de jours dans le futur regarder

    Returns:
        Nombre d'evenements ajoutes/mis a jour.
    """
    if not force and _is_cache_fresh():
        logger.debug("earnings cache fresh, skip refresh")
        return 0

    token = os.getenv("FINNHUB_API_KEY", "").strip()
    if not token:
        logger.error("FINNHUB_API_KEY non defini (cf .env), earnings refresh skipped")
        return 0

    today = date.today()
    window_start = today - timedelta(days=lookback_days)
    window_end = today + timedelta(days=lookahead_days)

    # Finnhub free tier coupe a 1500 events/call. Sur >7j on rate la fin de fenetre.
    # On chunk en 7j pour rester sous le seuil et garantir couverture complete.
    events: list[dict] = []
    chunk_start = window_start
    try:
        while chunk_start <= window_end:
            chunk_end = min(chunk_start + timedelta(days=7), window_end)
            batch = _fetch_finnhub_window(
                chunk_start.isoformat(), chunk_end.isoformat(), token
            )
            events.extend(batch)
            chunk_start = chunk_end + timedelta(days=1)
    except Exception as exc:
        logger.warning(f"finnhub fetch failed ({exc}), keeping stale cache")
        return 0

    tickers_set = set(tickers)
    rows = []
    for e in events:
        sym = e.get("symbol")
        if sym not in tickers_set:
            continue
        eps_est = e.get("epsEstimate")
        eps_act = e.get("epsActual")
        # Skip events sans actual publie (futur ou null)
        if eps_act is None or eps_est is None:
            continue
        try:
            eps_est_f = float(eps_est)
            eps_act_f = float(eps_act)
        except (TypeError, ValueError):
            continue
        if eps_est_f == 0:
            continue
        surprise_pct = (eps_act_f - eps_est_f) / abs(eps_est_f) * 100.0
        rows.append({
            "Earnings Date": pd.Timestamp(e.get("date"), tz="UTC"),
            "EPS Estimate": eps_est_f,
            "Reported EPS": eps_act_f,
            "Surprise(%)": surprise_pct,
            "symbol": sym,
        })

    if not rows:
        logger.info(f"earnings refresh: 0 new events from finnhub (window {window_start}->{window_end})")
        # On touch quand meme le mtime pour respecter TTL
        if EARNINGS_PARQUET.exists():
            EARNINGS_PARQUET.touch()
        return 0

    new_df = pd.DataFrame(rows)
    existing = _load_existing()
    if not existing.empty:
        existing["Earnings Date"] = pd.to_datetime(existing["Earnings Date"], errors="coerce", utc=True)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.dropna(subset=["Earnings Date"])
    combined = combined.drop_duplicates(subset=["symbol", "Earnings Date"], keep="last")
    n_new = max(0, len(combined) - len(existing))

    EARNINGS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(EARNINGS_PARQUET, index=False)
    logger.info(f"earnings refresh: +{n_new} events, total {len(combined)} (window {window_start}->{window_end})")
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
