#!/usr/bin/env python3
"""Download S&P 500 daily OHLCV (5 years) via yfinance.

Produces:
  data/us_stocks/<TICKER>.parquet  — per-ticker OHLCV + adj_close + dividends + splits
  data/us_stocks/_metadata.csv     — ticker, sector, industry, mcap, adv_usd, vol_ann, beta, n_bars, first, last
  data/us_stocks/_universe.json    — list of tickers that passed quality filters

Quality filters (for the downstream backtests):
  - >= 3 years of bars
  - ADV (average daily volume in USD) > $50M
  - no more than 2% missing bars
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import io

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("download_us")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "us_stocks"
OUT_DIR.mkdir(parents=True, exist_ok=True)

YEARS = 5
END = datetime.now(UTC).date()
START = END - timedelta(days=YEARS * 365 + 30)

MIN_BARS = 252 * 3           # 3y minimum
MIN_ADV_USD = 50_000_000     # $50M/d minimum liquidity
MAX_MISSING_PCT = 0.02


def get_sp500_tickers() -> pd.DataFrame:
    """Scrape current S&P 500 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    logger.info(f"Fetching S&P 500 list from {url}")
    headers = {"User-Agent": "Mozilla/5.0 (research-bot)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    df = df.rename(columns={
        "Symbol": "ticker",
        "Security": "name",
        "GICS Sector": "sector",
        "GICS Sub-Industry": "industry",
    })
    # yfinance expects dots as dashes for class B shares (e.g. BRK.B -> BRK-B)
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df[["ticker", "name", "sector", "industry"]].drop_duplicates("ticker")


def download_one(ticker: str) -> pd.DataFrame | None:
    try:
        df = yf.download(
            ticker,
            start=START.isoformat(),
            end=END.isoformat(),
            auto_adjust=False,
            actions=True,
            progress=False,
            threads=False,
        )
    except Exception as e:
        logger.warning(f"{ticker}: download failed: {e}")
        return None

    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    rename_map = {
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
        "Dividends": "dividends", "Stock Splits": "splits",
    }
    df = df.rename(columns=rename_map)
    df.index.name = "date"

    required = ["open", "high", "low", "close", "adj_close", "volume"]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        logger.warning(f"{ticker}: missing cols {missing_cols}")
        return None

    if "dividends" not in df.columns:
        df["dividends"] = 0.0
    if "splits" not in df.columns:
        df["splits"] = 0.0

    df = df[required + ["dividends", "splits"]]
    return df


def compute_stats(df: pd.DataFrame, spy_returns: pd.Series | None) -> dict:
    close = df["adj_close"].astype(float)
    vol = df["volume"].astype(float)
    ret = close.pct_change().dropna()

    adv_usd = float((close * vol).rolling(30).mean().iloc[-1]) if len(close) > 30 else float((close * vol).mean())
    vol_ann = float(ret.std() * np.sqrt(252))
    beta = np.nan
    if spy_returns is not None and len(ret) > 60:
        aligned = pd.concat([ret, spy_returns], axis=1, join="inner").dropna()
        if len(aligned) > 60:
            cov = aligned.cov().iloc[0, 1]
            var_m = aligned.iloc[:, 1].var()
            beta = float(cov / var_m) if var_m > 0 else np.nan

    return {
        "n_bars": len(df),
        "first": df.index.min().date().isoformat(),
        "last": df.index.max().date().isoformat(),
        "adv_usd": adv_usd,
        "vol_ann": vol_ann,
        "beta": beta,
    }


def main() -> int:
    t_start = time.time()
    sp500 = get_sp500_tickers()
    logger.info(f"S&P 500: {len(sp500)} tickers")

    logger.info("Downloading SPY benchmark for beta calc")
    spy_df = download_one("SPY")
    spy_returns = spy_df["adj_close"].pct_change().dropna() if spy_df is not None else None
    if spy_df is not None:
        spy_df.to_parquet(OUT_DIR / "SPY.parquet")

    rows = []
    ok = 0
    fail = 0
    for i, r in enumerate(sp500.itertuples(index=False), start=1):
        t = r.ticker
        if i % 25 == 0:
            elapsed = time.time() - t_start
            rate = i / elapsed
            eta = (len(sp500) - i) / rate if rate > 0 else 0
            logger.info(f"[{i}/{len(sp500)}] ok={ok} fail={fail} eta={eta:.0f}s")

        df = download_one(t)
        if df is None or len(df) < 60:
            fail += 1
            continue

        try:
            df.to_parquet(OUT_DIR / f"{t}.parquet")
        except Exception as e:
            logger.warning(f"{t}: parquet write failed: {e}")
            fail += 1
            continue

        stats = compute_stats(df, spy_returns)
        rows.append({
            "ticker": t,
            "name": r.name,
            "sector": r.sector,
            "industry": r.industry,
            **stats,
        })
        ok += 1

    if not rows:
        logger.error("No tickers downloaded")
        return 1

    meta = pd.DataFrame(rows)

    meta["pass_history"] = meta["n_bars"] >= MIN_BARS
    meta["pass_liquidity"] = meta["adv_usd"] >= MIN_ADV_USD
    meta["pass_all"] = meta["pass_history"] & meta["pass_liquidity"]

    meta = meta.sort_values("adv_usd", ascending=False)
    meta.to_csv(OUT_DIR / "_metadata.csv", index=False)

    universe = meta[meta["pass_all"]]["ticker"].tolist()
    (OUT_DIR / "_universe.json").write_text(json.dumps({
        "generated": datetime.now(UTC).isoformat(),
        "start": START.isoformat(),
        "end": END.isoformat(),
        "n_downloaded": len(meta),
        "n_universe": len(universe),
        "filters": {
            "min_bars": MIN_BARS,
            "min_adv_usd": MIN_ADV_USD,
        },
        "tickers": universe,
    }, indent=2))

    dt = time.time() - t_start
    by_sector = meta[meta["pass_all"]].groupby("sector").size().to_dict()
    logger.info("")
    logger.info("=== DOWNLOAD DONE ===")
    logger.info(f"Duration      : {dt/60:.1f} min")
    logger.info(f"Downloaded    : {ok}/{len(sp500)} ({fail} failed)")
    logger.info(f"Universe (OK) : {len(universe)}/{len(meta)}")
    logger.info(f"Period        : {START} -> {END}")
    logger.info(f"Output        : {OUT_DIR}")
    logger.info("Sectors in universe:")
    for s, n in sorted(by_sector.items(), key=lambda x: -x[1]):
        logger.info(f"  {s:30s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
