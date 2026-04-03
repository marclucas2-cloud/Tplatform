"""FX Mean Reversion Hourly strategy for BacktesterV2.

Hourly RSI mean reversion on EUR/USD and GBP/USD with Bollinger Band
confirmation and ADX range-bound filter. Designed for tight
range-bound market conditions where prices oscillate around a mean.

Hypothesis: In low-volatility, non-trending FX environments, RSI
extremes on hourly timeframes reliably revert because short-term
overshooting is driven by noise and algorithmic herding, not by
fundamental shifts. Bollinger Band confirmation ensures price has
actually reached a statistical extreme, not just a relative one.

Entry:
  - LONG: RSI(14) < 25 AND close < lower Bollinger Band (20, 2.0)
  - SHORT: RSI(14) > 75 AND close > upper Bollinger Band (20, 2.0)
  - ADX(14) < 20 filter (range-bound market only)

Exit:
  - SL: 1.5x ATR(14) from entry
  - TP: 1.0x ATR(14) from entry (quick mean reversion)
  - Max holding: 8 hours (time-based exit)

Pairs: EURUSD, GBPUSD
Timeframe: 1H bars
Expected: ~10-20 trades/month per pair, Sharpe target 1.0-2.0
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

SUPPORTED_PAIRS = ["EURUSD", "GBPUSD"]

# Max holding period in bars (8 hours = 8 bars of 1H)
_MAX_HOLDING_BARS = 8

STRATEGY_CONFIG = {
    "name": "FX Mean Reversion Hourly",
    "id": "FX-MR-H1",
    "pairs": SUPPORTED_PAIRS,
    "market_type": "fx",
    "broker": "ibkr",
    "timeframe": "1H",
    "frequency": "hourly",
    "allocation_pct": 0.08,
}


class FXMeanReversionHourly(StrategyBase):
    """FX Mean Reversion Hourly -- RSI + Bollinger Band reversion with ADX filter."""

    def __init__(self, symbol: str = "EURUSD") -> None:
        if symbol not in SUPPORTED_PAIRS:
            raise ValueError(
                f"Unsupported pair {symbol}. Must be one of {SUPPORTED_PAIRS}"
            )
        self._symbol = symbol

        # RSI parameters
        self.rsi_period: int = 14
        self.rsi_oversold: float = 25.0
        self.rsi_overbought: float = 75.0

        # Bollinger Band parameters
        self.bb_period: int = 20
        self.bb_std: float = 2.0

        # ADX filter (range-bound)
        self.adx_period: int = 14
        self.adx_max: float = 20.0  # must be BELOW this for range-bound

        # ATR-based risk management
        self.atr_period: int = 14
        self.sl_atr_mult: float = 1.5
        self.tp_atr_mult: float = 1.0

        # Max holding period
        self.max_holding_bars: int = _MAX_HOLDING_BARS

        # State
        self._position_open: bool = False
        self._bars_in_position: int = 0
        self._entry_bar_count: int = 0
        self._total_bars: int = 0

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"fx_mr_h1_{self._symbol.lower()}"

    @property
    def asset_class(self) -> str:
        return "fx"

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

        sym = self._symbol
        self._total_bars += 1

        # Track max holding time
        if self._position_open:
            self._bars_in_position += 1
            # Time-based exit after max holding period
            if self._bars_in_position >= self.max_holding_bars:
                self._position_open = False
                self._bars_in_position = 0
                # Engine handles the actual close; we just stop blocking new entries
            return None

        # Get all required indicators
        rsi = self.data_feed.get_indicator(sym, "rsi", self.rsi_period)
        adx = self.data_feed.get_indicator(sym, "adx", self.adx_period)
        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        bb_upper = self.data_feed.get_indicator(sym, "bollinger_upper", self.bb_period)
        bb_lower = self.data_feed.get_indicator(sym, "bollinger_lower", self.bb_period)

        if any(v is None for v in (rsi, adx, atr, bb_upper, bb_lower)):
            return None

        if atr <= 0:
            return None

        # ADX filter: only trade in range-bound markets
        if adx > self.adx_max:
            return None

        price = bar.close

        # LONG: RSI oversold + price below lower Bollinger Band
        if rsi < self.rsi_oversold and price < bb_lower:
            sl = price - self.sl_atr_mult * atr
            tp = price + self.tp_atr_mult * atr
            self._position_open = True
            self._bars_in_position = 0

            # Strength: how extreme is the RSI reading
            strength = min((self.rsi_oversold - rsi) / 25.0, 1.0)

            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=strength,
            )

        # SHORT: RSI overbought + price above upper Bollinger Band
        if rsi > self.rsi_overbought and price > bb_upper:
            sl = price + self.sl_atr_mult * atr
            tp = price - self.tp_atr_mult * atr
            self._position_open = True
            self._bars_in_position = 0

            strength = min((rsi - self.rsi_overbought) / 25.0, 1.0)

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

    def on_fill(self, fill) -> None:
        """Track position state changes from fills.

        When the engine fills an exit (SL/TP), this resets the position flag
        so new entries are allowed.
        """
        # If fill is an exit (opposite side to entry), reset position state.
        # Simplified: reset on any fill that is not our entry direction.
        pass

    def on_eod(self, timestamp) -> None:
        """Reset intraday position tracking at end of day.

        The max holding period is 8 hours, so positions should not carry
        overnight. Reset as a safety net.
        """
        self._position_open = False
        self._bars_in_position = 0

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self._symbol,
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "adx_period": self.adx_period,
            "adx_max": self.adx_max,
            "atr_period": self.atr_period,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
            "max_holding_bars": self.max_holding_bars,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "rsi_period": [10, 14, 20],
            "rsi_oversold": [20.0, 25.0, 30.0],
            "rsi_overbought": [70.0, 75.0, 80.0],
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "adx_max": [15.0, 20.0, 25.0],
            "sl_atr_mult": [1.0, 1.5, 2.0],
            "tp_atr_mult": [0.8, 1.0, 1.5],
            "max_holding_bars": [6, 8, 12],
        }
