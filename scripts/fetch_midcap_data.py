#!/usr/bin/env python3
"""Fetch historical daily OHLCV for ~200 midcap tickers via Alpaca API.

Usage:
    python scripts/fetch_midcap_data.py [--years 3] [--output data/midcap]

Requires: ALPACA_API_KEY, ALPACA_SECRET_KEY env vars.
Rate limits: Alpaca free tier = 200 req/min. Script paces at ~2/sec.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies_v2.us.midcap_stat_arb_scanner import ALL_TICKERS, GICS_INDUSTRY_GROUPS

logger = logging.getLogger("fetch_midcap")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

OUTPUT_DIR = ROOT / "data" / "midcap"


def fetch_all_tickers(
    years: int = 3,
    output_dir: Path | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for all midcap tickers via Alpaca.

    Returns dict of ticker -> DataFrame.
    Saves each ticker as Parquet in output_dir.
    """
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_SECRET_KEY")
    paper = os.environ.get("PAPER_TRADING", "true").lower() == "true"

    if not api_key or not api_secret:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        sys.exit(1)

    # Use alpaca-py if available, else requests
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        use_alpaca_sdk = True
        client = StockHistoricalDataClient(api_key, api_secret)
        logger.info("Using alpaca-py SDK")
    except ImportError:
        use_alpaca_sdk = False
        import requests
        base_url = "https://data.alpaca.markets/v2"
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }
        logger.info("Using Alpaca REST API directly")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)

    results = {}
    total = len(ALL_TICKERS)
    success = 0
    failed = []

    logger.info(f"Fetching {total} tickers, {years} years ({start_date.date()} to {end_date.date()})")

    for i, ticker in enumerate(ALL_TICKERS):
        parquet_path = output_dir / f"{ticker}.parquet"

        # Skip if already fetched recently (< 24h old)
        if parquet_path.exists():
            age_hours = (time.time() - parquet_path.stat().st_mtime) / 3600
            if age_hours < 24:
                try:
                    df = pd.read_parquet(parquet_path)
                    results[ticker] = df
                    success += 1
                    logger.debug(f"[{i+1}/{total}] {ticker}: cached ({len(df)} bars)")
                    continue
                except Exception:
                    pass  # Re-fetch if corrupt

        try:
            if use_alpaca_sdk:
                df = _fetch_alpaca_sdk(client, ticker, start_date, end_date)
            else:
                df = _fetch_alpaca_rest(ticker, start_date, end_date, headers, base_url)

            if df is not None and len(df) > 100:
                df.to_parquet(parquet_path)
                results[ticker] = df
                success += 1
                logger.info(f"[{i+1}/{total}] {ticker}: {len(df)} bars OK")
            else:
                failed.append(ticker)
                logger.warning(f"[{i+1}/{total}] {ticker}: insufficient data ({len(df) if df is not None else 0} bars)")

        except Exception as e:
            failed.append(ticker)
            logger.warning(f"[{i+1}/{total}] {ticker}: FAILED — {e}")

        # Rate limit: ~2 requests/sec to stay under 200/min
        time.sleep(0.5)

    # Summary
    logger.info(f"\nFetch complete: {success}/{total} tickers OK, {len(failed)} failed")
    if failed:
        logger.info(f"Failed tickers: {', '.join(failed[:20])}")

    # Save metadata
    meta = {
        "timestamp": datetime.now().isoformat(),
        "tickers_fetched": success,
        "tickers_failed": len(failed),
        "failed_list": failed,
        "start_date": str(start_date.date()),
        "end_date": str(end_date.date()),
        "groups": {g: len(t) for g, t in GICS_INDUSTRY_GROUPS.items()},
    }
    with open(output_dir / "fetch_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    return results


def _fetch_alpaca_sdk(client, ticker, start_date, end_date):
    """Fetch via alpaca-py SDK."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=start_date,
        end=end_date,
    )
    bars = client.get_stock_bars(request)
    df = bars.df

    if df.empty:
        return None

    # Handle multi-index (symbol, timestamp)
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")

    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Rename columns to standard
    rename = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
    df = df.rename(columns=rename)

    return df[["open", "high", "low", "close", "volume"]]


def _fetch_alpaca_rest(ticker, start_date, end_date, headers, base_url):
    """Fetch via REST API directly."""
    import requests

    url = f"{base_url}/stocks/{ticker}/bars"
    params = {
        "start": start_date.strftime("%Y-%m-%dT00:00:00Z"),
        "end": end_date.strftime("%Y-%m-%dT00:00:00Z"),
        "timeframe": "1Day",
        "limit": 10000,
        "adjustment": "split",
    }

    all_bars = []
    page_token = None

    while True:
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        bars = data.get("bars", [])
        all_bars.extend(bars)

        page_token = data.get("next_page_token")
        if not page_token:
            break

    if not all_bars:
        return None

    df = pd.DataFrame(all_bars)
    df["t"] = pd.to_datetime(df["t"])
    df = df.set_index("t")
    df.index = df.index.tz_localize(None)

    rename = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    df = df.rename(columns=rename)

    return df[["open", "high", "low", "close", "volume"]]


def load_cached_data(output_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Load previously fetched data from Parquet cache."""
    output_dir = output_dir or OUTPUT_DIR
    results = {}

    if not output_dir.exists():
        return results

    for parquet_file in output_dir.glob("*.parquet"):
        ticker = parquet_file.stem
        try:
            df = pd.read_parquet(parquet_file)
            results[ticker] = df
        except Exception as e:
            logger.warning(f"Cannot load {ticker}: {e}")

    logger.info(f"Loaded {len(results)} tickers from cache")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch midcap historical data")
    parser.add_argument("--years", type=int, default=3, help="Years of history")
    parser.add_argument("--output", type=str, default=None, help="Output directory")
    args = parser.parse_args()

    output = Path(args.output) if args.output else None
    fetch_all_tickers(years=args.years, output_dir=output)
