"""
Data fetcher : connexion Alpaca + cache local Parquet pour éviter
de re-télécharger les données à chaque run.
"""
import os
import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import config


def get_client() -> StockHistoricalDataClient:
    """Crée le client Alpaca data (pas besoin de clés pour données gratuites)."""
    return StockHistoricalDataClient(
        api_key=config.ALPACA_API_KEY or None,
        secret_key=config.ALPACA_SECRET_KEY or None,
    )


def fetch_bars(
    ticker: str,
    timeframe: str = "1Min",
    start: datetime = None,
    end: datetime = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Récupère les barres OHLCV depuis Alpaca avec cache Parquet local.
    
    Returns:
        DataFrame avec colonnes : open, high, low, close, volume, vwap, trade_count
        Index : timestamp (US/Eastern)
    """
    start = start or config.BACKTEST_START
    end = end or config.BACKTEST_END

    # ── Cache ──
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(
        config.CACHE_DIR,
        f"{ticker}_{timeframe}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
    )

    if use_cache and os.path.exists(cache_file):
        df = pd.read_parquet(cache_file)
        print(f"  [CACHE] {ticker} {timeframe}: {len(df)} bars chargées")
        return df

    # ── Fetch Alpaca ──
    client = get_client()
    tf = TimeFrame.Minute if "1" in timeframe else TimeFrame(5, TimeFrame.Minute.unit) if "5" in timeframe else TimeFrame.Hour

    # Alpaca limite les requêtes — on chunk par mois
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
            if bars and ticker in bars.data and len(bars.data[ticker]) > 0:
                rows = []
                for bar in bars.data[ticker]:
                    rows.append({
                        "timestamp": bar.timestamp,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume),
                        "vwap": float(bar.vwap) if bar.vwap else None,
                        "trade_count": int(bar.trade_count) if bar.trade_count else 0,
                    })
                all_bars.extend(rows)
        except Exception as e:
            print(f"  [WARN] {ticker} chunk {chunk_start.date()}-{chunk_end.date()}: {e}")
        chunk_start = chunk_end

    if not all_bars:
        print(f"  [EMPTY] {ticker}: aucune donnée récupérée")
        return pd.DataFrame()

    df = pd.DataFrame(all_bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    # Convertir en Eastern
    df.index = df.index.tz_convert(config.TIMEZONE)

    # ── Cache ──
    df.to_parquet(cache_file)
    print(f"  [FETCH] {ticker} {timeframe}: {len(df)} bars téléchargées et cachées")

    return df


def fetch_multiple(
    tickers: list[str],
    timeframe: str = "1Min",
    start: datetime = None,
    end: datetime = None,
) -> dict[str, pd.DataFrame]:
    """Récupère les données pour plusieurs tickers."""
    data = {}
    for ticker in tickers:
        print(f"Fetching {ticker}...")
        df = fetch_bars(ticker, timeframe, start, end)
        if not df.empty:
            data[ticker] = df
    return data


def get_daily_bars(ticker: str, start: datetime = None, end: datetime = None) -> pd.DataFrame:
    """Récupère les barres daily pour calculs de gaps, volumes moyens, etc."""
    start = start or config.BACKTEST_START - timedelta(days=30)  # Extra pour lookback
    end = end or config.BACKTEST_END

    client = get_client()
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(request)

    if not bars or ticker not in bars.data:
        return pd.DataFrame()

    rows = []
    for bar in bars.data[ticker]:
        rows.append({
            "timestamp": bar.timestamp,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(bar.volume),
        })

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    return df


if __name__ == "__main__":
    # Test rapide
    print("Testing data fetch...")
    df = fetch_bars("AAPL", "5Min")
    if not df.empty:
        print(f"AAPL 5min: {len(df)} bars, from {df.index[0]} to {df.index[-1]}")
        print(df.head())
    else:
        print("No data fetched — check API keys")
