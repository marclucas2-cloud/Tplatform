"""FX-007 Asian Range Breakout (ARB) strategy for BacktesterV2.

Hypothesis: FX pairs break out of their Asian session range (00:00-08:00 UTC)
during London open because institutional market makers trigger stops placed
at range extremes.

Entry (London Open 08:00-10:00 UTC):
  1. Calculate Asian range: high/low of 00:00-07:00 UTC bars
  2. Range must be tight: (high - low) / close < ATR(14) * range_filter_mult
  3. LONG if close > high_asian + buffer (0.1 * ATR14)
  4. SHORT if close < low_asian - buffer
  5. ADX(14) daily > adx_threshold (some trend)

Exit:
  - Stop loss: opposite side of Asian range
  - Take profit: 2x risk from entry
  - Time exit: close at 16:00 UTC if neither SL nor TP hit
  - Max holding: 8 hours (intraday only)

Pairs: EUR/USD, GBP/USD, USD/JPY, EUR/GBP
Timeframe: 1H bars
Expected: ~15-20 trades/month across 4 pairs, Sharpe target 1.0-2.0
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

# Asian session: 00:00-07:00 UTC (bars closing at 01:00 through 07:00)
_ASIAN_START_HOUR = 0
_ASIAN_END_HOUR = 7  # inclusive — last Asian bar closes at 07:00

# London open window: 08:00-10:00 UTC (entry allowed)
_LONDON_ENTRY_START = 8
_LONDON_ENTRY_END = 10

# Time exit: 16:00 UTC
_TIME_EXIT_HOUR = 16

# Supported pairs
SUPPORTED_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "EURGBP"]


class FXAsianRangeBreakout(StrategyBase):
    """FX-007 Asian Range Breakout — London open breakout of Asian range."""

    def __init__(self, symbol: str = "EURUSD") -> None:
        if symbol not in SUPPORTED_PAIRS:
            raise ValueError(
                f"Unsupported pair {symbol}. Must be one of {SUPPORTED_PAIRS}"
            )
        self._symbol = symbol

        # Parameters (tunable via WF)
        self.buffer_atr_mult: float = 0.1
        self.range_filter_mult: float = 1.5
        self.adx_threshold: float = 15.0
        self.adx_period: int = 14
        self.atr_period: int = 14
        self.sl_atr_mult: float = 0.5
        self.tp_risk_mult: float = 2.0

        # State tracking
        self._asian_high: float | None = None
        self._asian_low: float | None = None
        self._asian_computed_date: str | None = None
        self._position_open: bool = False
        self._entry_hour: int | None = None

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"fx_arb_{self._symbol.lower()}"

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
        hour = bar.timestamp.hour

        # --- Compute Asian range once per day ---
        bar_date = str(bar.timestamp.date())
        if bar_date != self._asian_computed_date and hour >= _LONDON_ENTRY_START:
            self._compute_asian_range(sym, bar.timestamp)
            self._asian_computed_date = bar_date
            self._position_open = False

        # --- Time exit: emit None but reset state at 16:00 UTC ---
        if hour >= _TIME_EXIT_HOUR:
            self._position_open = False
            return None

        # --- Only enter during London open window ---
        if hour < _LONDON_ENTRY_START or hour > _LONDON_ENTRY_END:
            return None

        # --- Need valid Asian range ---
        if self._asian_high is None or self._asian_low is None:
            return None

        # --- Already have a position today ---
        if self._position_open:
            return None

        # --- Get indicators ---
        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        adx = self.data_feed.get_indicator(sym, "adx", self.adx_period)
        if atr is None or adx is None:
            return None

        # --- ADX filter: need some trend ---
        if adx < self.adx_threshold:
            return None

        # --- Range width filter: skip if range too wide ---
        range_width = self._asian_high - self._asian_low
        if bar.close > 0 and range_width / bar.close >= atr * self.range_filter_mult:
            return None

        buffer = self.buffer_atr_mult * atr

        # --- LONG breakout ---
        if bar.close > self._asian_high + buffer:
            sl = self._asian_low - self.sl_atr_mult * atr
            risk = bar.close - sl
            tp = bar.close + self.tp_risk_mult * risk
            self._position_open = True
            self._entry_hour = hour
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=min((adx - self.adx_threshold) / 30.0, 1.0),
            )

        # --- SHORT breakout ---
        if bar.close < self._asian_low - buffer:
            sl = self._asian_high + self.sl_atr_mult * atr
            risk = sl - bar.close
            tp = bar.close - self.tp_risk_mult * risk
            self._position_open = True
            self._entry_hour = hour
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=min((adx - self.adx_threshold) / 30.0, 1.0),
            )

        return None

    # ------------------------------------------------------------------
    # Asian range computation
    # ------------------------------------------------------------------

    def _compute_asian_range(
        self, symbol: str, current_ts: pd.Timestamp
    ) -> None:
        """Compute high/low of the Asian session (00:00-07:00 UTC) for today.

        Uses df_feed.get_bars() to fetch recent bars and filters by hour.
        Anti-lookahead: DataFeed only returns closed bars before current_ts.
        """
        if self.data_feed is None:
            self._asian_high = None
            self._asian_low = None
            return

        # Fetch enough bars to cover the Asian session (at least 8 1H bars)
        bars_df = self.data_feed.get_bars(symbol, 24)
        if bars_df.empty:
            self._asian_high = None
            self._asian_low = None
            return

        today = current_ts.date()
        # Filter to Asian hours of today
        asian_mask = (
            (bars_df.index.date == today)
            & (bars_df.index.hour >= _ASIAN_START_HOUR)
            & (bars_df.index.hour <= _ASIAN_END_HOUR)
        )
        asian_bars = bars_df[asian_mask]

        if asian_bars.empty:
            self._asian_high = None
            self._asian_low = None
            return

        self._asian_high = float(asian_bars["high"].max())
        self._asian_low = float(asian_bars["low"].min())

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self._symbol,
            "buffer_atr_mult": self.buffer_atr_mult,
            "range_filter_mult": self.range_filter_mult,
            "adx_threshold": self.adx_threshold,
            "adx_period": self.adx_period,
            "atr_period": self.atr_period,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_risk_mult": self.tp_risk_mult,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "buffer_atr_mult": [0.05, 0.1, 0.15, 0.2],
            "range_filter_mult": [1.0, 1.5, 2.0],
            "adx_threshold": [10.0, 15.0, 20.0],
            "atr_period": [10, 14, 20],
            "sl_atr_mult": [0.3, 0.5, 0.7],
            "tp_risk_mult": [1.5, 2.0, 2.5, 3.0],
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_eod(self, timestamp: pd.Timestamp) -> None:
        """Reset daily state at end of day."""
        self._position_open = False
        self._asian_high = None
        self._asian_low = None
        self._asian_computed_date = None
        self._entry_hour = None
