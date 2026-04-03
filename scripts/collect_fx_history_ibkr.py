#!/usr/bin/env python3
"""
Collect historical FX OHLCV data from IB Gateway.

Downloads 1H (2Y), 4H (aggregated from 1H), and 1D (5Y) bars
for 8 FX pairs. Saves as Parquet files.

Usage:
    python scripts/collect_fx_history_ibkr.py
"""

import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from ib_insync import IB, Forex, util

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
IB_HOST = "127.0.0.1"
IB_PORT = 4002
CLIENT_ID = 10

FX_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "EURGBP",
    "EURJPY", "AUDJPY", "USDCHF", "NZDUSD",
]

DATA_DIR = Path("/opt/trading-platform/data/fx")

# IB rate limit: max 60 hist data requests per 10 min
# We sleep between requests to stay safe
SLEEP_BETWEEN_REQUESTS = 2.5  # seconds
REQUEST_COUNT = 0
REQUEST_WINDOW_START = time.time()
MAX_REQUESTS_PER_WINDOW = 55  # conservative, below 60
WINDOW_SECONDS = 600  # 10 minutes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate-limit helper
# ---------------------------------------------------------------------------
def rate_limit_guard():
    """Enforce IB's 60 requests / 10 min limit."""
    global REQUEST_COUNT, REQUEST_WINDOW_START

    REQUEST_COUNT += 1
    elapsed = time.time() - REQUEST_WINDOW_START

    if elapsed > WINDOW_SECONDS:
        # Reset window
        REQUEST_COUNT = 1
        REQUEST_WINDOW_START = time.time()
        return

    if REQUEST_COUNT >= MAX_REQUESTS_PER_WINDOW:
        wait = WINDOW_SECONDS - elapsed + 5  # extra 5s safety
        log.warning(
            f"Rate limit approaching ({REQUEST_COUNT} requests in {elapsed:.0f}s). "
            f"Sleeping {wait:.0f}s..."
        )
        time.sleep(wait)
        REQUEST_COUNT = 1
        REQUEST_WINDOW_START = time.time()


# ---------------------------------------------------------------------------
# IB historical data helpers
# ---------------------------------------------------------------------------
def request_historical(
    ib: IB,
    contract,
    duration: str,
    bar_size: str,
    what_to_show: str = "MIDPOINT",
) -> pd.DataFrame:
    """Request historical data from IB, return as DataFrame."""

    rate_limit_guard()

    log.info(
        f"  Requesting {contract.symbol}{contract.currency} "
        f"duration={duration} barSize={bar_size}"
    )

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",  # up to now
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=False,  # 24h for FX
            formatDate=1,
            timeout=120,
        )
    except Exception as e:
        log.error(f"  IB error: {e}")
        return pd.DataFrame()

    if not bars:
        log.warning("  No bars returned")
        return pd.DataFrame()

    df = util.df(bars)
    log.info(f"  Got {len(df)} bars")

    # Standardize columns
    df = df.rename(columns={
        "date": "datetime",
    })

    # Keep only OHLCV columns
    cols_keep = ["datetime", "open", "high", "low", "close", "volume"]
    cols_available = [c for c in cols_keep if c in df.columns]
    df = df[cols_available].copy()

    # Ensure datetime is proper type
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])

    return df


def request_1h_chunked(ib: IB, contract, years: int = 2) -> pd.DataFrame:
    """
    IB may not serve 2Y of 1H bars in one shot (max ~1Y per request for
    some combinations). We chunk into 6-month blocks working backwards.
    """
    all_dfs = []
    end_dt = datetime.now(UTC).replace(tzinfo=None)
    chunk_months = 6
    total_months = years * 12

    months_done = 0
    while months_done < total_months:
        remaining = total_months - months_done
        dur_months = min(chunk_months, remaining)
        duration_str = f"{dur_months} M"

        end_str = end_dt.strftime("%Y%m%d-%H:%M:%S")

        rate_limit_guard()

        log.info(
            f"  Chunk: endDateTime={end_str} duration={duration_str} barSize=1 hour"
        )

        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=duration_str,
                barSizeSetting="1 hour",
                whatToShow="MIDPOINT",
                useRTH=False,
                formatDate=1,
                timeout=120,
            )
        except Exception as e:
            log.error(f"  IB error on chunk: {e}")
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            months_done += dur_months
            end_dt -= timedelta(days=dur_months * 30)
            continue

        if not bars:
            log.warning(f"  No bars for chunk ending {end_str}")
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            months_done += dur_months
            end_dt -= timedelta(days=dur_months * 30)
            continue

        df_chunk = util.df(bars)
        df_chunk = df_chunk.rename(columns={"date": "datetime"})
        df_chunk["datetime"] = pd.to_datetime(df_chunk["datetime"])
        all_dfs.append(df_chunk)

        # Move end_dt to just before the earliest bar in this chunk
        earliest = df_chunk["datetime"].min()
        end_dt = earliest - timedelta(seconds=1)

        months_done += dur_months
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not all_dfs:
        return pd.DataFrame()

    df = pd.concat(all_dfs, ignore_index=True)
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    cols_keep = ["datetime", "open", "high", "low", "close", "volume"]
    cols_available = [c for c in cols_keep if c in df.columns]
    df = df[cols_available].copy()

    log.info(f"  Total 1H bars after chunking: {len(df)}")
    return df


def aggregate_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1H bars to 4H bars."""
    if df_1h.empty:
        return pd.DataFrame()

    df = df_1h.copy()
    df = df.set_index("datetime")

    # Resample to 4H
    df_4h = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum" if "volume" in df.columns else "first",
    }).dropna(subset=["open"])

    df_4h = df_4h.reset_index()
    log.info(f"  Aggregated to {len(df_4h)} 4H bars")
    return df_4h


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_ohlc(df: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    """Validate OHLC consistency and gaps."""
    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": len(df),
        "start": None,
        "end": None,
        "ohlc_errors": 0,
        "gap_warnings": 0,
        "status": "OK",
    }

    if df.empty:
        result["status"] = "EMPTY"
        return result

    result["start"] = df["datetime"].iloc[0]
    result["end"] = df["datetime"].iloc[-1]

    # OHLC consistency: high >= max(open, close), low <= min(open, close)
    ohlc_high_ok = df["high"] >= df[["open", "close"]].max(axis=1) - 1e-10
    ohlc_low_ok = df["low"] <= df[["open", "close"]].min(axis=1) + 1e-10
    ohlc_errors = (~ohlc_high_ok).sum() + (~ohlc_low_ok).sum()
    result["ohlc_errors"] = int(ohlc_errors)

    # Gap detection (skip weekends for FX)
    if len(df) > 1:
        diffs = df["datetime"].diff().dropna()

        if timeframe == "1H":
            expected_gap = timedelta(hours=1)
            max_gap = timedelta(hours=2)  # allow 2h before flagging
        elif timeframe == "4H":
            expected_gap = timedelta(hours=4)
            max_gap = timedelta(hours=8)
        elif timeframe == "1D":
            expected_gap = timedelta(days=1)
            max_gap = timedelta(days=3)  # allow weekends (Fri->Mon)
        else:
            expected_gap = timedelta(hours=1)
            max_gap = timedelta(hours=2)

        # For FX, weekends create ~48h gaps (Fri 17:00 ET -> Sun 17:00 ET)
        weekend_gap = timedelta(hours=54)  # generous weekend allowance

        gaps = diffs[diffs > max_gap]
        non_weekend_gaps = gaps[gaps < weekend_gap]
        result["gap_warnings"] = len(non_weekend_gaps)

    if ohlc_errors > 0:
        result["status"] = "OHLC_ERRORS"
    elif result["gap_warnings"] > 5:
        result["status"] = "GAPS"

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 70)
    log.info("FX Historical Data Collection — IB Gateway")
    log.info("=" * 70)

    # Create output directory
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Output directory: {DATA_DIR}")

    # Connect to IB
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=30)
        log.info(f"Connected to IB Gateway at {IB_HOST}:{IB_PORT} (clientId={CLIENT_ID})")
    except Exception as e:
        log.error(f"Failed to connect to IB Gateway: {e}")
        log.error("Make sure IB Gateway is running on port 4002")
        sys.exit(1)

    validation_results = []
    files_saved = []

    for pair in FX_PAIRS:
        log.info(f"\n{'='*50}")
        log.info(f"Processing {pair}")
        log.info(f"{'='*50}")

        contract = Forex(pair)

        # --- 1D bars, 5 years ---
        log.info(f"\n[{pair}] Downloading 1D bars (5Y)...")
        try:
            df_1d = request_historical(ib, contract, "5 Y", "1 day")
        except Exception as e:
            log.error(f"  Failed: {e}")
            df_1d = pd.DataFrame()

        if not df_1d.empty:
            fpath = DATA_DIR / f"{pair}_1D.parquet"
            df_1d.to_parquet(fpath, index=False)
            files_saved.append(str(fpath))
            log.info(f"  Saved {fpath} ({len(df_1d)} bars)")
            validation_results.append(validate_ohlc(df_1d, pair, "1D"))
        else:
            validation_results.append({
                "symbol": pair, "timeframe": "1D", "candles": 0,
                "start": None, "end": None, "ohlc_errors": 0,
                "gap_warnings": 0, "status": "NO_DATA",
            })

        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # --- 1H bars, 2 years (chunked) ---
        log.info(f"\n[{pair}] Downloading 1H bars (2Y, chunked)...")
        try:
            df_1h = request_1h_chunked(ib, contract, years=2)
        except Exception as e:
            log.error(f"  Failed: {e}")
            df_1h = pd.DataFrame()

        if not df_1h.empty:
            fpath = DATA_DIR / f"{pair}_1H.parquet"
            df_1h.to_parquet(fpath, index=False)
            files_saved.append(str(fpath))
            log.info(f"  Saved {fpath} ({len(df_1h)} bars)")
            validation_results.append(validate_ohlc(df_1h, pair, "1H"))
        else:
            validation_results.append({
                "symbol": pair, "timeframe": "1H", "candles": 0,
                "start": None, "end": None, "ohlc_errors": 0,
                "gap_warnings": 0, "status": "NO_DATA",
            })

        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # --- 4H bars, aggregated from 1H ---
        log.info(f"\n[{pair}] Aggregating 4H bars from 1H data...")
        if not df_1h.empty:
            df_4h = aggregate_to_4h(df_1h)
            if not df_4h.empty:
                fpath = DATA_DIR / f"{pair}_4H.parquet"
                df_4h.to_parquet(fpath, index=False)
                files_saved.append(str(fpath))
                log.info(f"  Saved {fpath} ({len(df_4h)} bars)")
                validation_results.append(validate_ohlc(df_4h, pair, "4H"))
            else:
                validation_results.append({
                    "symbol": pair, "timeframe": "4H", "candles": 0,
                    "start": None, "end": None, "ohlc_errors": 0,
                    "gap_warnings": 0, "status": "NO_DATA",
                })
        else:
            log.warning("  Skipping 4H — no 1H data available")
            validation_results.append({
                "symbol": pair, "timeframe": "4H", "candles": 0,
                "start": None, "end": None, "ohlc_errors": 0,
                "gap_warnings": 0, "status": "SKIPPED",
            })

    # Disconnect
    ib.disconnect()
    log.info("\nDisconnected from IB Gateway")

    # --- Summary ---
    log.info("\n" + "=" * 90)
    log.info("VALIDATION SUMMARY")
    log.info("=" * 90)
    log.info(
        f"{'Symbol':<10} {'TF':<5} {'Candles':>8} {'Start':<22} "
        f"{'End':<22} {'OHLC Err':>9} {'Gaps':>5} {'Status':<12}"
    )
    log.info("-" * 90)

    for r in validation_results:
        start_str = str(r["start"])[:19] if r["start"] else "N/A"
        end_str = str(r["end"])[:19] if r["end"] else "N/A"
        log.info(
            f"{r['symbol']:<10} {r['timeframe']:<5} {r['candles']:>8} "
            f"{start_str:<22} {end_str:<22} "
            f"{r['ohlc_errors']:>9} {r['gap_warnings']:>5} {r['status']:<12}"
        )

    log.info("-" * 90)
    total_files = len(files_saved)
    total_candles = sum(r["candles"] for r in validation_results)
    total_errors = sum(r["ohlc_errors"] for r in validation_results)
    log.info(f"Total: {total_files} files, {total_candles} candles, {total_errors} OHLC errors")
    log.info(f"\nFiles saved in: {DATA_DIR}")

    if total_errors > 0:
        log.warning(f"WARNING: {total_errors} OHLC consistency errors found!")
    if total_candles == 0:
        log.error("NO DATA COLLECTED — check IB Gateway connection and market data subscriptions")
        sys.exit(1)

    log.info("\nDone.")


if __name__ == "__main__":
    main()
