#!/usr/bin/env python3
"""Download S&P 500 daily OHLCV via Alpaca Market Data API (bulk multi-symbol).

Used by the worker cycle us_stocks_daily for production consistency: the
prices we backtest on must come from the same source as execution.

Differences vs download_us_data.py (yfinance):
  - Source: Alpaca IEX feed (free tier, consistent with Alpaca execution)
  - Speed: ~10 sec for 500 tickers (bulk multi-symbol, 100 at a time)
  - No dividends/splits in bar data (would need /v2/corporate_actions endpoint)
  - Requires ALPACA_API_KEY + ALPACA_SECRET_KEY in env

Output (same as download_us_data.py):
  data/us_stocks/<TICKER>.parquet   — OHLCV (no div/split cols)
  data/us_stocks/_metadata.csv
  data/us_stocks/_universe.json
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("download_us_alpaca")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "us_stocks"
OUT_DIR.mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

YEARS = 5
END = datetime.now(UTC).date()
START = END - timedelta(days=YEARS * 365 + 30)

MIN_BARS = 252 * 3
# IEX volume is ~1.5-3% of consolidated SIP volume. $1.5M IEX ADV ≈ $50M SIP.
MIN_ADV_USD = 1_500_000
BATCH_SIZE = 100  # Alpaca bulk bar endpoint accepts up to ~100 symbols


def _to_alpaca_sym(t: str) -> str:
    """Convert yfinance-style dash ticker to Alpaca dot ticker (BRK-B -> BRK.B)."""
    return t.replace("-", ".")


def _from_alpaca_sym(t: str) -> str:
    """Convert Alpaca dot ticker back to yfinance-style dash ticker for file naming."""
    return t.replace(".", "-")


def get_sp500_tickers() -> pd.DataFrame:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    logger.info(f"Fetching S&P 500 list from {url}")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (research-bot)"}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    df = df.rename(columns={
        "Symbol": "ticker", "Security": "name",
        "GICS Sector": "sector", "GICS Sub-Industry": "industry",
    })
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df[["ticker", "name", "sector", "industry"]].drop_duplicates("ticker")


def _bars_to_df(bars) -> pd.DataFrame:
    rows = [{
        "date": b.timestamp,
        "open": float(b.open),
        "high": float(b.high),
        "low": float(b.low),
        "close": float(b.close),
        "adj_close": float(b.close),
        "volume": float(b.volume),
    } for b in bars]
    df = pd.DataFrame(rows).set_index("date")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    # Alpaca daily bars are timestamped at 04:00 UTC (midnight ET) — strip the
    # time component so index matches pure-date expected by downstream code.
    df.index = df.index.normalize()
    df["dividends"] = 0.0
    df["splits"] = 0.0
    return df


def download_batch(client, symbols: list[str], start: datetime, end: datetime, feed: str) -> dict[str, pd.DataFrame]:
    """Download a batch of symbols. Returns {yf_ticker: DataFrame}.

    On batch failure (1 bad symbol kills the whole call), falls back to
    individual requests to salvage the valid ones.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    alpaca_syms = [_to_alpaca_sym(s) for s in symbols]
    try:
        req = StockBarsRequest(
            symbol_or_symbols=alpaca_syms,
            timeframe=TimeFrame.Day,
            start=start, end=end, feed=feed,
        )
        result = client.get_stock_bars(req)
        return {_from_alpaca_sym(sym): _bars_to_df(bars)
                for sym, bars in result.data.items() if bars}
    except Exception as e:
        logger.warning(f"Batch failed ({e}), retrying per-symbol…")
        out = {}
        for sym in alpaca_syms:
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=sym,
                    timeframe=TimeFrame.Day,
                    start=start, end=end, feed=feed,
                )
                r = client.get_stock_bars(req)
                if sym in r.data and r.data[sym]:
                    out[_from_alpaca_sym(sym)] = _bars_to_df(r.data[sym])
            except Exception as e2:
                logger.warning(f"  {sym}: {e2}")
        return out


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
    api_key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret:
        logger.error("ALPACA_API_KEY / ALPACA_SECRET_KEY missing in env")
        return 1

    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except ImportError:
        logger.error("alpaca-py not installed — pip install alpaca-py")
        return 1

    client = StockHistoricalDataClient(api_key, secret)
    feed = os.environ.get("ALPACA_DATA_FEED", "iex")  # iex = free tier
    logger.info(f"Alpaca Market Data feed: {feed}")

    t_start = time.time()
    sp500 = get_sp500_tickers()
    logger.info(f"S&P 500: {len(sp500)} tickers")

    tickers_all = sp500["ticker"].tolist() + ["SPY"]

    start_dt = datetime.combine(START, datetime.min.time())
    end_dt = datetime.combine(END, datetime.min.time())

    all_data: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers_all), BATCH_SIZE):
        batch = tickers_all[i:i + BATCH_SIZE]
        t0 = time.time()
        try:
            batch_data = download_batch(client, batch, start_dt, end_dt, feed)
            all_data.update(batch_data)
            logger.info(f"Batch {i//BATCH_SIZE + 1}: {len(batch_data)}/{len(batch)} downloaded in {time.time()-t0:.1f}s")
        except Exception as e:
            logger.warning(f"Batch {i//BATCH_SIZE + 1} failed: {e}")

    if "SPY" not in all_data:
        logger.error("SPY missing — cannot compute beta")
        return 1

    spy_returns = all_data["SPY"]["adj_close"].pct_change().dropna()
    all_data["SPY"].to_parquet(OUT_DIR / "SPY.parquet")

    rows = []
    ok = 0
    fail = 0
    for r in sp500.itertuples(index=False):
        t = r.ticker
        df = all_data.get(t)
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
            "ticker": t, "name": r.name, "sector": r.sector, "industry": r.industry,
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
        "source": f"alpaca-{feed}",
        "start": START.isoformat(),
        "end": END.isoformat(),
        "n_downloaded": len(meta),
        "n_universe": len(universe),
        "filters": {"min_bars": MIN_BARS, "min_adv_usd": MIN_ADV_USD},
        "tickers": universe,
    }, indent=2))

    dt = time.time() - t_start
    logger.info("")
    logger.info("=== ALPACA DOWNLOAD DONE ===")
    logger.info(f"Duration      : {dt:.1f}s")
    logger.info(f"Downloaded    : {ok}/{len(sp500)} ({fail} failed)")
    logger.info(f"Universe (OK) : {len(universe)}/{len(meta)}")
    logger.info(f"Period        : {START} -> {END}")
    logger.info(f"Feed          : {feed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
