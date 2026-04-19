"""FX-011 End-of-Month Flow Rebalancing strategy for BacktesterV2.

Pension funds and corporates rebalance their FX hedges at month-end (last 3
trading days to first trading day of the new month). USD tends to be sold when
US equities had a positive month (hedging gains) and bought when negative
(covering losses). This creates predictable flow patterns.

Entry (last 3 trading days of month + first day of next month):
  1. Detect month-end window: last 3 trading days of the calendar month OR
     the first trading day of the next month.
  2. Calculate monthly equity performance: SPY return over the month.
  3. If SPY monthly return > +1%: expect USD selling -> LONG EUR/USD, GBP/USD;
     SHORT USD/JPY.
  4. If SPY monthly return < -1%: expect USD buying -> SHORT EUR/USD, GBP/USD;
     LONG USD/JPY.
  5. Filter: skip if abs(monthly_return) < 1% (weak signal).

Exit:
  - Time exit: close on 2nd trading day of new month (~5 days max hold).
  - Stop loss: 1.5x ATR(14, daily) from entry.
  - Take profit: 2x ATR(14, daily) from entry.

Pairs: EUR/USD, GBP/USD, USD/JPY.
Cost: ~$2/trade IBKR, spread ~0.8-2.0 bps.
Expected: ~4 trades/month (3 pairs x ~1.3 signals), Sharpe target 0.8-1.5.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, Dict, List


from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

# Pairs with their USD direction:
#   EUR/USD, GBP/USD: USD is quote -> LONG = sell USD, SHORT = buy USD
#   USD/JPY: USD is base -> SHORT = sell USD, LONG = buy USD
_PAIRS_USD_QUOTE = ("EURUSD", "GBPUSD")  # long = sell USD
_PAIRS_USD_BASE = ("USDJPY",)              # short = sell USD

SUPPORTED_PAIRS = _PAIRS_USD_QUOTE + _PAIRS_USD_BASE

# Minimum absolute monthly SPY return to trigger a signal
_MIN_MONTHLY_RETURN_PCT = 1.0  # 1%

# Month-end window: last N trading days of month
_EOM_WINDOW_DAYS = 3

# Time exit: close on the Nth trading day of the new month
_EXIT_TRADING_DAY = 2  # 2nd trading day


class FXEOMFlow(StrategyBase):
    """End-of-Month flow rebalancing: trade FX hedging flow around month-end."""

    def __init__(self) -> None:
        self.atr_period: int = 14
        self.sl_atr_mult: float = 1.5  # SL = 1.5x ATR from entry
        self.tp_atr_mult: float = 2.0  # TP = 2x ATR from entry
        self.min_monthly_return: float = _MIN_MONTHLY_RETURN_PCT  # % threshold
        self.eom_window_days: int = _EOM_WINDOW_DAYS
        self.exit_trading_day: int = _EXIT_TRADING_DAY
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "fx_eom_flow"

    @property
    def asset_class(self) -> str:
        return "fx"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    # ------------------------------------------------------------------
    # Month-end window detection
    # ------------------------------------------------------------------

    @staticmethod
    def _last_n_trading_days_of_month(
        year: int, month: int, n: int = 3
    ) -> List[date]:
        """Return the last N weekday dates of the given month.

        This is a simplified business-day calendar that excludes weekends
        but not holidays. Good enough for FX which trades Mon-Fri.
        """
        last_day = calendar.monthrange(year, month)[1]
        days: List[date] = []
        d = date(year, month, last_day)
        while len(days) < n:
            if d.weekday() < 5:  # Mon=0 .. Fri=4
                days.append(d)
            d -= timedelta(days=1)
        days.reverse()
        return days

    @staticmethod
    def _first_n_trading_days_of_month(
        year: int, month: int, n: int = 1
    ) -> List[date]:
        """Return the first N weekday dates of the given month."""
        days: List[date] = []
        d = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        while len(days) < n and d.day <= last_day:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    def is_eom_window(self, bar_date: date) -> bool:
        """Check if bar_date falls in the month-end rebalancing window.

        The window includes:
          - Last `eom_window_days` trading days of the current month, OR
          - First trading day of the next month.
        """
        year, month = bar_date.year, bar_date.month

        # Check: is bar_date in last N trading days of its month?
        last_days = self._last_n_trading_days_of_month(
            year, month, self.eom_window_days
        )
        if bar_date in last_days:
            return True

        # Check: is bar_date the 1st trading day of its month?
        first_days = self._first_n_trading_days_of_month(year, month, n=1)
        if first_days and bar_date == first_days[0]:
            return True

        return False

    def is_exit_day(self, bar_date: date) -> bool:
        """Check if bar_date is the time-exit day (2nd trading day of month)."""
        year, month = bar_date.year, bar_date.month
        first_days = self._first_n_trading_days_of_month(
            year, month, n=self.exit_trading_day
        )
        if len(first_days) >= self.exit_trading_day:
            return bar_date == first_days[-1]
        return False

    # ------------------------------------------------------------------
    # Monthly SPY return computation
    # ------------------------------------------------------------------

    def _get_monthly_spy_return(self) -> float | None:
        """Compute SPY's return for the current (or most recent) month.

        Uses daily close data from the data feed. Returns the percentage
        return (e.g. 2.5 for +2.5%), or None if data is unavailable.
        """
        if self.data_feed is None:
            return None

        # Try SPY data — may not be available in pure FX datasets
        try:
            bars_df = self.data_feed.get_bars("SPY", 40)
        except KeyError:
            return None

        if bars_df is None or len(bars_df) < 20:
            return None

        # Get first and last close of the most recent ~22 trading days
        closes = bars_df["close"].values.astype(float)
        month_return_pct = ((closes[-1] / closes[0]) - 1.0) * 100.0
        return float(month_return_pct)

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        # Convert bar timestamp to a date
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        bar_date = ts.date()

        # Only generate signals in the EOM window
        if not self.is_eom_window(bar_date):
            return None

        # Get monthly SPY return
        spy_return = self._get_monthly_spy_return()
        if spy_return is None:
            return None

        # Filter: skip weak signals
        if abs(spy_return) < self.min_monthly_return:
            return None

        # Determine USD direction
        usd_selling = spy_return > self.min_monthly_return
        usd_buying = spy_return < -self.min_monthly_return

        if not (usd_selling or usd_buying):
            return None

        # Try each pair
        for sym in SUPPORTED_PAIRS:
            signal = self._evaluate_pair(sym, bar, usd_selling)
            if signal is not None:
                return signal

        return None

    def _evaluate_pair(
        self, sym: str, bar: Bar, usd_selling: bool
    ) -> Signal | None:
        """Generate a signal for a single FX pair based on USD flow direction."""
        if self.data_feed is None:
            return None

        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        if atr is None or atr <= 0:
            return None

        sl_distance = self.sl_atr_mult * atr
        tp_distance = self.tp_atr_mult * atr

        # Determine side based on pair type and USD direction
        if sym in _PAIRS_USD_QUOTE:
            # EUR/USD, GBP/USD: long = sell USD
            if usd_selling:
                return Signal(
                    symbol=sym,
                    side="BUY",
                    strategy_name=self.name,
                    stop_loss=bar.close - sl_distance,
                    take_profit=bar.close + tp_distance,
                    strength=0.7,
                )
            else:
                return Signal(
                    symbol=sym,
                    side="SELL",
                    strategy_name=self.name,
                    stop_loss=bar.close + sl_distance,
                    take_profit=bar.close - tp_distance,
                    strength=0.7,
                )
        elif sym in _PAIRS_USD_BASE:
            # USD/JPY: short = sell USD
            if usd_selling:
                return Signal(
                    symbol=sym,
                    side="SELL",
                    strategy_name=self.name,
                    stop_loss=bar.close + sl_distance,
                    take_profit=bar.close - tp_distance,
                    strength=0.7,
                )
            else:
                return Signal(
                    symbol=sym,
                    side="BUY",
                    strategy_name=self.name,
                    stop_loss=bar.close - sl_distance,
                    take_profit=bar.close + tp_distance,
                    strength=0.7,
                )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "atr_period": self.atr_period,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
            "min_monthly_return": self.min_monthly_return,
            "eom_window_days": self.eom_window_days,
            "exit_trading_day": self.exit_trading_day,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "atr_period": [10, 14, 20],
            "sl_atr_mult": [1.0, 1.5, 2.0],
            "tp_atr_mult": [1.5, 2.0, 2.5],
            "min_monthly_return": [0.5, 1.0, 1.5],
            "eom_window_days": [2, 3, 4],
            "exit_trading_day": [1, 2, 3],
        }
