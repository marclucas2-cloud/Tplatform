"""Anti-lookahead DataFeed for BacktesterV2.

CRITICAL INVARIANT: At any given simulation timestamp T, only bars whose
*close* timestamp is strictly < T are visible. A bar that closes AT T is the
"current" candle and must NOT be returned — it is still forming.

Example: if the engine clock is 14:30 and bars are hourly, the latest
visible bar is the 13:00-14:00 candle (close=14:00 < 14:30). The
14:00-15:00 candle is in progress and invisible.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.backtester_v2.types import Bar

# Required columns in every data source DataFrame
_REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


class DataFeed:
    """Provides strictly past-only market data to strategies.

    Args:
        data_sources: Mapping of symbol -> DataFrame with DatetimeIndex
            and OHLCV columns. DataFrames must be sorted ascending by index.

    Raises:
        ValueError: If data is unsorted or missing required columns.
    """

    def __init__(self, data_sources: Dict[str, pd.DataFrame]) -> None:
        self._data: Dict[str, pd.DataFrame] = {}
        self._timestamp: Optional[pd.Timestamp] = None
        self._cache: Dict[str, object] = {}

        for symbol, df in data_sources.items():
            self._validate_and_store(symbol, df)

    def _validate_and_store(self, symbol: str, df: pd.DataFrame) -> None:
        """Validate DataFrame and store a clean copy.

        Args:
            symbol: Asset identifier.
            df: OHLCV DataFrame with DatetimeIndex.

        Raises:
            ValueError: If columns are missing or data is unsorted.
        """
        missing = _REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(
                f"[{symbol}] Missing columns: {missing}. "
                f"Required: {_REQUIRED_COLUMNS}"
            )

        if not df.index.is_monotonic_increasing:
            raise ValueError(
                f"[{symbol}] Data must be sorted ascending by timestamp. "
                f"Call df.sort_index() before passing to DataFeed."
            )

        # Store a copy to prevent external mutation
        self._data[symbol] = df.copy()

    def set_timestamp(self, timestamp: pd.Timestamp) -> None:
        """Advance the simulation clock. Clears all caches.

        Args:
            timestamp: The new simulation time.
        """
        self._timestamp = timestamp
        self._cache.clear()

    def _get_visible_data(self, symbol: str) -> pd.DataFrame:
        """Return all bars with close timestamp strictly before current time.

        Args:
            symbol: Asset identifier.

        Returns:
            DataFrame slice containing only fully closed bars.

        Raises:
            KeyError: If symbol is unknown.
            RuntimeError: If timestamp has not been set.
        """
        if self._timestamp is None:
            raise RuntimeError("DataFeed.set_timestamp() must be called first")

        cache_key = f"visible_{symbol}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if symbol not in self._data:
            raise KeyError(f"Unknown symbol: {symbol}")

        df = self._data[symbol]
        # Strictly less than current timestamp — the bar closing AT
        # self._timestamp is still "forming" and not visible.
        visible = df.loc[df.index < self._timestamp]
        self._cache[cache_key] = visible
        return visible

    def get_latest_bar(self, symbol: str) -> Optional[Bar]:
        """Return the last fully closed bar.

        Args:
            symbol: Asset identifier.

        Returns:
            A Bar dataclass, or None if no closed bars exist yet.
        """
        visible = self._get_visible_data(symbol)
        if visible.empty:
            return None

        row = visible.iloc[-1]
        return Bar(
            symbol=symbol,
            timestamp=visible.index[-1],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

    def get_bars(self, symbol: str, n: int) -> pd.DataFrame:
        """Return the last N fully closed bars as a DataFrame.

        Args:
            symbol: Asset identifier.
            n: Number of bars to return.

        Returns:
            DataFrame with at most n rows (fewer if not enough history).
        """
        visible = self._get_visible_data(symbol)
        return visible.tail(n).copy()

    def get_indicator(
        self, symbol: str, indicator: str, period: int
    ) -> Optional[float]:
        """Compute a technical indicator on closed bars only.

        Args:
            symbol: Asset identifier.
            indicator: One of: sma, ema, rsi, atr, adx,
                bollinger_upper, bollinger_mid, bollinger_lower.
            period: Lookback period for the indicator.

        Returns:
            The indicator value, or None if insufficient data.
        """
        cache_key = f"ind_{symbol}_{indicator}_{period}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        visible = self._get_visible_data(symbol)
        if len(visible) < period:
            return None

        result = self._calculate_indicator(visible, indicator, period)
        self._cache[cache_key] = result
        return result

    @staticmethod
    def _calculate_indicator(
        df: pd.DataFrame, indicator: str, period: int
    ) -> Optional[float]:
        """Compute indicator value from a DataFrame of closed bars.

        Args:
            df: OHLCV DataFrame (all data is guaranteed to be past-only).
            indicator: Indicator name.
            period: Lookback period.

        Returns:
            Latest indicator value, or None on failure.
        """
        close = df["close"]
        high = df["high"]
        low = df["low"]

        if indicator == "sma":
            return float(close.iloc[-period:].mean())

        if indicator == "ema":
            return float(close.ewm(span=period, adjust=False).mean().iloc[-1])

        if indicator == "rsi":
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = (-delta.clip(upper=0))
            avg_gain = gain.ewm(span=period, adjust=False).mean().iloc[-1]
            avg_loss = loss.ewm(span=period, adjust=False).mean().iloc[-1]
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return float(100.0 - 100.0 / (1.0 + rs))

        if indicator == "atr":
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            return float(tr.iloc[-period:].mean())

        if indicator == "adx":
            return _compute_adx(high, low, close, period)

        if indicator.startswith("bollinger"):
            sma = close.iloc[-period:].mean()
            std = close.iloc[-period:].std()
            if indicator == "bollinger_upper":
                return float(sma + 2 * std)
            if indicator == "bollinger_mid":
                return float(sma)
            if indicator == "bollinger_lower":
                return float(sma - 2 * std)

        raise ValueError(f"Unknown indicator: {indicator}")

    @property
    def symbols(self) -> list[str]:
        """List of available symbols."""
        return list(self._data.keys())

    @property
    def timestamp(self) -> Optional[pd.Timestamp]:
        """Current simulation timestamp."""
        return self._timestamp


def _compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> Optional[float]:
    """Compute Average Directional Index.

    Args:
        high: High prices series.
        low: Low prices series.
        close: Close prices series.
        period: ADX lookback period.

    Returns:
        ADX value, or None if insufficient data.
    """
    if len(high) < period + 1:
        return None

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)) * 100
    adx = dx.ewm(span=period, adjust=False).mean()

    return float(adx.iloc[-1])
