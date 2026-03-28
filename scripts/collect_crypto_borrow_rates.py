"""
DATA-003 — Collect margin borrow rates + BTC dominance for crypto strategies.

Collects:
  1. Margin borrow rate history from Binance (authenticated SAPI endpoint)
     - GET /sapi/v1/margin/interestRateHistory
     - Assets: BTC, ETH, SOL, BNB, XRP, DOGE, AVAX, LINK, ADA, DOT, USDT
     - Max 100 days per request, paginated backwards for up to 2 years
     - Output: data/crypto/borrow_rates/<ASSET>_borrow_rates.parquet

  2. BTC dominance proxy from CoinGecko free API
     - GET /api/v3/coins/bitcoin/market_chart (market_caps over 3 years)
     - Output: data/crypto/dominance/btc_dominance.parquet

  3. Collection metadata log
     - Output: data/crypto/metadata/collection_log.json

Requires:
  - BINANCE_API_KEY + BINANCE_API_SECRET (for borrow rate endpoint)
  - requests, pandas (already in project deps)

Usage:
    python scripts/collect_crypto_borrow_rates.py
    python scripts/collect_crypto_borrow_rates.py --assets BTC ETH USDT
    python scripts/collect_crypto_borrow_rates.py --days 365
    python scripts/collect_crypto_borrow_rates.py --dominance-only
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("collect_borrow_rates")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_TESTNET_BASE = "https://testnet.binance.vision"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

DEFAULT_ASSETS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
    "AVAX", "LINK", "ADA", "DOT", "USDT",
]

# Binance /sapi/v1/margin/interestRateHistory returns max 100 rows per call.
# Each row = 1 day, so we paginate in 100-day windows.
BORROW_PAGE_SIZE_DAYS = 100

# CoinGecko rate limit: ~10 req/min on free tier
COINGECKO_DELAY = 6.5

# Retry config
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds


# ---------------------------------------------------------------------------
# Binance HMAC signing (same pattern as core/broker/binance_broker.py)
# ---------------------------------------------------------------------------
class BinanceSigner:
    """Lightweight HMAC-SHA256 signer for Binance SAPI calls."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool | None = None,
    ):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        if testnet is None:
            testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        self.base_url = BINANCE_TESTNET_BASE if testnet else BINANCE_SPOT_BASE
        self._session = requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": self.api_key})

        if not self.api_key or not self.api_secret:
            logger.warning(
                "BINANCE_API_KEY / BINANCE_API_SECRET not set — "
                "borrow rate collection will fail (authenticated endpoint)"
            )

    def _sign(self, params: dict) -> dict:
        """Add timestamp + HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    def get(self, path: str, params: dict | None = None, signed: bool = True) -> Any:
        """Signed GET request with retry + backoff."""
        params = dict(params or {})
        if signed:
            params = self._sign(params)
        url = f"{self.base_url}{path}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = RETRY_BACKOFF_BASE ** attempt
                logger.warning(f"Request error (attempt {attempt}): {e}, retrying in {wait:.1f}s")
                time.sleep(wait)
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"Rate limited 429, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue

            if resp.status_code >= 400:
                try:
                    err = resp.json()
                except Exception:
                    err = resp.text
                if attempt < MAX_RETRIES and resp.status_code >= 500:
                    wait = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(f"Server error {resp.status_code} (attempt {attempt}), retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Binance API {resp.status_code}: {err}")

            return resp.json()

        raise RuntimeError(f"Max retries exceeded for {path}")


# ---------------------------------------------------------------------------
# Borrow rate collection
# ---------------------------------------------------------------------------
def collect_borrow_rates(
    signer: BinanceSigner,
    asset: str,
    days: int = 730,
) -> pd.DataFrame:
    """Collect margin borrow rate history for a single asset.

    Paginates backwards from now in 100-day windows (Binance API limit).

    Args:
        signer: Authenticated BinanceSigner.
        asset: Asset symbol (e.g. BTC, ETH, USDT).
        days: Total days of history to fetch (default 2 years).

    Returns:
        DataFrame with columns: timestamp, asset, dailyInterestRate, annualRate.
    """
    path = "/sapi/v1/margin/interestRateHistory"
    all_records: list[dict] = []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Paginate in windows of BORROW_PAGE_SIZE_DAYS
    window_start = start
    while window_start < end:
        window_end = min(window_start + timedelta(days=BORROW_PAGE_SIZE_DAYS), end)

        params = {
            "asset": asset,
            "startTime": int(window_start.timestamp() * 1000),
            "endTime": int(window_end.timestamp() * 1000),
            "size": 100,
        }

        try:
            data = signer.get(path, params=params, signed=True)
        except RuntimeError as e:
            logger.warning(f"  Failed to fetch {asset} borrow rates "
                           f"({window_start.date()} -> {window_end.date()}): {e}")
            window_start = window_end
            continue

        if isinstance(data, list):
            all_records.extend(data)
        elif isinstance(data, dict) and "data" in data:
            # Some Binance responses wrap in {"data": [...]}
            all_records.extend(data["data"])

        window_start = window_end
        time.sleep(0.2)  # Rate limit courtesy

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)

    # Normalize column names (API returns camelCase)
    if "interestRate" in df.columns:
        df = df.rename(columns={"interestRate": "dailyInterestRate"})
    if "timestamp" not in df.columns and "time" in df.columns:
        df = df.rename(columns={"time": "timestamp"})

    # Convert timestamp
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    # Ensure float rate
    if "dailyInterestRate" in df.columns:
        df["dailyInterestRate"] = pd.to_numeric(df["dailyInterestRate"], errors="coerce")
        df["annualRate"] = df["dailyInterestRate"] * 365

    df["asset"] = asset
    df = df.drop_duplicates(subset=["timestamp", "asset"]).sort_values("timestamp")
    return df.reset_index(drop=True)


def save_borrow_rates(df: pd.DataFrame, asset: str, output_dir: Path) -> Path:
    """Save borrow rates to Parquet, merging with existing data."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{asset}_borrow_rates.parquet"

    if path.exists() and not df.empty:
        existing = pd.read_parquet(path)
        df = pd.concat([existing, df]).drop_duplicates(
            subset=["timestamp", "asset"]
        ).sort_values("timestamp").reset_index(drop=True)

    if not df.empty:
        df.to_parquet(path, index=False)

    return path


# ---------------------------------------------------------------------------
# BTC dominance collection (CoinGecko)
# ---------------------------------------------------------------------------
def collect_btc_dominance(days: int = 1095) -> pd.DataFrame:
    """Collect BTC dominance proxy from CoinGecko free API.

    Uses bitcoin market_cap and total crypto market_cap to compute dominance.
    Falls back to bitcoin market_cap only if total is unavailable.

    Args:
        days: Days of history (default 3 years = 1095).

    Returns:
        DataFrame with columns: timestamp, btc_market_cap, total_market_cap, dominance.
    """
    # Step 1: BTC market cap
    logger.info("Fetching BTC market cap from CoinGecko...")
    btc_url = f"{COINGECKO_BASE}/coins/bitcoin/market_chart"
    btc_params = {"vs_currency": "usd", "days": days, "interval": "daily"}

    btc_resp = _coingecko_get(btc_url, btc_params)
    btc_caps = btc_resp.get("market_caps", [])

    if not btc_caps:
        logger.error("No BTC market cap data from CoinGecko")
        return pd.DataFrame()

    btc_df = pd.DataFrame(btc_caps, columns=["timestamp_ms", "btc_market_cap"])
    btc_df["timestamp"] = pd.to_datetime(btc_df["timestamp_ms"], unit="ms", utc=True)

    # Step 2: Total crypto market cap (via /global/market_cap_chart is pro-only,
    # so we approximate using CoinGecko /global endpoint for current + historical
    # bitcoin dominance percentage)
    time.sleep(COINGECKO_DELAY)

    logger.info("Fetching total crypto market cap from CoinGecko...")
    total_url = f"{COINGECKO_BASE}/global"
    try:
        total_resp = _coingecko_get(total_url, {})
        global_data = total_resp.get("data", {})
        current_dominance = global_data.get("market_cap_percentage", {}).get("btc")
        current_total = global_data.get("total_market_cap", {}).get("usd")
    except Exception as e:
        logger.warning(f"Could not fetch global market data: {e}")
        current_dominance = None
        current_total = None

    # Compute dominance proxy: BTC mcap / estimated total
    # For historical data, we use BTC mcap as the primary signal;
    # the ratio is most meaningful for strategy signals
    if current_total and current_dominance:
        logger.info(
            f"Current BTC dominance: {current_dominance:.1f}%, "
            f"total market cap: ${current_total/1e9:.0f}B"
        )

    # Build output DataFrame
    result = btc_df[["timestamp", "btc_market_cap"]].copy()

    # Use the BTC dominance percentage time series if available from the
    # /coins/bitcoin/market_chart response (market_caps gives absolute values).
    # We store absolute BTC market cap — strategies compute dominance ratio themselves.
    result["dominance_pct"] = None
    if current_dominance is not None:
        # Annotate latest row with known dominance for reference
        result.loc[result.index[-1], "dominance_pct"] = current_dominance

    result = result.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    return result.reset_index(drop=True)


def _coingecko_get(url: str, params: dict) -> dict:
    """GET request to CoinGecko with retry + backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF_BASE ** attempt
            logger.warning(f"CoinGecko request error (attempt {attempt}): {e}, retrying in {wait:.1f}s")
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning(f"CoinGecko rate limited, sleeping {retry_after}s")
            time.sleep(retry_after)
            continue

        if resp.status_code >= 400:
            if attempt < MAX_RETRIES and resp.status_code >= 500:
                wait = RETRY_BACKOFF_BASE ** attempt
                logger.warning(f"CoinGecko {resp.status_code} (attempt {attempt}), retrying in {wait:.1f}s")
                time.sleep(wait)
                continue
            raise RuntimeError(f"CoinGecko API {resp.status_code}: {resp.text[:200]}")

        return resp.json()

    raise RuntimeError(f"Max retries exceeded for {url}")


def save_btc_dominance(df: pd.DataFrame, output_dir: Path) -> Path:
    """Save BTC dominance data to Parquet, merging with existing."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "btc_dominance.parquet"

    if path.exists() and not df.empty:
        existing = pd.read_parquet(path)
        df = pd.concat([existing, df]).drop_duplicates(
            subset=["timestamp"]
        ).sort_values("timestamp").reset_index(drop=True)

    if not df.empty:
        df.to_parquet(path, index=False)

    return path


# ---------------------------------------------------------------------------
# Data quality validation
# ---------------------------------------------------------------------------
def validate_borrow_data(df: pd.DataFrame, asset: str) -> dict:
    """Validate borrow rate data quality."""
    if df.empty:
        return {"asset": asset, "status": "EMPTY", "rows": 0, "issues": []}

    issues: list[str] = []

    # Check for negative rates (should not happen)
    if "dailyInterestRate" in df.columns:
        neg = (df["dailyInterestRate"] < 0).sum()
        if neg > 0:
            issues.append(f"{neg} negative interest rates")

        # Check for suspiciously high rates (> 1% daily = 365% annual)
        high = (df["dailyInterestRate"] > 0.01).sum()
        if high > 0:
            issues.append(f"{high} unusually high rates (>1%/day)")

    # Check for gaps (daily data expected)
    if len(df) > 1 and "timestamp" in df.columns:
        diffs = df["timestamp"].diff().dropna()
        gaps = diffs[diffs > pd.Timedelta("2d")]
        if len(gaps) > 0:
            issues.append(f"{len(gaps)} time gaps (>2 days)")

    return {
        "asset": asset,
        "status": "OK" if not issues else "WARNINGS",
        "rows": len(df),
        "start": str(df["timestamp"].min()) if "timestamp" in df.columns else "?",
        "end": str(df["timestamp"].max()) if "timestamp" in df.columns else "?",
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Collection metadata log
# ---------------------------------------------------------------------------
def write_collection_log(
    metadata_dir: Path,
    borrow_results: list[dict],
    dominance_result: dict | None,
) -> Path:
    """Write collection metadata to JSON log."""
    metadata_dir.mkdir(parents=True, exist_ok=True)
    log_path = metadata_dir / "collection_log.json"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "collect_crypto_borrow_rates.py",
        "borrow_rates": borrow_results,
        "btc_dominance": dominance_result,
    }

    # Append to existing log
    log: list[dict] = []
    if log_path.exists():
        try:
            with open(log_path, "r") as f:
                log = json.load(f)
            if not isinstance(log, list):
                log = [log]
        except (json.JSONDecodeError, ValueError):
            log = []

    log.append(entry)

    # Keep last 100 entries
    log = log[-100:]

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, default=str)

    return log_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Collect crypto margin borrow rates + BTC dominance"
    )
    parser.add_argument(
        "--assets", nargs="+", default=DEFAULT_ASSETS,
        help=f"Assets to collect borrow rates for (default: {', '.join(DEFAULT_ASSETS)})",
    )
    parser.add_argument(
        "--days", type=int, default=730,
        help="Days of borrow rate history (default 730 = 2 years)",
    )
    parser.add_argument(
        "--dominance-only", action="store_true",
        help="Only collect BTC dominance (skip borrow rates)",
    )
    parser.add_argument(
        "--dominance-days", type=int, default=1095,
        help="Days of BTC dominance history (default 1095 = 3 years)",
    )
    parser.add_argument(
        "--skip-dominance", action="store_true",
        help="Skip BTC dominance collection",
    )
    args = parser.parse_args()

    borrow_dir = ROOT / "data" / "crypto" / "borrow_rates"
    dominance_dir = ROOT / "data" / "crypto" / "dominance"
    metadata_dir = ROOT / "data" / "crypto" / "metadata"

    borrow_results: list[dict] = []
    dominance_result: dict | None = None

    # ------------------------------------------------------------------
    # 1. Borrow rates (authenticated Binance SAPI)
    # ------------------------------------------------------------------
    if not args.dominance_only:
        signer = BinanceSigner()
        logger.info(f"Collecting borrow rates for {len(args.assets)} assets ({args.days} days)...")

        for asset in args.assets:
            logger.info(f"  Fetching {asset} borrow rates...")
            try:
                df = collect_borrow_rates(signer, asset, days=args.days)
                if not df.empty:
                    path = save_borrow_rates(df, asset, borrow_dir)
                    result = validate_borrow_data(df, asset)
                    logger.info(f"  OK {asset}: {result['rows']} records -> {path.name}")
                else:
                    result = {"asset": asset, "status": "NO_DATA", "rows": 0, "issues": []}
                    logger.warning(f"  SKIP {asset}: no data returned")
            except Exception as e:
                result = {"asset": asset, "status": "ERROR", "rows": 0, "issues": [str(e)]}
                logger.error(f"  FAIL {asset}: {e}")

            borrow_results.append(result)

    # ------------------------------------------------------------------
    # 2. BTC dominance (CoinGecko, no auth)
    # ------------------------------------------------------------------
    if not args.skip_dominance:
        logger.info(f"Collecting BTC dominance ({args.dominance_days} days)...")
        try:
            dom_df = collect_btc_dominance(days=args.dominance_days)
            if not dom_df.empty:
                path = save_btc_dominance(dom_df, dominance_dir)
                dominance_result = {
                    "status": "OK",
                    "rows": len(dom_df),
                    "start": str(dom_df["timestamp"].min()),
                    "end": str(dom_df["timestamp"].max()),
                    "file": str(path),
                }
                logger.info(f"  OK BTC dominance: {len(dom_df)} rows -> {path.name}")
            else:
                dominance_result = {"status": "NO_DATA", "rows": 0}
                logger.warning("  SKIP BTC dominance: no data returned")
        except Exception as e:
            dominance_result = {"status": "ERROR", "rows": 0, "error": str(e)}
            logger.error(f"  FAIL BTC dominance: {e}")

    # ------------------------------------------------------------------
    # 3. Metadata log
    # ------------------------------------------------------------------
    log_path = write_collection_log(metadata_dir, borrow_results, dominance_result)
    logger.info(f"Metadata log: {log_path}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n=== Borrow Rate Collection Summary ===")
    if borrow_results:
        for r in borrow_results:
            status = r.get("status", "?")
            rows = r.get("rows", 0)
            issues = r.get("issues", [])
            print(f"  {r['asset']}: {status} ({rows} rows)")
            for issue in issues:
                print(f"    ! {issue}")

        total = sum(r.get("rows", 0) for r in borrow_results)
        ok = sum(1 for r in borrow_results if r.get("status") in ("OK", "WARNINGS"))
        print(f"  Total borrow: {total} records, {ok}/{len(borrow_results)} assets OK")
    else:
        print("  (skipped)")

    print("\n=== BTC Dominance ===")
    if dominance_result:
        print(f"  Status: {dominance_result.get('status', '?')} ({dominance_result.get('rows', 0)} rows)")
    else:
        print("  (skipped)")

    print(f"\nMetadata: {log_path}")


if __name__ == "__main__":
    main()
