"""Dynamic universe manager for liquid stock filtering and earnings data.

Provides:
  - Liquidity-based filtering to top N stocks from S&P 500 / EU / JP
  - Earnings calendar retrieval for PEAD strategy
  - Survivorship bias detection
  - Cross-currency PnL normalization (JPY/EUR/GBP -> USD)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# -- FX rates fallback (used when live rates are unavailable) --
_DEFAULT_FX_RATES_TO_USD: Dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
    "CHF": 1.12,
    "CAD": 0.74,
    "AUD": 0.66,
    "NZD": 0.61,
}


@dataclass
class EarningsEvent:
    """Single earnings announcement record."""

    ticker: str
    earnings_date: date
    eps_estimate: float | None = None
    eps_actual: float | None = None
    surprise_pct: float | None = None


@dataclass
class SurvivorshipResult:
    """Result of survivorship bias check."""

    clean_tickers: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class DynamicUniverseManager:
    """Dynamically filter universe to top N liquid stocks.

    Works across markets: US (Alpaca/IBKR), EU (IBKR), JPY (IBKR).

    Args:
        min_volume_usd: Minimum average daily dollar volume to qualify.
        top_n: Maximum number of tickers to keep after filtering.
        fx_rates: Optional custom FX rates dict mapping currency -> USD.
    """

    def __init__(
        self,
        min_volume_usd: float = 50_000_000,
        top_n: int = 100,
        fx_rates: Dict[str, float] | None = None,
    ) -> None:
        self.min_volume_usd = min_volume_usd
        self.top_n = top_n
        self._fx_rates = fx_rates or dict(_DEFAULT_FX_RATES_TO_USD)

    # ------------------------------------------------------------------
    # Liquidity filtering
    # ------------------------------------------------------------------

    def filter_universe(
        self,
        volume_data: pd.DataFrame,
        price_data: pd.DataFrame | None = None,
    ) -> List[str]:
        """Filter tickers to top_n by average daily dollar volume.

        If price_data is provided, dollar volume = volume * close price.
        Otherwise volume_data is assumed to already be in dollar terms.

        Args:
            volume_data: DataFrame with tickers as columns and dates as index.
                Values are raw share volumes (if price_data given) or dollar
                volumes.
            price_data: Optional DataFrame of close prices, same shape as
                volume_data. When supplied, dollar volume is computed as
                volume * price per bar.

        Returns:
            List of up to top_n ticker strings sorted by descending avg
            dollar volume, all exceeding min_volume_usd.
        """
        if volume_data.empty:
            logger.warning("filter_universe received empty volume_data")
            return []

        if price_data is not None:
            # Align columns
            common = volume_data.columns.intersection(price_data.columns)
            dollar_volume = volume_data[common] * price_data[common]
        else:
            dollar_volume = volume_data

        avg_dv = dollar_volume.mean(axis=0).dropna()

        # Apply minimum threshold
        qualified = avg_dv[avg_dv >= self.min_volume_usd]

        # Sort descending and take top_n
        top = qualified.sort_values(ascending=False).head(self.top_n)

        result = list(top.index)
        logger.info(
            "filter_universe: %d/%d tickers pass (min_vol=%.0f, top_n=%d)",
            len(result),
            len(avg_dv),
            self.min_volume_usd,
            self.top_n,
        )
        return result

    # ------------------------------------------------------------------
    # Earnings calendar
    # ------------------------------------------------------------------

    def get_earnings_calendar(
        self,
        tickers: List[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Retrieve earnings calendar for a list of tickers.

        Attempts to use yfinance. Returns a DataFrame with columns:
            ticker, earnings_date, eps_estimate, eps_actual, surprise_pct

        If yfinance is unavailable, returns an empty DataFrame with the
        correct schema.

        Args:
            tickers: List of stock ticker symbols.
            start: Start date for calendar window.
            end: End date for calendar window.

        Returns:
            DataFrame with earnings events in the date range.
        """
        columns = [
            "ticker",
            "earnings_date",
            "eps_estimate",
            "eps_actual",
            "surprise_pct",
        ]
        records: List[Dict[str, Any]] = []

        try:
            import yfinance as yf
        except ImportError:
            logger.warning(
                "yfinance not installed -- returning empty earnings calendar"
            )
            return pd.DataFrame(columns=columns)

        for ticker in tickers:
            try:
                tk = yf.Ticker(ticker)
                cal = getattr(tk, "earnings_dates", None)
                if cal is None or (isinstance(cal, pd.DataFrame) and cal.empty):
                    continue

                # yfinance earnings_dates: index is datetime, columns vary
                for dt_idx, row in cal.iterrows():
                    try:
                        event_date = pd.Timestamp(dt_idx).date()
                    except Exception:
                        continue

                    if not (start <= event_date <= end):
                        continue

                    eps_est = row.get("EPS Estimate", None)
                    eps_act = row.get("Reported EPS", None)
                    surprise = row.get("Surprise(%)", None)

                    # Compute surprise if we have estimate and actual
                    if (
                        surprise is None
                        and eps_est is not None
                        and eps_act is not None
                        and eps_est != 0
                    ):
                        try:
                            surprise = (
                                (float(eps_act) - float(eps_est))
                                / abs(float(eps_est))
                                * 100.0
                            )
                        except (ValueError, ZeroDivisionError):
                            surprise = None

                    records.append(
                        {
                            "ticker": ticker,
                            "earnings_date": event_date,
                            "eps_estimate": _safe_float(eps_est),
                            "eps_actual": _safe_float(eps_act),
                            "surprise_pct": _safe_float(surprise),
                        }
                    )
            except Exception as exc:
                logger.debug("Earnings fetch failed for %s: %s", ticker, exc)
                continue

        df = pd.DataFrame(records, columns=columns)
        if not df.empty:
            df = df.sort_values("earnings_date").reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # Survivorship bias detection
    # ------------------------------------------------------------------

    def check_survivorship_bias(
        self,
        tickers: List[str],
        start_date: date,
        price_data: pd.DataFrame | None = None,
    ) -> SurvivorshipResult:
        """Verify that tickers existed at start_date.

        If price_data is provided, checks that each ticker has valid data
        on or before start_date. Otherwise, uses yfinance to check history
        availability. Falls back to accepting all tickers if data is
        unavailable.

        Args:
            tickers: List of ticker symbols to validate.
            start_date: Date at which all tickers must have been trading.
            price_data: Optional DataFrame with tickers as columns and
                DatetimeIndex. Used to verify data existence at start_date.

        Returns:
            SurvivorshipResult with clean_tickers, removed, and warnings.
        """
        result = SurvivorshipResult()

        if price_data is not None:
            start_ts = pd.Timestamp(start_date, tz="UTC")
            for ticker in tickers:
                if ticker not in price_data.columns:
                    result.removed.append(ticker)
                    result.warnings.append(
                        f"{ticker}: no price data column found"
                    )
                    continue

                series = price_data[ticker].dropna()
                if series.empty:
                    result.removed.append(ticker)
                    result.warnings.append(f"{ticker}: all NaN price data")
                    continue

                first_valid = series.index[0]
                # Normalize timezone for comparison
                if first_valid.tzinfo is None:
                    first_valid = first_valid.tz_localize("UTC")
                if start_ts.tzinfo is None:
                    start_ts = start_ts.tz_localize("UTC")

                if first_valid > start_ts:
                    result.removed.append(ticker)
                    result.warnings.append(
                        f"{ticker}: first data {first_valid.date()} "
                        f"after start_date {start_date}"
                    )
                else:
                    result.clean_tickers.append(ticker)
        else:
            # No price data -- try yfinance, fallback to accepting all
            try:
                import yfinance as yf

                for ticker in tickers:
                    try:
                        tk = yf.Ticker(ticker)
                        hist = tk.history(
                            start=str(start_date),
                            end=str(start_date + pd.Timedelta(days=7)),
                        )
                        if hist.empty:
                            result.removed.append(ticker)
                            result.warnings.append(
                                f"{ticker}: no yfinance data at {start_date}"
                            )
                        else:
                            result.clean_tickers.append(ticker)
                    except Exception as exc:
                        result.warnings.append(
                            f"{ticker}: yfinance check failed ({exc})"
                        )
                        result.clean_tickers.append(ticker)
            except ImportError:
                logger.warning(
                    "yfinance not installed -- accepting all tickers "
                    "without survivorship check"
                )
                result.clean_tickers = list(tickers)
                result.warnings.append(
                    "yfinance unavailable: no survivorship check performed"
                )

        logger.info(
            "Survivorship check: %d clean, %d removed",
            len(result.clean_tickers),
            len(result.removed),
        )
        return result

    # ------------------------------------------------------------------
    # Currency normalization
    # ------------------------------------------------------------------

    def normalize_currency(
        self,
        pnl: float,
        currency: str,
        target: str = "USD",
    ) -> float:
        """Convert PnL from one currency to target currency.

        Uses stored FX rates. If direct rate is unavailable, attempts
        cross via USD.

        Args:
            pnl: Profit/loss amount in source currency.
            currency: Source currency code (e.g. "EUR", "JPY", "GBP").
            target: Target currency code (default "USD").

        Returns:
            PnL converted to target currency.

        Raises:
            ValueError: If conversion rate is unavailable.
        """
        currency = currency.upper()
        target = target.upper()

        if currency == target:
            return pnl

        # Direct: source -> USD -> target
        src_to_usd = self._fx_rates.get(currency)
        tgt_to_usd = self._fx_rates.get(target)

        if src_to_usd is None:
            raise ValueError(
                f"No FX rate for {currency}. "
                f"Available: {list(self._fx_rates.keys())}"
            )
        if tgt_to_usd is None:
            raise ValueError(
                f"No FX rate for {target}. "
                f"Available: {list(self._fx_rates.keys())}"
            )

        # pnl_in_usd = pnl * (source/USD rate)
        # pnl_in_target = pnl_in_usd / (target/USD rate)
        return pnl * src_to_usd / tgt_to_usd

    def update_fx_rates(self, rates: Dict[str, float]) -> None:
        """Update FX rates used for currency normalization.

        Args:
            rates: Mapping of currency code -> USD rate.
        """
        self._fx_rates.update(rates)


def _safe_float(val: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        result = float(val)
        if np.isnan(result):
            return None
        return result
    except (ValueError, TypeError):
        return None
