"""
DATA-002 — Collect historical crypto data from Binance.

Downloads OHLCV candles + funding rates for all symbols in the universe.
Stores in data/crypto/ as Parquet (candles) and SQLite (funding).

Usage:
    python scripts/collect_crypto_history.py
    python scripts/collect_crypto_history.py --symbols BTCUSDT ETHUSDT
    python scripts/collect_crypto_history.py --interval 5m --days 365
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("collect_crypto")

# Binance public API (no key needed for market data)
FUTURES_BASE = "https://fapi.binance.com"
MAX_KLINES = 1000


def fetch_klines(
    symbol: str, interval: str, start_ms: int, end_ms: int
) -> list[list]:
    """Fetch klines from Binance futures API."""
    url = f"{FUTURES_BASE}/fapi/v1/klines"
    all_klines = []
    current = start_ms

    while current < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "endTime": end_ms,
            "limit": MAX_KLINES,
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", 60))
            logger.warning(f"Rate limited, sleeping {retry}s")
            time.sleep(retry)
            continue

        resp.raise_for_status()
        data = resp.json()
        if not data:
            break

        all_klines.extend(data)
        last_time = data[-1][0]
        if last_time <= current:
            break
        current = last_time + 1
        time.sleep(0.1)  # Respect rate limits

    return all_klines


def klines_to_df(klines: list[list]) -> pd.DataFrame:
    """Convert raw klines to clean DataFrame."""
    if not klines:
        return pd.DataFrame()

    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore",
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df = df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]]
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    return df.reset_index(drop=True)


def fetch_funding_rates(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch historical funding rates."""
    url = f"{FUTURES_BASE}/fapi/v1/fundingRate"
    all_rates = []
    current = start_ms

    while current < end_ms:
        params = {
            "symbol": symbol,
            "startTime": current,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break

        all_rates.extend(data)
        last_time = data[-1]["fundingTime"]
        if last_time <= current:
            break
        current = last_time + 1
        time.sleep(0.1)

    return all_rates


def validate_data(df: pd.DataFrame, symbol: str) -> dict:
    """Validate collected data quality."""
    if df.empty:
        return {"symbol": symbol, "status": "EMPTY", "rows": 0}

    issues = []

    # Check for gaps
    if len(df) > 1:
        diffs = df["timestamp"].diff().dropna()
        expected = diffs.mode().iloc[0] if len(diffs) > 0 else pd.Timedelta("1h")
        gaps = diffs[diffs > expected * 2]
        if len(gaps) > 0:
            issues.append(f"{len(gaps)} time gaps detected")

    # Check for zero volume
    zero_vol = (df["volume"] == 0).sum()
    if zero_vol > 0:
        issues.append(f"{zero_vol} zero-volume candles")

    # Check OHLC consistency
    bad_ohlc = (
        (df["high"] < df["open"]) | (df["high"] < df["close"])
        | (df["low"] > df["open"]) | (df["low"] > df["close"])
    ).sum()
    if bad_ohlc > 0:
        issues.append(f"{bad_ohlc} invalid OHLC candles")

    # Flash crash detection
    mid = (df["open"] + df["close"]) / 2
    wick_pct = (df["high"] - df["low"]) / mid * 100
    flash_crashes = (wick_pct > 10).sum()
    if flash_crashes > 0:
        issues.append(f"{flash_crashes} flash crash candles (>10% wick)")

    return {
        "symbol": symbol,
        "status": "OK" if not issues else "WARNINGS",
        "rows": len(df),
        "start": str(df["timestamp"].min()),
        "end": str(df["timestamp"].max()),
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description="Collect crypto historical data")
    parser.add_argument(
        "--symbols", nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                 "AVAXUSDT", "DOTUSDT", "LINKUSDT", "ADAUSDT"],
    )
    parser.add_argument("--interval", default="1h", help="Candle interval")
    parser.add_argument("--days", type=int, default=1095, help="Days of history (default 3y)")
    parser.add_argument("--funding", action="store_true", default=True, help="Also collect funding")
    parser.add_argument("--validate-only", action="store_true", help="Only validate existing data")
    args = parser.parse_args()

    data_dir = ROOT / "data" / "crypto" / "candles"
    funding_dir = ROOT / "data" / "crypto" / "funding"
    data_dir.mkdir(parents=True, exist_ok=True)
    funding_dir.mkdir(parents=True, exist_ok=True)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    results = []

    for symbol in args.symbols:
        logger.info(f"{'Validating' if args.validate_only else 'Collecting'} {symbol} {args.interval}...")

        parquet_path = data_dir / f"{symbol}_{args.interval}.parquet"

        if args.validate_only:
            if parquet_path.exists():
                df = pd.read_parquet(parquet_path)
                result = validate_data(df, symbol)
            else:
                result = {"symbol": symbol, "status": "MISSING", "rows": 0}
            results.append(result)
            continue

        # Fetch candles
        try:
            klines = fetch_klines(symbol, args.interval, start_ms, end_ms)
            df = klines_to_df(klines)

            if not df.empty:
                # Merge with existing
                if parquet_path.exists():
                    existing = pd.read_parquet(parquet_path)
                    df = pd.concat([existing, df]).drop_duplicates(
                        subset=["timestamp"]
                    ).sort_values("timestamp")

                df.to_parquet(parquet_path, index=False)
                result = validate_data(df, symbol)
                logger.info(f"  ✓ {symbol}: {result['rows']} candles saved")
            else:
                result = {"symbol": symbol, "status": "NO_DATA", "rows": 0}
                logger.warning(f"  ✗ {symbol}: no data returned")

        except Exception as e:
            result = {"symbol": symbol, "status": "ERROR", "error": str(e)}
            logger.error(f"  ✗ {symbol}: {e}")

        results.append(result)

        # Fetch funding rates
        if args.funding:
            try:
                rates = fetch_funding_rates(symbol, start_ms, end_ms)
                if rates:
                    fr_df = pd.DataFrame(rates)
                    fr_df["fundingTime"] = pd.to_datetime(
                        fr_df["fundingTime"], unit="ms", utc=True
                    )
                    fr_df["fundingRate"] = fr_df["fundingRate"].astype(float)
                    fr_path = funding_dir / f"funding_{symbol}.parquet"

                    if fr_path.exists():
                        existing = pd.read_parquet(fr_path)
                        fr_df = pd.concat([existing, fr_df]).drop_duplicates(
                            subset=["fundingTime"]
                        ).sort_values("fundingTime")

                    fr_df.to_parquet(fr_path, index=False)
                    logger.info(f"  ✓ {symbol}: {len(fr_df)} funding rates saved")
            except Exception as e:
                logger.warning(f"  Funding rates failed for {symbol}: {e}")

    # Summary
    print("\n=== Collection Summary ===")
    for r in results:
        status = r.get("status", "?")
        rows = r.get("rows", 0)
        issues = r.get("issues", [])
        print(f"  {r['symbol']}: {status} ({rows} rows)")
        for issue in issues:
            print(f"    ⚠ {issue}")

    total = sum(r.get("rows", 0) for r in results)
    ok = sum(1 for r in results if r.get("status") in ("OK", "WARNINGS"))
    print(f"\nTotal: {total} candles, {ok}/{len(results)} symbols OK")


if __name__ == "__main__":
    main()
