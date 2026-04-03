"""Post-Earnings Announcement Drift (PEAD) strategy for BacktesterV2.

Academic edge: stocks tend to drift in the direction of earnings surprise
for 3-10 days after the announcement. This strategy captures that drift
with strict risk controls.

Entry rules:
  - LONG if earnings surprise > +5% AND gap-up on report day
  - SHORT if earnings surprise < -5% AND gap-down on report day
  - Volatility crush filter: skip if implied vol dropped > 30% after report

Exit rules:
  - Time-based: close after hold_period_days (default 5, tunable 3-10)
  - Stop-loss: 3% from entry
  - Take-profit: 6% from entry

Position limits:
  - Max 3 concurrent PEAD positions

Priority tickers: ASML, LVMH, SAP (EU), NVDA, AAPL, MSFT (US)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List


from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, Fill, PortfolioState, Signal

logger = logging.getLogger(__name__)


# -- Strategy configuration --

STRATEGY_CONFIG = {
    "name": "earnings_drift",
    "asset_class": "equity",
    "broker": "alpaca",
    "description": "Post-Earnings Announcement Drift (PEAD)",
    "priority_tickers": ["NVDA", "AAPL", "MSFT", "ASML", "LVMH", "SAP"],
    "version": "1.0.0",
}

# Tickers this strategy focuses on (configurable via set_universe)
DEFAULT_TICKERS = [
    # US mega-cap tech
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NFLX",
    # EU large-cap
    "ASML", "SAP",
    # US diversified
    "JPM", "GS", "BAC",
]

# LVMH requires special handling (ticker MC.PA on EU exchanges)
EU_TICKER_MAP = {
    "LVMH": "MC.PA",
}


@dataclass
class ActivePosition:
    """Tracks a PEAD position for time-based exit."""

    ticker: str
    side: str  # "BUY" or "SELL"
    entry_price: float
    entry_date: date
    hold_days: int = 0


class EarningsDrift(StrategyBase):
    """Post-Earnings Announcement Drift strategy.

    Uses earnings surprise data to enter positions in the direction of
    the surprise, then holds for a configurable number of days.
    """

    def __init__(self) -> None:
        # -- Tunable parameters --
        self.surprise_threshold_pct: float = 5.0
        self.hold_period_days: int = 5
        self.sl_pct: float = 3.0
        self.tp_pct: float = 6.0
        self.max_concurrent: int = 3
        self.vol_crush_threshold: float = 30.0
        self.min_gap_pct: float = 0.5  # minimum gap to confirm direction

        # -- Internal state --
        self._data_feed: DataFeed | None = None
        self._active_positions: Dict[str, ActivePosition] = {}
        self._earnings_data: Dict[str, Dict[str, Any]] = {}
        self._universe: List[str] = list(DEFAULT_TICKERS)
        self._vol_data: Dict[str, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # StrategyBase interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "earnings_drift"

    @property
    def asset_class(self) -> str:
        return "equity"

    @property
    def broker(self) -> str:
        return "alpaca"

    def set_data_feed(self, feed: DataFeed) -> None:
        """Attach the DataFeed for accessing historical bars."""
        self._data_feed = feed

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        """Process a bar and generate PEAD signals.

        Logic:
          1. Check if any active position needs time-exit
          2. If ticker had earnings recently, check surprise + gap
          3. Apply volatility crush filter
          4. Emit signal if all conditions met

        Args:
            bar: Latest closed OHLCV bar.
            portfolio_state: Current portfolio state.

        Returns:
            Signal to enter or exit, or None.
        """
        ticker = bar.symbol
        bar_date = bar.timestamp.date() if hasattr(bar.timestamp, "date") else bar.timestamp

        # -- Check time-based exit for existing positions --
        if ticker in self._active_positions:
            pos = self._active_positions[ticker]
            pos.hold_days += 1

            if pos.hold_days >= self.hold_period_days:
                exit_side = "SELL" if pos.side == "BUY" else "BUY"
                del self._active_positions[ticker]
                logger.info(
                    "PEAD time-exit: %s %s after %d days",
                    exit_side, ticker, pos.hold_days,
                )
                return Signal(
                    symbol=ticker,
                    side=exit_side,
                    strategy_name=self.name,
                    order_type="MARKET",
                    strength=0.5,
                )

        # -- Don't open new positions if at max --
        if len(self._active_positions) >= self.max_concurrent:
            return None

        # -- Check if this ticker is in our universe --
        if ticker not in self._universe:
            return None

        # -- Check for earnings event --
        earnings = self._get_earnings_for_ticker(ticker, bar_date)
        if earnings is None:
            return None

        surprise_pct = earnings.get("surprise_pct")
        if surprise_pct is None:
            return None

        # -- Check surprise threshold --
        if abs(surprise_pct) < self.surprise_threshold_pct:
            return None

        # -- Check gap direction confirms surprise --
        gap_pct = self._compute_gap_pct(bar)
        if gap_pct is None:
            return None

        is_positive_surprise = surprise_pct > self.surprise_threshold_pct
        is_negative_surprise = surprise_pct < -self.surprise_threshold_pct

        # Gap must confirm direction
        if is_positive_surprise and gap_pct < self.min_gap_pct:
            return None
        if is_negative_surprise and gap_pct > -self.min_gap_pct:
            return None

        # -- Volatility crush filter --
        if self._is_vol_crushed(ticker, bar_date):
            logger.debug(
                "PEAD skip %s: vol crush > %.0f%%",
                ticker, self.vol_crush_threshold,
            )
            return None

        # -- Generate signal --
        if is_positive_surprise:
            sl = bar.close * (1.0 - self.sl_pct / 100.0)
            tp = bar.close * (1.0 + self.tp_pct / 100.0)
            side = "BUY"
        elif is_negative_surprise:
            sl = bar.close * (1.0 + self.sl_pct / 100.0)
            tp = bar.close * (1.0 - self.tp_pct / 100.0)
            side = "SELL"
        else:
            return None

        # Track position
        self._active_positions[ticker] = ActivePosition(
            ticker=ticker,
            side=side,
            entry_price=bar.close,
            entry_date=bar_date,
        )

        strength = min(abs(surprise_pct) / 20.0, 1.0)

        logger.info(
            "PEAD entry: %s %s surprise=%.1f%% gap=%.2f%%",
            side, ticker, surprise_pct, gap_pct,
        )

        return Signal(
            symbol=ticker,
            side=side,
            strategy_name=self.name,
            order_type="MARKET",
            stop_loss=sl,
            take_profit=tp,
            strength=strength,
        )

    def on_fill(self, fill: Fill) -> None:
        """Track fills for position management."""
        ticker = fill.order.symbol
        if fill.rejected and ticker in self._active_positions:
            del self._active_positions[ticker]

    # ------------------------------------------------------------------
    # Parameter management
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "surprise_threshold_pct": self.surprise_threshold_pct,
            "hold_period_days": self.hold_period_days,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "max_concurrent": self.max_concurrent,
            "vol_crush_threshold": self.vol_crush_threshold,
            "min_gap_pct": self.min_gap_pct,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "surprise_threshold_pct": [3.0, 5.0, 7.0, 10.0],
            "hold_period_days": [3, 5, 7, 10],
            "sl_pct": [2.0, 3.0, 4.0],
            "tp_pct": [4.0, 6.0, 8.0],
            "max_concurrent": [2, 3, 5],
            "vol_crush_threshold": [20.0, 30.0, 40.0],
            "min_gap_pct": [0.3, 0.5, 1.0],
        }

    # ------------------------------------------------------------------
    # Earnings data management
    # ------------------------------------------------------------------

    def set_earnings_data(
        self, earnings: Dict[str, Dict[str, Any]]
    ) -> None:
        """Load earnings data for backtesting.

        Args:
            earnings: Nested dict:
                { ticker: { "YYYY-MM-DD": {
                    "surprise_pct": float,
                    "eps_estimate": float,
                    "eps_actual": float,
                }}}
        """
        self._earnings_data = earnings

    def set_universe(self, tickers: List[str]) -> None:
        """Override the default ticker universe.

        Args:
            tickers: List of ticker symbols to trade.
        """
        self._universe = list(tickers)

    def set_vol_data(self, vol_data: Dict[str, Dict[str, float]]) -> None:
        """Load implied volatility data for vol crush filter.

        Args:
            vol_data: { ticker: { "YYYY-MM-DD": implied_vol_pct }}
        """
        self._vol_data = vol_data

    @property
    def active_position_count(self) -> int:
        """Number of currently active PEAD positions."""
        return len(self._active_positions)

    def reset(self) -> None:
        """Reset internal state for a new backtest run."""
        self._active_positions.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_earnings_for_ticker(
        self, ticker: str, bar_date: date
    ) -> Dict[str, Any] | None:
        """Check if ticker has an earnings event on or 1 day before bar_date.

        The drift starts on the report day and the day after. We allow
        entry on the earnings date itself (post-market reported) or the
        next trading day.

        Returns:
            Earnings dict with surprise_pct, or None.
        """
        if ticker not in self._earnings_data:
            return None

        ticker_earnings = self._earnings_data[ticker]

        # Check exact date
        date_str = bar_date.isoformat()
        if date_str in ticker_earnings:
            return ticker_earnings[date_str]

        # Check previous day (for after-market reports)
        prev_date = bar_date - timedelta(days=1)
        prev_str = prev_date.isoformat()
        if prev_str in ticker_earnings:
            return ticker_earnings[prev_str]

        return None

    def _compute_gap_pct(self, bar: Bar) -> float | None:
        """Compute the open-to-previous-close gap percentage.

        Args:
            bar: Current bar with open price.

        Returns:
            Gap percentage, or None if no previous bar available.
        """
        if self._data_feed is None:
            return None

        prev_bars = self._data_feed.get_bars(bar.symbol, 2)
        if len(prev_bars) < 2:
            return None

        prev_close = float(prev_bars["close"].iloc[-2])
        if prev_close == 0:
            return None

        return ((bar.open - prev_close) / prev_close) * 100.0

    def _is_vol_crushed(self, ticker: str, bar_date: date) -> bool:
        """Check if implied vol dropped significantly after earnings.

        Compares IV on bar_date with IV one day prior. If drop exceeds
        vol_crush_threshold, the edge is likely already priced in.

        Returns:
            True if vol crush detected (should skip entry).
        """
        if ticker not in self._vol_data:
            return False

        vol_series = self._vol_data[ticker]
        date_str = bar_date.isoformat()
        prev_str = (bar_date - timedelta(days=1)).isoformat()

        curr_vol = vol_series.get(date_str)
        prev_vol = vol_series.get(prev_str)

        if curr_vol is None or prev_vol is None or prev_vol == 0:
            return False

        drop_pct = ((prev_vol - curr_vol) / prev_vol) * 100.0
        return drop_pct > self.vol_crush_threshold
