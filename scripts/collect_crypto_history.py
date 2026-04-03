"""
DATA-002 — Collect historical crypto data from Binance.

Downloads OHLCV candles + funding rates for all symbols in the universe.
Supports SPOT API (api.binance.com, default for France) and FUTURES API
(fapi.binance.com, for funding rates / reference data).

Tier-based multi-timeframe collection:
  - Tier 1 (BTC, ETH): 1h, 4h, 1d from Jan 2023
  - Tier 2 (SOL, BNB, XRP, DOGE, AVAX, LINK, ADA, DOT, NEAR, SUI): 4h, 1d from Jan 2024

Stores in data/crypto/ as Parquet (candles) and Parquet (funding).

Usage:
    python scripts/collect_crypto_history.py
    python scripts/collect_crypto_history.py --source spot
    python scripts/collect_crypto_history.py --source futures --symbols BTCUSDT ETHUSDT
    python scripts/collect_crypto_history.py --interval 1h --days 365
    python scripts/collect_crypto_history.py --tier-mode
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
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

# Binance public APIs (no key needed for market data)
SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
MAX_KLINES = 1000

# Tier config for multi-timeframe collection
TIER_1_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TIER_2_SYMBOLS = [
    "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "ADAUSDT", "DOTUSDT",
    "NEARUSDT", "SUIUSDT",
]
ALL_SYMBOLS = TIER_1_SYMBOLS + TIER_2_SYMBOLS

TIER_CONFIG: dict[str, dict] = {
    "tier1": {
        "symbols": TIER_1_SYMBOLS,
        "intervals": ["1h", "4h", "1d"],
        "start": datetime(2023, 1, 1, tzinfo=UTC),
    },
    "tier2": {
        "symbols": TIER_2_SYMBOLS,
        "intervals": ["4h", "1d"],
        "start": datetime(2024, 1, 1, tzinfo=UTC),
    },
}


def _klines_url(source: str) -> str:
    """Return the klines endpoint URL for the given source."""
    if source == "spot":
        return f"{SPOT_BASE}/api/v3/klines"
    return f"{FUTURES_BASE}/fapi/v1/klines"


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    source: str = "spot",
) -> list[list]:
    """Fetch klines from Binance spot or futures API.

    Args:
        symbol: Trading pair (e.g. BTCUSDT).
        interval: Candle interval (1h, 4h, 1d, ...).
        start_ms: Start timestamp in milliseconds.
        end_ms: End timestamp in milliseconds.
        source: 'spot' (api.binance.com) or 'futures' (fapi.binance.com).
    """
    url = _klines_url(source)
    all_klines: list[list] = []
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


def _collect_one(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    source: str,
    data_dir: Path,
    validate_only: bool = False,
) -> dict:
    """Collect (or validate) candles for a single symbol/interval pair."""
    label = f"{symbol} {interval} ({source})"
    parquet_path = data_dir / f"{symbol}_{interval}.parquet"

    if validate_only:
        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            return validate_data(df, label)
        return {"symbol": label, "status": "MISSING", "rows": 0}

    try:
        klines = fetch_klines(symbol, interval, start_ms, end_ms, source=source)
        df = klines_to_df(klines)

        if not df.empty:
            if parquet_path.exists():
                existing = pd.read_parquet(parquet_path)
                df = pd.concat([existing, df]).drop_duplicates(
                    subset=["timestamp"]
                ).sort_values("timestamp")

            df.to_parquet(parquet_path, index=False)
            result = validate_data(df, label)
            logger.info(f"  OK {label}: {result['rows']} candles saved")
        else:
            result = {"symbol": label, "status": "NO_DATA", "rows": 0}
            logger.warning(f"  SKIP {label}: no data returned")

    except Exception as e:
        result = {"symbol": label, "status": "ERROR", "error": str(e)}
        logger.error(f"  FAIL {label}: {e}")

    return result


def _build_tier_jobs(source: str) -> list[dict]:
    """Build collection jobs from TIER_CONFIG.

    Returns a list of dicts with keys: symbol, interval, start_ms, end_ms.
    """
    end = datetime.now(UTC)
    end_ms = int(end.timestamp() * 1000)
    jobs: list[dict] = []

    for _tier_name, cfg in TIER_CONFIG.items():
        start_ms = int(cfg["start"].timestamp() * 1000)
        for sym in cfg["symbols"]:
            for iv in cfg["intervals"]:
                jobs.append({
                    "symbol": sym,
                    "interval": iv,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "source": source,
                })
    return jobs


def main():
    parser = argparse.ArgumentParser(description="Collect crypto historical data")
    parser.add_argument(
        "--symbols", nargs="+",
        default=ALL_SYMBOLS,
        help="Symbols to collect (default: all tier1+tier2)",
    )
    parser.add_argument("--interval", default="1h", help="Candle interval (ignored in --tier-mode)")
    parser.add_argument("--days", type=int, default=1095, help="Days of history (default 3y, ignored in --tier-mode)")
    parser.add_argument(
        "--source", choices=["spot", "futures"], default="spot",
        help="API source: spot (api.binance.com, default for France) or futures (fapi.binance.com)",
    )
    parser.add_argument(
        "--tier-mode", action="store_true",
        help="Tier-based multi-timeframe collection (overrides --symbols/--interval/--days)",
    )
    parser.add_argument("--funding", action="store_true", default=True, help="Also collect funding (futures only)")
    parser.add_argument("--validate-only", action="store_true", help="Only validate existing data")
    args = parser.parse_args()

    data_dir = ROOT / "data" / "crypto" / "candles"
    funding_dir = ROOT / "data" / "crypto" / "funding"
    data_dir.mkdir(parents=True, exist_ok=True)
    funding_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    if args.tier_mode:
        # ----- Tier-based multi-timeframe collection -----
        jobs = _build_tier_jobs(source=args.source)
        logger.info(f"Tier mode: {len(jobs)} jobs ({args.source} API)")

        for job in jobs:
            result = _collect_one(
                symbol=job["symbol"],
                interval=job["interval"],
                start_ms=job["start_ms"],
                end_ms=job["end_ms"],
                source=job["source"],
                data_dir=data_dir,
                validate_only=args.validate_only,
            )
            results.append(result)
    else:
        # ----- Classic single-interval mode -----
        end = datetime.now(UTC)
        start = end - timedelta(days=args.days)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        for symbol in args.symbols:
            logger.info(
                f"{'Validating' if args.validate_only else 'Collecting'} "
                f"{symbol} {args.interval} ({args.source})..."
            )

            result = _collect_one(
                symbol=symbol,
                interval=args.interval,
                start_ms=start_ms,
                end_ms=end_ms,
                source=args.source,
                data_dir=data_dir,
                validate_only=args.validate_only,
            )
            results.append(result)

            # Fetch funding rates (futures API only, not available on spot)
            if args.funding and args.source == "futures":
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
                        logger.info(f"  OK {symbol}: {len(fr_df)} funding rates saved")
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
            print(f"    ! {issue}")

    total = sum(r.get("rows", 0) for r in results)
    ok = sum(1 for r in results if r.get("status") in ("OK", "WARNINGS"))
    print(f"\nTotal: {total} candles, {ok}/{len(results)} symbols OK")


if __name__ == "__main__":
    main()
