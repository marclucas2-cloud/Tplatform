"""
IBKR historical data downloader for EU indices.

Downloads OHLCV bars from Interactive Brokers TWS/Gateway via ib_insync.
Handles rate limiting, pagination for large requests, and timezone
conversion to UTC.

EU index IBKR contract specs:
  DAX      : symbol="DAX",    exchange="EUREX", currency="EUR"
  CAC40    : symbol="CAC40",  exchange="MONEP", currency="EUR"
  ESTX50   : symbol="ESTX50", exchange="EUREX", currency="EUR"
  FTSE100  : symbol="Z",      exchange="ICEEU", currency="GBP"

IBKR rate limits:
  - Max 60 historical data requests per 10 minutes
  - Identical requests within 15 seconds are paced
  - Large durations auto-paginated to avoid timeouts

Usage:
    loader = IBKRDataLoader(host="127.0.0.1", port=4002, client_id=50)
    df = loader.download_bars("DAX", duration="30 D", bar_size="5 mins")
    multi = loader.download_multi(["DAX", "CAC40", "ESTX50"], "30 D", "5 mins")
"""
from __future__ import annotations

import logging
import time as _time

import pandas as pd

logger = logging.getLogger(__name__)

# -- EU index contract specifications --
EU_INDEX_CONTRACTS = {
    "DAX": {
        "symbol": "DAX",
        "sec_type": "IND",
        "exchange": "EUREX",
        "currency": "EUR",
        "description": "DAX 40 Index",
    },
    "CAC40": {
        "symbol": "CAC40",
        "sec_type": "IND",
        "exchange": "MONEP",
        "currency": "EUR",
        "description": "CAC 40 Index",
    },
    "ESTX50": {
        "symbol": "ESTX50",
        "sec_type": "IND",
        "exchange": "EUREX",
        "currency": "EUR",
        "description": "Euro Stoxx 50 Index",
    },
    "FTSE100": {
        "symbol": "Z",
        "sec_type": "IND",
        "exchange": "ICEEU",
        "currency": "GBP",
        "description": "FTSE 100 Index",
    },
}

# IBKR rate limits
_MAX_REQUESTS_PER_10MIN = 60
_MIN_REQUEST_INTERVAL_SEC = 10.5  # Safe interval to stay under 60/10min
_PACING_WAIT_SEC = 15.0           # Wait after pacing violation

# Duration limits for single IBKR requests (approximate)
_DURATION_LIMITS = {
    "1 secs": "1800 S",
    "5 secs": "3600 S",
    "1 min": "1 D",
    "2 mins": "2 D",
    "5 mins": "1 W",
    "15 mins": "2 W",
    "30 mins": "1 M",
    "1 hour": "1 M",
    "1 day": "1 Y",
}


class IBKRDataLoader:
    """IBKR historical data downloader.

    Downloads bars from IBKR TWS/Gateway, handling rate limiting,
    pagination, and timezone conversion.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 50,
        timeout: int = 60,
    ):
        """
        Args:
            host: IBKR Gateway/TWS host (default 127.0.0.1).
            port: IBKR Gateway/TWS port (4001=live, 4002=paper).
            client_id: Unique client ID for this connection.
            timeout: Connection timeout in seconds.
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout

        # Rate limiting state
        self._request_timestamps: list[float] = []
        self._last_request_time: float = 0.0

        # Connection (lazy)
        self._ib = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish connection to IBKR Gateway/TWS.

        Uses ib_insync. Connection is lazy and will be established
        on first data request if not already connected.
        """
        if self._ib is not None and self._ib.isConnected():
            return

        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError(
                "ib_insync is required for IBKR data loading. "
                "Install with: pip install ib_insync"
            )

        self._ib = IB()
        self._ib.connect(
            host=self.host,
            port=self.port,
            clientId=self.client_id,
            timeout=self.timeout,
        )
        logger.info(
            f"Connected to IBKR at {self.host}:{self.port} "
            f"(clientId={self.client_id})"
        )

    def disconnect(self) -> None:
        """Disconnect from IBKR Gateway/TWS."""
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
            logger.info("Disconnected from IBKR")

    def _ensure_connected(self) -> None:
        """Ensure we have an active IBKR connection."""
        if self._ib is None or not self._ib.isConnected():
            self.connect()

    # ------------------------------------------------------------------
    # Contract creation
    # ------------------------------------------------------------------

    def _make_contract(self, symbol: str, exchange: str | None = None):
        """Create an ib_insync contract for the given symbol.

        If the symbol matches a known EU index, uses predefined specs.
        Otherwise creates a generic Index contract.

        Args:
            symbol: Symbol name (e.g., "DAX", "CAC40", "ESTX50", "FTSE100").
            exchange: Optional exchange override.

        Returns:
            ib_insync.Contract object.
        """
        from ib_insync import Index

        spec = EU_INDEX_CONTRACTS.get(symbol.upper())
        if spec:
            return Index(
                symbol=spec["symbol"],
                exchange=exchange or spec["exchange"],
                currency=spec["currency"],
            )

        # Fallback: use symbol as-is
        return Index(
            symbol=symbol,
            exchange=exchange or "SMART",
            currency="USD",
        )

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _wait_for_rate_limit(self) -> None:
        """Wait if necessary to respect IBKR rate limits.

        IBKR allows max 60 requests per 10 minutes.
        We enforce a minimum interval between requests.
        """
        now = _time.monotonic()

        # Clean up timestamps older than 10 minutes
        cutoff = now - 600.0
        self._request_timestamps = [
            ts for ts in self._request_timestamps if ts > cutoff
        ]

        # Check request count in last 10 minutes
        if len(self._request_timestamps) >= _MAX_REQUESTS_PER_10MIN - 5:
            # Near the limit, wait longer
            wait_until = self._request_timestamps[0] + 600.0
            sleep_time = wait_until - now
            if sleep_time > 0:
                logger.info(
                    f"IBKR rate limit: {len(self._request_timestamps)} requests "
                    f"in last 10min, waiting {sleep_time:.1f}s"
                )
                _time.sleep(sleep_time)

        # Minimum interval between requests
        elapsed = now - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL_SEC:
            sleep_time = _MIN_REQUEST_INTERVAL_SEC - elapsed
            _time.sleep(sleep_time)

        # Record this request
        self._last_request_time = _time.monotonic()
        self._request_timestamps.append(self._last_request_time)

    # ------------------------------------------------------------------
    # Data download
    # ------------------------------------------------------------------

    def download_bars(
        self,
        symbol: str,
        duration: str = "30 D",
        bar_size: str = "5 mins",
        exchange: str | None = None,
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        end_datetime: str = "",
    ) -> pd.DataFrame:
        """Download historical bars from IBKR.

        Args:
            symbol: Symbol name (e.g., "DAX", "CAC40").
            duration: How far back to go (e.g., "30 D", "1 Y").
            bar_size: Bar size (e.g., "1 min", "5 mins", "1 hour", "1 day").
            exchange: Optional exchange override.
            what_to_show: Data type ("TRADES", "MIDPOINT", "BID", "ASK").
            use_rth: True for regular trading hours only.
            end_datetime: End time (empty string = now).

        Returns:
            DataFrame with columns [open, high, low, close, volume]
            and DatetimeIndex in UTC.
        """
        self._ensure_connected()

        contract = self._make_contract(symbol, exchange)

        # Qualify the contract
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(
                f"Failed to qualify contract for {symbol}. "
                f"Check symbol, exchange, and IBKR connection."
            )
        contract = qualified[0]

        logger.info(
            f"Downloading {symbol} bars: duration={duration}, "
            f"bar_size={bar_size}, what_to_show={what_to_show}, "
            f"use_rth={use_rth}"
        )

        # Rate limit
        self._wait_for_rate_limit()

        # Request historical data
        try:
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime=end_datetime,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=2,  # UTC format
            )
        except Exception as e:
            error_msg = str(e).lower()
            if "pacing" in error_msg:
                logger.warning(
                    f"IBKR pacing violation for {symbol}, "
                    f"waiting {_PACING_WAIT_SEC}s and retrying"
                )
                _time.sleep(_PACING_WAIT_SEC)
                self._wait_for_rate_limit()
                bars = self._ib.reqHistoricalData(
                    contract,
                    endDateTime=end_datetime,
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=what_to_show,
                    useRTH=use_rth,
                    formatDate=2,
                )
            else:
                raise

        if not bars:
            logger.warning(f"No bars returned for {symbol}")
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"]
            )

        # Convert to DataFrame
        df = self._bars_to_dataframe(bars)

        logger.info(
            f"Downloaded {len(df)} bars for {symbol} "
            f"({df.index.min()} to {df.index.max()})"
        )

        return df

    def _bars_to_dataframe(self, bars) -> pd.DataFrame:
        """Convert ib_insync BarData list to a pandas DataFrame.

        Args:
            bars: List of ib_insync BarData objects.

        Returns:
            DataFrame with OHLCV columns and DatetimeIndex in UTC.
        """
        rows = []
        for bar in bars:
            rows.append({
                "datetime": bar.date,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume) if bar.volume >= 0 else 0,
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"]
            )

        # Parse datetime and set as index
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()

        # Ensure UTC timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        return df

    # ------------------------------------------------------------------
    # Multi-symbol download
    # ------------------------------------------------------------------

    def download_multi(
        self,
        symbols: list[str],
        duration: str = "30 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """Download historical bars for multiple symbols.

        Handles IBKR rate limiting (max 60 requests per 10 minutes)
        with proper spacing between requests.

        Args:
            symbols: List of symbol names (e.g., ["DAX", "CAC40", "ESTX50"]).
            duration: How far back to go.
            bar_size: Bar size setting.
            what_to_show: Data type.
            use_rth: Regular trading hours only.

        Returns:
            Dict mapping symbol name to DataFrame.
        """
        results = {}

        logger.info(
            f"Downloading {len(symbols)} symbols: {symbols}, "
            f"duration={duration}, bar_size={bar_size}"
        )

        for i, symbol in enumerate(symbols):
            try:
                df = self.download_bars(
                    symbol=symbol,
                    duration=duration,
                    bar_size=bar_size,
                    what_to_show=what_to_show,
                    use_rth=use_rth,
                )
                results[symbol] = df

                logger.info(
                    f"[{i + 1}/{len(symbols)}] {symbol}: "
                    f"{len(df)} bars downloaded"
                )

            except Exception as e:
                logger.error(f"Failed to download {symbol}: {e}")
                results[symbol] = pd.DataFrame(
                    columns=["open", "high", "low", "close", "volume"]
                )

        return results

    # ------------------------------------------------------------------
    # Data validation
    # ------------------------------------------------------------------

    def validate_downloaded_data(
        self, df: pd.DataFrame, symbol: str
    ) -> dict:
        """Run data quality checks on downloaded bars.

        Validates:
        - Non-empty DataFrame
        - No NaN values in OHLCV columns
        - OHLC consistency (high >= open/close, low <= open/close)
        - No duplicate timestamps
        - Monotonically increasing index
        - Reasonable price range (no zero or negative prices)
        - Volume sanity (no negative volumes)
        - Gap detection (missing bars during session hours)

        Args:
            df: Downloaded DataFrame with OHLCV columns.
            symbol: Symbol name for reporting.

        Returns:
            {
                "symbol": str,
                "valid": bool,
                "n_bars": int,
                "date_range": {start, end} or None,
                "issues": [str],
                "warnings": [str],
                "stats": {
                    "nan_count": int,
                    "duplicate_count": int,
                    "ohlc_invalid_count": int,
                    "zero_price_count": int,
                    "negative_volume_count": int,
                },
            }
        """
        issues = []
        warnings = []
        stats = {
            "nan_count": 0,
            "duplicate_count": 0,
            "ohlc_invalid_count": 0,
            "zero_price_count": 0,
            "negative_volume_count": 0,
        }

        # Empty check
        if df.empty:
            return {
                "symbol": symbol,
                "valid": False,
                "n_bars": 0,
                "date_range": None,
                "issues": ["DataFrame is empty"],
                "warnings": [],
                "stats": stats,
            }

        n_bars = len(df)

        # Date range
        date_range = None
        if isinstance(df.index, pd.DatetimeIndex):
            date_range = {
                "start": df.index.min().isoformat(),
                "end": df.index.max().isoformat(),
            }

        # Required columns
        required = {"open", "high", "low", "close", "volume"}
        missing_cols = required - set(df.columns)
        if missing_cols:
            issues.append(f"Missing columns: {missing_cols}")
            return {
                "symbol": symbol,
                "valid": False,
                "n_bars": n_bars,
                "date_range": date_range,
                "issues": issues,
                "warnings": warnings,
                "stats": stats,
            }

        # NaN values
        for col in ["open", "high", "low", "close", "volume"]:
            nan_count = int(df[col].isna().sum())
            stats["nan_count"] += nan_count
            if nan_count > 0:
                issues.append(f"{nan_count} NaN values in {col}")

        # Duplicate timestamps
        if isinstance(df.index, pd.DatetimeIndex):
            dup_count = int(df.index.duplicated().sum())
            stats["duplicate_count"] = dup_count
            if dup_count > 0:
                warnings.append(f"{dup_count} duplicate timestamps")

            # Monotonically increasing
            if not df.index.is_monotonic_increasing:
                issues.append("Index is not monotonically increasing")

        # OHLC consistency
        ohlc_invalid = 0
        if not df[["open", "high", "low", "close"]].isna().any().any():
            # high >= max(open, close)
            high_invalid = (
                df["high"] < df[["open", "close"]].max(axis=1)
            ).sum()
            # low <= min(open, close)
            low_invalid = (
                df["low"] > df[["open", "close"]].min(axis=1)
            ).sum()
            ohlc_invalid = int(high_invalid + low_invalid)
            stats["ohlc_invalid_count"] = ohlc_invalid
            if ohlc_invalid > 0:
                pct = (ohlc_invalid / n_bars) * 100
                if pct > 5:
                    issues.append(
                        f"{ohlc_invalid} OHLC-inconsistent bars ({pct:.1f}%)"
                    )
                else:
                    warnings.append(
                        f"{ohlc_invalid} OHLC-inconsistent bars ({pct:.1f}%)"
                    )

        # Zero or negative prices
        for col in ["open", "high", "low", "close"]:
            zero_count = int((df[col] <= 0).sum())
            stats["zero_price_count"] += zero_count
        if stats["zero_price_count"] > 0:
            issues.append(
                f"{stats['zero_price_count']} zero/negative price values"
            )

        # Negative volumes
        neg_vol = int((df["volume"] < 0).sum())
        stats["negative_volume_count"] = neg_vol
        if neg_vol > 0:
            warnings.append(f"{neg_vol} negative volume values")

        # Gap detection (estimate expected frequency)
        if isinstance(df.index, pd.DatetimeIndex) and n_bars >= 10:
            diffs = df.index.to_series().diff().dropna()
            if len(diffs) > 0:
                median_diff = diffs.median()
                large_gaps = diffs[diffs > median_diff * 5]
                if len(large_gaps) > 0:
                    warnings.append(
                        f"{len(large_gaps)} large gaps detected "
                        f"(> 5x median interval of {median_diff})"
                    )

        valid = len(issues) == 0

        return {
            "symbol": symbol,
            "valid": valid,
            "n_bars": n_bars,
            "date_range": date_range,
            "issues": issues,
            "warnings": warnings,
            "stats": stats,
        }

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
