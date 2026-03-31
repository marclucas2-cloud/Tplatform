"""EU Mean Reversion DAX strategy for BacktesterV2.

Hypothesis: During active Frankfurt sessions, when DAX extends beyond 2x ATR(14)
from its VWAP, institutional rebalancing and profit-taking pull price back to the
mean. The DAX, being Europe's most liquid index, exhibits strong mean-reverting
behavior during high-extension episodes.

Entry rules:
  1. Session filter: only trade 09:30-17:00 CET (08:30-16:00 UTC)
  2. Compute VWAP from session start using cumulative (price * volume) / volume
  3. Compute ATR(14) on closed bars
  4. LONG if price < VWAP - atr_entry_mult * ATR (oversold extension)
  5. SHORT if price > VWAP + atr_entry_mult * ATR (overbought extension)
  6. One trade per session maximum

Exit rules:
  - Take profit: price returns to VWAP (mean reversion target)
  - Stop loss: price extends further — 3x ATR from VWAP
  - Time exit: EOD (17:00 CET)

Symbol: DAX (Frankfurt)
Timeframe: 15min or 1H bars
Expected: ~8-12 trades/month, win rate 55-65%, Sharpe 1.0-2.0
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


# Frankfurt session in UTC: 08:30-16:00 (= 09:30-17:00 CET)
_SESSION_START_HOUR_UTC = 8
_SESSION_START_MIN_UTC = 30
_SESSION_END_HOUR_UTC = 16

STRATEGY_CONFIG = {
    "name": "eu_mean_reversion_dax",
    "id": "EU-MR-DAX",
    "market_type": "eu_equity",
    "broker": "ibkr",
    "timeframe": "1H",
}


class EUMeanReversionDAX(StrategyBase):
    """ATR extension mean reversion on DAX — fade overextensions from VWAP."""

    SYMBOL = "DAX"

    def __init__(self) -> None:
        # ATR parameters
        self.atr_period: int = 14
        self.atr_entry_mult: float = 2.0
        self.atr_sl_mult: float = 3.0

        # VWAP lookback (bars from session start for VWAP computation)
        self.vwap_lookback: int = 24

        # State tracking
        self._position_open: bool = False
        self._session_date: Optional[str] = None

        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "eu_mean_reversion_dax"

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
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None

        sym = self.SYMBOL
        hour = bar.timestamp.hour
        minute = bar.timestamp.minute if hasattr(bar.timestamp, "minute") else 0

        # Reset daily state
        bar_date = str(bar.timestamp.date())
        if bar_date != self._session_date:
            self._session_date = bar_date
            self._position_open = False

        # Session filter: 08:30-16:00 UTC (09:30-17:00 CET)
        bar_minutes = hour * 60 + minute
        if bar_minutes < _SESSION_START_HOUR_UTC * 60 + _SESSION_START_MIN_UTC:
            return None
        if hour >= _SESSION_END_HOUR_UTC:
            return None

        # Already traded today
        if self._position_open:
            return None

        # Get ATR
        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Compute VWAP from recent session bars
        vwap = self._compute_vwap(sym)
        if vwap is None or vwap <= 0:
            return None

        extension = bar.close - vwap

        # LONG: price overextended below VWAP
        if extension < -self.atr_entry_mult * atr:
            sl = vwap - self.atr_sl_mult * atr
            tp = vwap  # Target: return to VWAP
            self._position_open = True
            strength = min(abs(extension) / (self.atr_sl_mult * atr), 1.0)
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=strength,
            )

        # SHORT: price overextended above VWAP
        if extension > self.atr_entry_mult * atr:
            sl = vwap + self.atr_sl_mult * atr
            tp = vwap  # Target: return to VWAP
            self._position_open = True
            strength = min(abs(extension) / (self.atr_sl_mult * atr), 1.0)
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
    # VWAP computation
    # ------------------------------------------------------------------

    def _compute_vwap(self, symbol: str) -> Optional[float]:
        """Compute volume-weighted average price from recent session bars.

        Uses get_bars() to fetch recent bars and computes cumulative VWAP.
        Anti-lookahead: DataFeed only returns closed bars.
        """
        if self.data_feed is None:
            return None

        bars_df = self.data_feed.get_bars(symbol, self.vwap_lookback)
        if bars_df.empty or "volume" not in bars_df.columns:
            return None

        # Typical price * volume for VWAP
        typical_price = (bars_df["high"] + bars_df["low"] + bars_df["close"]) / 3.0
        cum_tp_vol = (typical_price * bars_df["volume"]).sum()
        cum_vol = bars_df["volume"].sum()

        if cum_vol <= 0:
            return None

        return float(cum_tp_vol / cum_vol)

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "atr_period": self.atr_period,
            "atr_entry_mult": self.atr_entry_mult,
            "atr_sl_mult": self.atr_sl_mult,
            "vwap_lookback": self.vwap_lookback,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "atr_period": [10, 14, 20],
            "atr_entry_mult": [1.5, 2.0, 2.5],
            "atr_sl_mult": [2.5, 3.0, 3.5],
            "vwap_lookback": [16, 24, 32],
        }

    def on_eod(self, timestamp) -> None:
        """Reset daily state at end of day."""
        self._position_open = False
        self._session_date = None
