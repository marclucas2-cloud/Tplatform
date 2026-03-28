"""
CryptoDataPipeline V2 — Collect, clean and store crypto market data.

Adapted for Binance France (Margin + Spot + Earn):
  - Candles OHLCV (spot)
  - Margin borrow rates (hourly, per asset)
  - Earn APY rates
  - OI + funding rate READ-ONLY (for signals, not trading)
  - BTC dominance (CoinGecko)

Storage: Parquet for OHLCV, SQLite for metadata/rates.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "crypto"

MAX_KLINES_PER_REQUEST = 1000


class CryptoDataPipeline:
    """Collect, clean and store crypto data from Binance (margin+spot+earn)."""

    UNIVERSE = {
        "tier_1": ["BTCUSDT", "ETHUSDT"],
        "tier_2": ["SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"],
        "tier_3": [
            "AVAXUSDT", "LINKUSDT", "ADAUSDT", "DOTUSDT",
            "NEARUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT",
        ],
    }

    MIN_24H_VOLUME_USD = 50_000_000
    MIN_MARKET_CAP_USD = 1_000_000_000
    MAX_BORROW_RATE_DAILY = 0.001  # 0.1%/day

    def __init__(self, broker=None):
        self._broker = broker
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for sub in ("candles", "funding", "borrow_rates", "earn_rates", "orderbook", "metadata", "features"):
            (DATA_DIR / sub).mkdir(exist_ok=True)

    @property
    def all_symbols(self) -> list[str]:
        symbols = []
        for tier in self.UNIVERSE.values():
            symbols.extend(tier)
        return symbols

    def get_tier(self, symbol: str) -> str:
        for tier_name, symbols in self.UNIVERSE.items():
            if symbol in symbols:
                return tier_name
        return "unknown"

    # ------------------------------------------------------------------
    # Candle collection
    # ------------------------------------------------------------------

    def collect_candles(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        if self._broker is None:
            raise RuntimeError("Broker not set")
        all_candles = []
        current_start = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        while current_start < end_ms:
            data = self._broker.get_prices(symbol, timeframe=interval, bars=MAX_KLINES_PER_REQUEST, start=datetime.fromtimestamp(current_start / 1000, tz=timezone.utc).isoformat(), end=end.isoformat())
            bars = data.get("bars", [])
            if not bars:
                break
            all_candles.extend(bars)
            last_time = bars[-1]["t"]
            if last_time <= current_start:
                break
            current_start = last_time + 1
            time.sleep(0.05)
        if not all_candles:
            return pd.DataFrame()
        df = pd.DataFrame(all_candles)
        df = df.rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df

    def save_candles(self, symbol: str, interval: str, df: pd.DataFrame):
        path = DATA_DIR / "candles" / f"{symbol}_{interval}.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        df.to_parquet(path, index=False)

    def load_candles(self, symbol: str, interval: str) -> pd.DataFrame:
        path = DATA_DIR / "candles" / f"{symbol}_{interval}.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    # ------------------------------------------------------------------
    # Borrow rates (margin-specific)
    # ------------------------------------------------------------------

    def collect_borrow_rates(self, asset: str) -> dict:
        """Collect current borrow rate for an asset."""
        if self._broker is None:
            raise RuntimeError("Broker not set")
        return self._broker.get_borrow_rate(asset)

    def save_borrow_rate(self, asset: str, rate_data: dict):
        """Save borrow rate to SQLite."""
        db_path = DATA_DIR / "borrow_rates" / "borrow_rates.sqlite"
        df = pd.DataFrame([{"asset": asset, "timestamp": datetime.now(timezone.utc).isoformat(), **rate_data}])
        with sqlite3.connect(db_path) as conn:
            df.to_sql("borrow_rates", conn, if_exists="append", index=False)

    def load_borrow_rates(self, asset: str) -> pd.DataFrame:
        db_path = DATA_DIR / "borrow_rates" / "borrow_rates.sqlite"
        if not db_path.exists():
            return pd.DataFrame()
        with sqlite3.connect(db_path) as conn:
            try:
                return pd.read_sql(f"SELECT * FROM borrow_rates WHERE asset='{asset}'", conn)
            except Exception:
                return pd.DataFrame()

    # ------------------------------------------------------------------
    # Earn APY rates
    # ------------------------------------------------------------------

    def collect_earn_rates(self) -> list[dict]:
        if self._broker is None:
            raise RuntimeError("Broker not set")
        return self._broker.get_earn_rates()

    def save_earn_rates(self, rates: list[dict]):
        db_path = DATA_DIR / "earn_rates" / "earn_rates.sqlite"
        df = pd.DataFrame(rates)
        df["timestamp"] = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            df.to_sql("earn_rates", conn, if_exists="append", index=False)

    # ------------------------------------------------------------------
    # Read-only futures data (signals only)
    # ------------------------------------------------------------------

    def get_oi_signal(self, symbol: str) -> dict:
        """Read OI from futures API (not trading, just signal)."""
        if self._broker is None:
            raise RuntimeError("Broker not set")
        return self._broker.get_open_interest_readonly(symbol)

    def get_funding_signal(self, symbol: str) -> dict:
        """Read funding rate from futures API (signal only)."""
        if self._broker is None:
            raise RuntimeError("Broker not set")
        return self._broker.get_funding_rate_readonly(symbol)

    # ------------------------------------------------------------------
    # Cleaning
    # ------------------------------------------------------------------

    def clean_candles(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        initial_len = len(df)
        df = df[df["volume"] > 0].copy()
        mid = (df["open"] + df["close"]) / 2
        wick_pct = ((df["high"] - df["low"]) / mid * 100).fillna(0)
        df["flash_crash_flag"] = wick_pct > 10
        df = df[(df["high"] >= df["open"]) & (df["high"] >= df["close"]) & (df["low"] <= df["open"]) & (df["low"] <= df["close"]) & (df["high"] >= df["low"])].copy()
        df = df.drop_duplicates(subset=["timestamp"])
        if "timestamp" in df.columns and len(df) >= 3:
            df = df.set_index("timestamp").sort_index()
            df = df[~df.index.duplicated(keep="first")]
            freq = pd.infer_freq(df.index[:20]) if len(df) >= 3 else None
            if freq:
                df = df.asfreq(freq)
                df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].ffill(limit=5)
                df["volume"] = df["volume"].fillna(0)
            df = df.reset_index()
        removed = initial_len - len(df)
        if removed > 0:
            logger.info(f"Cleaned: removed {removed}/{initial_len}")
        return df

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    def compute_features(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        df = self.load_candles(symbol, interval)
        if df.empty or len(df) < 200:
            return pd.DataFrame()
        df = df.sort_values("timestamp").reset_index(drop=True)
        df["ret_1h"] = df["close"].pct_change()
        df["ret_4h"] = df["close"].pct_change(4)
        df["ret_24h"] = df["close"].pct_change(24)
        df["vol_7d"] = df["ret_1h"].rolling(168).std() * np.sqrt(8760)
        df["vol_30d"] = df["ret_1h"].rolling(720).std() * np.sqrt(8760)
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(168).mean()
        df["ema_20"] = df["close"].ewm(span=20).mean()
        df["ema_50"] = df["close"].ewm(span=50).mean()
        df["ema_200"] = df["close"].ewm(span=200).mean()
        # RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi_14"] = 100 - (100 / (1 + rs))
        # ADX
        df["adx_14"] = self._compute_adx(df, 14)
        # ATR
        tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
        df["atr_14"] = tr.rolling(14).mean()
        # Bollinger Bands
        df["bb_mid"] = df["close"].rolling(20).mean()
        df["bb_std"] = df["close"].rolling(20).std()
        df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
        return df

    @staticmethod
    def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * plus_dm.rolling(period).mean() / atr
        minus_di = 100 * minus_dm.rolling(period).mean() / atr
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
        return dx.rolling(period).mean()

    # ------------------------------------------------------------------
    # Universe filter
    # ------------------------------------------------------------------

    def filter_universe(self) -> list[str]:
        if self._broker is None:
            raise RuntimeError("Broker not set")
        valid = []
        for symbol in self.all_symbols:
            try:
                ticker = self._broker.get_ticker_24h(symbol)
                if ticker.get("quote_volume", 0) < self.MIN_24H_VOLUME_USD:
                    continue
                # Check borrow availability
                base = symbol.replace("USDT", "")
                rate = self._broker.get_borrow_rate(base)
                if rate.get("daily_rate", 1) > self.MAX_BORROW_RATE_DAILY:
                    logger.debug(f"{symbol}: borrow rate too high")
                    continue
                valid.append(symbol)
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Filter failed {symbol}: {e}")
        logger.info(f"Universe: {len(valid)}/{len(self.all_symbols)} pass")
        return valid
