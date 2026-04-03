"""
Data fetcher : connexion Alpaca + cache local Parquet.
Supporte le fetch massif parallélisé pour des univers de 500-5000 tickers.
"""
import os
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import config

_client = None


def get_client() -> StockHistoricalDataClient:
    global _client
    if _client is None:
        _client = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY or None,
            secret_key=config.ALPACA_SECRET_KEY or None,
        )
    return _client


def _get_timeframe(timeframe_str: str) -> TimeFrame:
    if "1" in timeframe_str and "M" in timeframe_str:
        return TimeFrame.Minute
    elif "5" in timeframe_str:
        return TimeFrame(5, TimeFrame.Minute.unit)
    elif "15" in timeframe_str:
        return TimeFrame(15, TimeFrame.Minute.unit)
    elif "D" in timeframe_str:
        return TimeFrame.Day
    return TimeFrame(5, TimeFrame.Minute.unit)


def _cache_path(ticker: str, timeframe: str, start: datetime, end: datetime) -> str:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return os.path.join(
        config.CACHE_DIR,
        f"{ticker}_{timeframe}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
    )


def fetch_bars(
    ticker: str,
    timeframe: str = "5Min",
    start: datetime = None,
    end: datetime = None,
    use_cache: bool = True,
    silent: bool = False,
) -> pd.DataFrame:
    start = start or config.BACKTEST_START
    end = end or config.BACKTEST_END
    cache_file = _cache_path(ticker, timeframe, start, end)

    if use_cache and os.path.exists(cache_file):
        try:
            df = pd.read_parquet(cache_file)
            if not silent:
                print(f"  [CACHE] {ticker}: {len(df)} bars")
            return df
        except Exception:
            pass

    client = get_client()
    tf = _get_timeframe(timeframe)
    all_bars = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=30), end)
        try:
            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=tf,
                start=chunk_start,
                end=chunk_end,
            )
            bars = client.get_stock_bars(request)
            if bars and ticker in bars.data:
                for bar in bars.data[ticker]:
                    all_bars.append({
                        "timestamp": bar.timestamp,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume),
                        "vwap": float(bar.vwap) if bar.vwap else None,
                        "trade_count": int(bar.trade_count) if bar.trade_count else 0,
                    })
        except Exception as e:
            if not silent:
                print(f"  [WARN] {ticker} {chunk_start.date()}: {e}")
        chunk_start = chunk_end

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    df.index = df.index.tz_convert(config.TIMEZONE)

    try:
        df.to_parquet(cache_file)
    except Exception:
        pass

    if not silent:
        print(f"  [FETCH] {ticker}: {len(df)} bars")
    return df


def fetch_batch_multi_symbol(
    tickers: list[str],
    timeframe: str = "5Min",
    start: datetime = None,
    end: datetime = None,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Fetch optimisé multi-symbol : fetche par batch de 50 symboles.
    Beaucoup plus rapide que le fetch un par un pour les gros univers.
    """
    start = start or config.BACKTEST_START
    end = end or config.BACKTEST_END
    client = get_client()
    tf = _get_timeframe(timeframe)
    results = {}

    # Séparer cache vs à fetcher
    to_fetch = []
    for ticker in tickers:
        cache_file = _cache_path(ticker, timeframe, start, end)
        if use_cache and os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty:
                    results[ticker] = df
                    continue
            except Exception:
                pass
        to_fetch.append(ticker)

    if results:
        print(f"  [CACHE] {len(results)} tickers from cache")

    if not to_fetch:
        return results

    print(f"  [FETCH] {len(to_fetch)} tickers to download...")

    batch_size = min(config.FETCH_BATCH_SIZE, 50)
    total_batches = (len(to_fetch) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(to_fetch), batch_size):
        batch = to_fetch[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        batch_data = {t: [] for t in batch}

        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=7), end)
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=tf,
                    start=chunk_start,
                    end=chunk_end,
                )
                bars = client.get_stock_bars(request)
                if bars:
                    for ticker in batch:
                        if ticker in bars.data:
                            for bar in bars.data[ticker]:
                                batch_data[ticker].append({
                                    "timestamp": bar.timestamp,
                                    "open": float(bar.open),
                                    "high": float(bar.high),
                                    "low": float(bar.low),
                                    "close": float(bar.close),
                                    "volume": int(bar.volume),
                                    "vwap": float(bar.vwap) if bar.vwap else None,
                                    "trade_count": int(bar.trade_count) if bar.trade_count else 0,
                                })
            except Exception as e:
                msg = str(e)
                if "too many requests" in msg.lower():
                    time.sleep(5)  # Rate limit — attendre avant retry
                else:
                    print(f"    [WARN] Batch {batch_num} {chunk_start.date()}: {e}")
            chunk_start = chunk_end
            time.sleep(0.5)  # Pause entre chunks pour éviter rate limit

        fetched_count = 0
        for ticker, rows in batch_data.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            df.index = df.index.tz_convert(config.TIMEZONE)
            try:
                df.to_parquet(_cache_path(ticker, timeframe, start, end))
            except Exception:
                pass
            results[ticker] = df
            fetched_count += 1

        print(f"    Batch {batch_num}/{total_batches}: "
              f"{fetched_count}/{len(batch)} OK | Total: {len(results)}/{len(tickers)}")

        if batch_idx + batch_size < len(to_fetch):
            time.sleep(config.FETCH_RATE_LIMIT_SLEEP)

    print(f"  [DONE] {len(results)} tickers loaded")
    return results


def fetch_multiple(
    tickers: list[str],
    timeframe: str = "5Min",
    start: datetime = None,
    end: datetime = None,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Point d'entrée principal — choisit la méthode optimale."""
    if len(tickers) <= 20:
        data = {}
        for ticker in tickers:
            df = fetch_bars(ticker, timeframe, start, end, use_cache)
            if not df.empty:
                data[ticker] = df
        return data
    else:
        return fetch_batch_multi_symbol(tickers, timeframe, start, end, use_cache)


def get_daily_bars(ticker: str, start: datetime = None, end: datetime = None) -> pd.DataFrame:
    return fetch_bars(
        ticker, timeframe="1Day",
        start=start or config.BACKTEST_START - timedelta(days=30),
        end=end or config.BACKTEST_END,
        silent=True,
    )


if __name__ == "__main__":
    print("Testing data fetch...")
    df = fetch_bars("AAPL", "5Min")
    if not df.empty:
        print(f"AAPL 5min: {len(df)} bars, {df.index[0]} -> {df.index[-1]}")
    else:
        print("No data — check ALPACA_API_KEY and ALPACA_SECRET_KEY")
