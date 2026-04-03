"""EU FTSE 100 ATR Mean Reversion strategy for BacktesterV2.

Mean reversion on the FTSE 100 index using ATR-based overbought/oversold
detection. Same structural pattern as existing DAX/CAC mean reversion
strategies, adapted for UK market hours (08:00-16:30 GMT).

Hypothesis: FTSE 100 exhibits strong intraday mean reversion because the
index is dominated by defensive mega-caps (mining, pharma, energy) that
attract systematic rebalancing flows. Deviations from the daily VWAP
revert faster than on growth-heavy indices.

Entry:
  - Price moves > atr_entry_mult * ATR(14) from session open -> fade the move
  - RSI(14) confirmation: < 30 for long, > 70 for short
  - Session: 08:00-16:30 GMT (UK market hours)

Exit:
  - SL: atr_sl_mult * ATR(14) from entry
  - TP: atr_tp_mult * ATR(14) from entry (reversion to mean)
  - Time exit: 16:30 GMT if neither SL nor TP hit (close before auction)

Symbol: "FTSE100" (or IBKR "Z" futures contract)
Timeframe: 1H bars
Expected: ~10-15 trades/month, Sharpe target 1.0-1.8
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

# UK session hours (GMT/UTC)
_SESSION_START_HOUR = 8
_SESSION_END_HOUR = 16   # exit at 16:30, so last entry at 16:00
_EXIT_HOUR = 16

STRATEGY_CONFIG = {
    "name": "EU FTSE Mean Reversion",
    "id": "EU-FTSE-MR",
    "symbols": ["FTSE100"],
    "market_type": "eu_equity",
    "broker": "ibkr",
    "timeframe": "1H",
    "frequency": "intraday",
    "allocation_pct": 0.08,
}


class EUFTSEMeanReversion(StrategyBase):
    """EU FTSE 100 ATR Mean Reversion -- intraday fade of ATR-sized moves."""

    SYMBOL = "FTSE100"

    def __init__(self) -> None:
        # Parameters (tunable via WF)
        self.atr_period: int = 14
        self.atr_entry_mult: float = 1.5   # entry: price moved > 1.5x ATR from open
        self.atr_sl_mult: float = 2.0      # stop loss: 2x ATR from entry
        self.atr_tp_mult: float = 1.0      # take profit: 1x ATR (revert to mean)
        self.rsi_period: int = 14
        self.rsi_oversold: float = 30.0
        self.rsi_overbought: float = 70.0
        self.close_eod: bool = True

        # State
        self._session_open_price: float | None = None
        self._session_open_date: str | None = None
        self._traded_today: bool = False

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "eu_ftse_mean_reversion"

    @property
    def asset_class(self) -> str:
        return "eu_equity"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        sym = self.SYMBOL
        hour = bar.timestamp.hour
        bar_date = str(bar.timestamp.date())

        # Track session open price
        if bar_date != self._session_open_date and hour >= _SESSION_START_HOUR:
            self._session_open_price = bar.open
            self._session_open_date = bar_date
            self._traded_today = False

        # Only trade during UK session
        if hour < _SESSION_START_HOUR or hour >= _SESSION_END_HOUR:
            return None

        if self._traded_today:
            return None

        if self._session_open_price is None:
            return None

        # Get indicators
        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        rsi = self.data_feed.get_indicator(sym, "rsi", self.rsi_period)

        if atr is None or rsi is None or atr <= 0:
            return None

        # Calculate distance from session open
        distance = bar.close - self._session_open_price
        distance_in_atr = abs(distance) / atr

        # Need sufficient move to trigger entry
        if distance_in_atr < self.atr_entry_mult:
            return None

        price = bar.close

        # LONG: price dropped significantly from open + RSI oversold
        if distance < 0 and rsi < self.rsi_oversold:
            sl = price - self.atr_sl_mult * atr
            tp = price + self.atr_tp_mult * atr
            self._traded_today = True
            strength = min(distance_in_atr / 3.0, 1.0)

            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=strength,
            )

        # SHORT: price rose significantly from open + RSI overbought
        if distance > 0 and rsi > self.rsi_overbought:
            sl = price + self.atr_sl_mult * atr
            tp = price - self.atr_tp_mult * atr
            self._traded_today = True
            strength = min(distance_in_atr / 3.0, 1.0)

            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=strength,
            )

        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_eod(self, timestamp) -> None:
        """Reset daily state."""
        self._traded_today = False
        self._session_open_price = None
        self._session_open_date = None

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "atr_period": self.atr_period,
            "atr_entry_mult": self.atr_entry_mult,
            "atr_sl_mult": self.atr_sl_mult,
            "atr_tp_mult": self.atr_tp_mult,
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "close_eod": self.close_eod,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "atr_period": [10, 14, 20],
            "atr_entry_mult": [1.0, 1.5, 2.0],
            "atr_sl_mult": [1.5, 2.0, 2.5],
            "atr_tp_mult": [0.8, 1.0, 1.5],
            "rsi_oversold": [25.0, 30.0, 35.0],
            "rsi_overbought": [65.0, 70.0, 75.0],
        }
