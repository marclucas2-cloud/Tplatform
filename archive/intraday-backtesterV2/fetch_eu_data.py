"""
Fetch EU market data via IBKR TWS (paper trading, port 7497).

Connects to Interactive Brokers TWS, downloads historical data for
European stocks, indices, and ETFs, then saves to parquet in data_cache/eu/.

Usage:
    python fetch_eu_data.py              # Fetch all EU data
    python fetch_eu_data.py --daily-only # Daily bars only
    python fetch_eu_data.py --intraday-only # Intraday 15M bars only

Rate limits: 10s sleep between each historical data request to avoid
IBKR pacing violations.
"""
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import sys
import time
import json
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

from ib_insync import IB, Stock, Index, util

# ── Paths ──
SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_DIR = SCRIPT_DIR / "data_cache" / "eu"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── IBKR Connection ──
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497       # TWS paper trading
IBKR_CLIENT_ID = 2     # Avoid conflict with main worker (clientId=1)

# ── EU Universe ──

# Actions FR (Euronext Paris — SMART routing for data)
FR_STOCKS = {
    "MC":  Stock("MC", "SMART", "EUR"),     # LVMH
    "TTE": Stock("TTE", "SMART", "EUR"),    # TotalEnergies
    "BNP": Stock("BNP", "SMART", "EUR"),    # BNP Paribas
}

# Actions DE (Xetra = IBIS in IBKR, or SMART)
DE_STOCKS = {
    "SAP": Stock("SAP", "SMART", "EUR"),   # SAP
    "SIE": Stock("SIE", "SMART", "EUR"),   # Siemens
    "ALV": Stock("ALV", "SMART", "EUR"),   # Allianz
    "BMW": Stock("BMW", "SMART", "EUR"),   # BMW
}

# Actions NL (Amsterdam — SMART routing)
NL_STOCKS = {
    "ASML": Stock("ASML", "SMART", "EUR"),  # ASML
    "SHELL": Stock("SHELL", "SMART", "EUR"),  # Shell PLC (EUR listing)
}

# ETFs indices
EU_ETFS = {
    "EXS1": Stock("EXS1", "SMART", "EUR"),  # iShares DAX ETF (Xetra)
    "ISF":  Stock("ISF", "SMART", "GBP"),    # iShares FTSE 100 ETF (LSE)
}

# Indices (pour reference, pas de trading direct)
EU_INDICES = {
    "DAX":  Index("DAX", "EUREX", "EUR"),    # DAX index via EUREX
    # CAC et FTSE via leurs ETFs si les indices echouent
}

# All contracts grouped
ALL_CONTRACTS = {}
ALL_CONTRACTS.update(FR_STOCKS)
ALL_CONTRACTS.update(DE_STOCKS)
ALL_CONTRACTS.update(NL_STOCKS)
ALL_CONTRACTS.update(EU_ETFS)
ALL_CONTRACTS.update(EU_INDICES)

# Stocks only (for backtesting)
STOCK_CONTRACTS = {}
STOCK_CONTRACTS.update(FR_STOCKS)
STOCK_CONTRACTS.update(DE_STOCKS)
STOCK_CONTRACTS.update(NL_STOCKS)
STOCK_CONTRACTS.update(EU_ETFS)


def connect_ibkr() -> IB:
    """Connect to IBKR TWS."""
    ib = IB()
    print(f"[IBKR] Connecting to {IBKR_HOST}:{IBKR_PORT} (clientId={IBKR_CLIENT_ID})...")
    ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
    print(f"[IBKR] Connected. Account: {ib.managedAccounts()}")
    return ib


def fetch_historical(
    ib: IB,
    symbol: str,
    contract,
    bar_size: str = "1 day",
    duration: str = "1 Y",
    what_to_show: str = "TRADES",
) -> pd.DataFrame:
    """
    Fetch historical data for a single contract.
    Returns a DataFrame with columns: open, high, low, close, volume.
    """
    print(f"  [FETCH] {symbol} | {bar_size} | {duration} | {what_to_show}")

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=True,            # Regular trading hours only
            formatDate=1,
        )

        if not bars:
            print(f"    [WARN] {symbol}: no data returned")
            return pd.DataFrame()

        df = util.df(bars)
        if df.empty:
            print(f"    [WARN] {symbol}: empty DataFrame")
            return pd.DataFrame()

        # Normalize columns
        df = df.rename(columns={"date": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()

        # Keep standard OHLCV columns
        cols_keep = ["open", "high", "low", "close", "volume"]
        cols_available = [c for c in cols_keep if c in df.columns]
        df = df[cols_available]

        # Remove zero-volume bars (no trading)
        if "volume" in df.columns:
            df = df[df["volume"] > 0]

        print(f"    [OK] {symbol}: {len(df)} bars | {df.index[0]} -> {df.index[-1]}")
        return df

    except Exception as e:
        print(f"    [ERROR] {symbol}: {e}")
        return pd.DataFrame()


def save_parquet(df: pd.DataFrame, symbol: str, timeframe: str):
    """Save DataFrame to parquet in data_cache/eu/."""
    if df.empty:
        return
    path = CACHE_DIR / f"{symbol}_{timeframe}.parquet"
    df.to_parquet(path)
    print(f"    [SAVED] {path.name} ({len(df)} bars)")


def load_parquet(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load DataFrame from parquet cache."""
    path = CACHE_DIR / f"{symbol}_{timeframe}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame()


def fetch_all_daily(ib: IB):
    """Fetch 1 year of daily bars for all EU contracts."""
    print("\n" + "=" * 60)
    print("  PHASE 1a: DAILY BARS (1 year)")
    print("=" * 60)

    results = {}
    for symbol, contract in ALL_CONTRACTS.items():
        # Check cache first
        cached = load_parquet(symbol, "1D")
        if not cached.empty and len(cached) > 200:
            print(f"  [CACHE] {symbol}: {len(cached)} daily bars")
            results[symbol] = cached
            continue

        # For indices, use what_to_show="TRADES" (some need "MIDPOINT")
        what = "TRADES"
        if isinstance(contract, Index):
            what = "TRADES"

        df = fetch_historical(
            ib, symbol, contract,
            bar_size="1 day",
            duration="1 Y",
            what_to_show=what,
        )

        if df.empty and isinstance(contract, Index):
            # Retry with MIDPOINT for indices
            print(f"    [RETRY] {symbol} with MIDPOINT...")
            time.sleep(10)
            df = fetch_historical(
                ib, symbol, contract,
                bar_size="1 day",
                duration="1 Y",
                what_to_show="MIDPOINT",
            )

        if not df.empty:
            save_parquet(df, symbol, "1D")
            results[symbol] = df

        time.sleep(10)  # Rate limit

    print(f"\n  [DAILY SUMMARY] {len(results)}/{len(ALL_CONTRACTS)} tickers fetched")
    return results


def fetch_all_intraday(ib: IB):
    """Fetch 15-minute intraday bars (max available, ~6-12 months)."""
    print("\n" + "=" * 60)
    print("  PHASE 1b: INTRADAY 15M BARS")
    print("=" * 60)

    results = {}
    # Only fetch intraday for tradeable stocks/ETFs (not indices)
    for symbol, contract in STOCK_CONTRACTS.items():
        # Check cache first
        cached = load_parquet(symbol, "15M")
        if not cached.empty and len(cached) > 500:
            print(f"  [CACHE] {symbol}: {len(cached)} 15M bars")
            results[symbol] = cached
            continue

        # IBKR allows ~6 months of 15min data
        # Fetch in 30-day chunks to stay within limits
        all_dfs = []
        end_dt = datetime.now()

        for chunk_i in range(6):  # 6 x 30 days = ~180 days
            chunk_end = end_dt - timedelta(days=chunk_i * 30)
            end_str = chunk_end.strftime("%Y%m%d %H:%M:%S")

            df_chunk = fetch_historical(
                ib, f"{symbol} (chunk {chunk_i+1}/6)", contract,
                bar_size="15 mins",
                duration="30 D",
                what_to_show="TRADES",
            )

            if not df_chunk.empty:
                all_dfs.append(df_chunk)

            time.sleep(10)  # Rate limit between each request

        if all_dfs:
            df = pd.concat(all_dfs).sort_index()
            df = df[~df.index.duplicated(keep="first")]
            save_parquet(df, symbol, "15M")
            results[symbol] = df
            print(f"    [COMBINED] {symbol}: {len(df)} 15M bars total")
        else:
            print(f"    [WARN] {symbol}: no intraday data")

    print(f"\n  [INTRADAY SUMMARY] {len(results)}/{len(STOCK_CONTRACTS)} tickers fetched")
    return results


def compute_stats(daily_data: dict, intraday_data: dict):
    """Compute basic stats: ATR, avg volume, correlation matrix."""
    print("\n" + "=" * 60)
    print("  PHASE 1c: COMPUTING STATS")
    print("=" * 60)

    stats = {}

    for symbol, df in daily_data.items():
        if df.empty or len(df) < 20:
            continue

        # ATR 14
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_14 = tr.rolling(14).mean().iloc[-1]
        atr_pct = (atr_14 / close.iloc[-1]) * 100 if close.iloc[-1] > 0 else 0

        # Average volume
        avg_vol = df["volume"].rolling(20).mean().iloc[-1] if "volume" in df.columns else 0

        # Daily return stats
        returns = close.pct_change().dropna()
        daily_vol = returns.std() * 100

        stats[symbol] = {
            "last_price": round(float(close.iloc[-1]), 2),
            "atr_14": round(float(atr_14), 2) if not pd.isna(atr_14) else 0,
            "atr_pct": round(float(atr_pct), 2),
            "avg_volume_20d": int(avg_vol) if not pd.isna(avg_vol) else 0,
            "daily_vol_pct": round(float(daily_vol), 2),
            "bars_daily": len(df),
            "bars_intraday": len(intraday_data.get(symbol, pd.DataFrame())),
            "start_date": str(df.index[0].date()) if hasattr(df.index[0], "date") else str(df.index[0])[:10],
            "end_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1])[:10],
        }

        print(f"  {symbol:>6s} | Price: {stats[symbol]['last_price']:>8.2f} | "
              f"ATR: {stats[symbol]['atr_pct']:>5.2f}% | "
              f"Vol: {stats[symbol]['avg_volume_20d']:>12,} | "
              f"DailyVol: {stats[symbol]['daily_vol_pct']:>5.2f}%")

    # Correlation matrix (daily returns)
    print("\n  Correlation matrix (daily returns):")
    returns_dict = {}
    for symbol, df in daily_data.items():
        if len(df) >= 60 and "close" in df.columns:
            returns_dict[symbol] = df["close"].pct_change().dropna()

    if len(returns_dict) >= 2:
        returns_df = pd.DataFrame(returns_dict)
        # Align on common dates
        returns_df = returns_df.dropna()
        if len(returns_df) >= 30:
            corr = returns_df.corr()
            print(corr.round(2).to_string())

            # Save correlation
            corr_path = CACHE_DIR / "eu_correlation.csv"
            corr.to_csv(corr_path)
            print(f"\n  [SAVED] {corr_path}")

    # Save stats
    stats_path = CACHE_DIR / "eu_stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  [SAVED] {stats_path}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Fetch EU market data from IBKR")
    parser.add_argument("--daily-only", action="store_true", help="Fetch daily bars only")
    parser.add_argument("--intraday-only", action="store_true", help="Fetch intraday bars only")
    parser.add_argument("--stats-only", action="store_true", help="Compute stats from cache only")
    args = parser.parse_args()

    print("=" * 60)
    print("  EU DATA FETCHER — IBKR TWS")
    print("=" * 60)
    print(f"  Cache dir: {CACHE_DIR}")
    print(f"  Contracts: {len(ALL_CONTRACTS)} total")
    print(f"    FR stocks: {list(FR_STOCKS.keys())}")
    print(f"    DE stocks: {list(DE_STOCKS.keys())}")
    print(f"    NL stocks: {list(NL_STOCKS.keys())}")
    print(f"    ETFs:      {list(EU_ETFS.keys())}")
    print(f"    Indices:   {list(EU_INDICES.keys())}")

    if args.stats_only:
        # Load from cache
        daily = {}
        intraday = {}
        for symbol in ALL_CONTRACTS:
            d = load_parquet(symbol, "1D")
            if not d.empty:
                daily[symbol] = d
            i = load_parquet(symbol, "15M")
            if not i.empty:
                intraday[symbol] = i
        compute_stats(daily, intraday)
        return

    # Connect to IBKR
    ib = connect_ibkr()

    try:
        daily_data = {}
        intraday_data = {}

        if not args.intraday_only:
            daily_data = fetch_all_daily(ib)

        if not args.daily_only:
            intraday_data = fetch_all_intraday(ib)

        # If only one phase was run, load other from cache
        if args.daily_only:
            for symbol in STOCK_CONTRACTS:
                cached = load_parquet(symbol, "15M")
                if not cached.empty:
                    intraday_data[symbol] = cached

        if args.intraday_only:
            for symbol in ALL_CONTRACTS:
                cached = load_parquet(symbol, "1D")
                if not cached.empty:
                    daily_data[symbol] = cached

        # Compute stats
        compute_stats(daily_data, intraday_data)

    finally:
        ib.disconnect()
        print("\n[IBKR] Disconnected.")

    print("\n" + "=" * 60)
    print("  EU DATA FETCH COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
