#!/usr/bin/env python3
"""Cross-Asset Momentum Pipeline — fetch, backtest, walk-forward, verdict.

Usage:
    python scripts/run_cross_asset_momentum.py [--years 3]

5 assets: SPY, TLT, GLD, EURUSD, BTC
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("xasset_momentum")


# Ticker mapping: strategy symbol -> Alpaca/broker ticker
TICKERS = {
    "SPY": "SPY",
    "TLT": "TLT",
    "GLD": "GLD",
    "EURUSD": "EURUSD=X",  # Will use Alpaca proxy or IBKR
    "BTC": "BTC/USD",       # Alpaca crypto
}

# Alpaca-fetchable tickers (equity + crypto)
ALPACA_EQUITY = ["SPY", "TLT", "GLD"]
ALPACA_CRYPTO = ["BTC/USD"]


def fetch_data(years: int = 3) -> dict[str, pd.DataFrame]:
    """Fetch data for all 5 assets."""
    data_dir = ROOT / "data" / "cross_asset"
    data_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_SECRET_KEY")

    if not api_key:
        logger.error("ALPACA_API_KEY required")
        sys.exit(1)

    import requests
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)
    results = {}

    # 1. Fetch equities (SPY, TLT, GLD)
    for ticker in ALPACA_EQUITY:
        cache = data_dir / f"{ticker}.parquet"
        if cache.exists() and (time.time() - cache.stat().st_mtime) / 3600 < 24:
            results[ticker] = pd.read_parquet(cache)
            logger.info(f"{ticker}: cached ({len(results[ticker])} bars)")
            continue

        logger.info(f"Fetching {ticker}...")
        df = _fetch_alpaca_bars(ticker, start_date, end_date, headers, "stocks")
        if df is not None and len(df) > 100:
            df.to_parquet(cache)
            results[ticker] = df
            logger.info(f"{ticker}: {len(df)} bars OK")
        else:
            logger.warning(f"{ticker}: FAILED")
        time.sleep(0.5)

    # 2. Fetch BTC via Alpaca crypto
    cache = data_dir / "BTC.parquet"
    if cache.exists() and (time.time() - cache.stat().st_mtime) / 3600 < 24:
        results["BTC"] = pd.read_parquet(cache)
        logger.info(f"BTC: cached ({len(results['BTC'])} bars)")
    else:
        logger.info("Fetching BTC/USD...")
        df = _fetch_alpaca_bars("BTC/USD", start_date, end_date, headers, "crypto")
        if df is not None and len(df) > 100:
            df.to_parquet(cache)
            results["BTC"] = df
            logger.info(f"BTC: {len(df)} bars OK")
        else:
            logger.warning("BTC: FAILED via Alpaca, trying without")

    # 3. EURUSD — use FX proxy (FXE ETF) or skip if unavailable
    cache = data_dir / "EURUSD.parquet"
    if cache.exists() and (time.time() - cache.stat().st_mtime) / 3600 < 24:
        results["EURUSD"] = pd.read_parquet(cache)
        logger.info(f"EURUSD: cached ({len(results['EURUSD'])} bars)")
    else:
        # Try FXE (EUR/USD ETF proxy) via Alpaca
        logger.info("Fetching EURUSD via FXE proxy...")
        df = _fetch_alpaca_bars("FXE", start_date, end_date, headers, "stocks")
        if df is not None and len(df) > 100:
            df.to_parquet(cache)
            results["EURUSD"] = df
            logger.info(f"EURUSD (FXE): {len(df)} bars OK")
        else:
            logger.warning("EURUSD: unavailable, will run with 4 assets")

    logger.info(f"\nData ready: {list(results.keys())} ({len(results)} assets)")
    return results


def _fetch_alpaca_bars(ticker, start, end, headers, asset_type="stocks"):
    """Fetch bars from Alpaca REST API."""
    import requests

    base = f"https://data.alpaca.markets/v2/{asset_type}/{ticker}/bars"
    if asset_type == "crypto":
        base = f"https://data.alpaca.markets/v1beta3/crypto/us/bars"

    all_bars = []
    page_token = None

    while True:
        if asset_type == "crypto":
            params = {
                "symbols": ticker,
                "timeframe": "1Day",
                "start": start.strftime("%Y-%m-%dT00:00:00Z"),
                "end": end.strftime("%Y-%m-%dT00:00:00Z"),
                "limit": 10000,
            }
        else:
            params = {
                "start": start.strftime("%Y-%m-%dT00:00:00Z"),
                "end": end.strftime("%Y-%m-%dT00:00:00Z"),
                "timeframe": "1Day",
                "limit": 10000,
                "adjustment": "split",
            }

        if page_token:
            params["page_token"] = page_token

        try:
            resp = requests.get(base, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"API error for {ticker}: {e}")
            return None

        if asset_type == "crypto":
            bars = data.get("bars", {}).get(ticker, [])
        else:
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
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    rename = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    df = df.rename(columns=rename)
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[cols].sort_index()


def run_pipeline(years: int = 3):
    """Full pipeline."""
    print("=" * 60)
    print("CROSS-ASSET MOMENTUM PIPELINE")
    print(f"Date: {datetime.now().isoformat()}")
    print("=" * 60)

    # Step 1: Fetch
    print("\n--- STEP 1: DATA ---")
    prices = fetch_data(years)

    if len(prices) < 3:
        print(f"ABORT: Only {len(prices)} assets (need >= 3)")
        return

    # Step 2: Backtest
    print("\n--- STEP 2: BACKTEST ---")
    from strategies_v2.us.cross_asset_momentum import (
        backtest_cross_asset_momentum,
        CrossAssetMomentumConfig,
    )

    # Find common date range
    common_start = max(df.index[0] for df in prices.values())
    common_end = min(df.index[-1] for df in prices.values())

    # Start after lookback warmup
    config = CrossAssetMomentumConfig()
    bt_start = common_start + timedelta(days=config.lookback_days + 10)
    bt_end = common_end

    print(f"Period: {bt_start.date()} to {bt_end.date()}")

    result = backtest_cross_asset_momentum(
        prices=prices,
        start_date=str(bt_start.date()),
        end_date=str(bt_end.date()),
        initial_capital=30_000,
        config=config,
    )

    print(result.summary())

    if result.sharpe_ratio < 0.3:
        print(f"GATE 2 FAILED: Sharpe {result.sharpe_ratio} < 0.3")
        _save_report(result, None)
        return

    print(f"GATE 2 PASSED: Sharpe {result.sharpe_ratio}")

    # Step 3: Walk-Forward
    print("\n--- STEP 3: WALK-FORWARD ---")
    from strategies_v2.us.cross_asset_momentum import walk_forward_cross_asset

    wf = walk_forward_cross_asset(prices, n_windows=5, initial_capital=30_000)

    print(f"\nWalk-Forward Results:")
    print(f"  Avg Sharpe OOS: {wf['avg_sharpe_oos']}")
    print(f"  Min/Max: {wf['min_sharpe_oos']} / {wf['max_sharpe_oos']}")
    print(f"  Profitable windows: {wf['profitable_windows_pct']}%")
    for w in wf["windows"]:
        print(f"    W{w['window']}: Sharpe={w['sharpe']:.2f}, Ret={w['return_pct']:.2f}%, DD={w['max_dd_pct']:.2f}%")

    print(f"\n  VERDICT: {wf['verdict']}")

    # Show latest allocation
    print("\n--- CURRENT ALLOCATION ---")
    from strategies_v2.us.cross_asset_momentum import CrossAssetMomentumStrategy
    strat = CrossAssetMomentumStrategy(config)
    signals = strat.generate_signals(prices, capital=30_000)
    summary = strat.get_portfolio_summary(signals)
    print(f"  Long: {summary['n_long']} assets ({summary['long_pct']}%)")
    print(f"  Cash: {summary['n_cash']} assets ({summary['cash_pct']}%)")
    for a in summary["assets"]:
        print(f"    {a['symbol']:<8} {a['signal']:<6} ret12m={a['return_12m']:>7.1%} "
              f"ret1m={a['return_1m']:>7.1%} weight={a['weight']:>6.1%} ${a['notional']:>6.0f}")

    _save_report(result, wf)


def _save_report(result, wf):
    report = {
        "timestamp": datetime.now().isoformat(),
        "strategy": "cross_asset_momentum",
        "backtest": {
            "sharpe": result.sharpe_ratio,
            "return_pct": result.total_return_pct,
            "ann_return_pct": result.annualized_return_pct,
            "max_dd_pct": result.max_drawdown_pct,
            "volatility_pct": result.volatility_annual,
            "calmar": result.calmar_ratio,
        },
        "walk_forward": wf,
    }
    path = ROOT / "reports" / "research" / "cross_asset_momentum.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    run_pipeline(years=args.years)
