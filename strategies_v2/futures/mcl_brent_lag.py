"""MCL Brent Lag strategy for BacktesterV2.

Exploits lead-lag relationship between Brent crude and European equity open.
Micro Crude Oil (MCL) on NYMEX, $600 margin, tick size $0.01 = $1.00/tick.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MCLBrentLag(StrategyBase):
    """MCL Brent lead-lag with EU equity open momentum."""

    SYMBOL = "MCL"
    TICK_VALUE = 1.0  # $1.00 per tick ($0.01 move on 100 barrels)

    def __init__(self) -> None:
        self.lag_threshold: float = 0.005
        self.sl_ticks: int = 25
        self.tp_ticks: int = 50
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mcl_brent_lag"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None
        sym = self.SYMBOL

        ema_fast = self.data_feed.get_indicator(sym, "ema", 10)
        ema_slow = self.data_feed.get_indicator(sym, "ema", 30)
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)
        atr = self.data_feed.get_indicator(sym, "atr", 14)

        if any(v is None for v in (ema_fast, ema_slow, rsi, atr)):
            return None

        # Momentum from EMA spread as proxy for lead-lag signal
        spread_pct = (ema_fast - ema_slow) / ema_slow if ema_slow != 0 else 0.0

        tick_size = 0.01
        sl_distance = self.sl_ticks * tick_size
        tp_distance = self.tp_ticks * tick_size

        # Long: strong upward momentum exceeding threshold
        if spread_pct > self.lag_threshold and 40.0 <= rsi <= 70.0:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - sl_distance,
                take_profit=bar.close + tp_distance,
                strength=min(abs(spread_pct) / (self.lag_threshold * 3), 1.0),
            )

        # Short: strong downward momentum
        if spread_pct < -self.lag_threshold and 30.0 <= rsi <= 60.0:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + sl_distance,
                take_profit=bar.close - tp_distance,
                strength=min(abs(spread_pct) / (self.lag_threshold * 3), 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "lag_threshold": self.lag_threshold,
            "sl_ticks": self.sl_ticks,
            "tp_ticks": self.tp_ticks,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "lag_threshold": [0.003, 0.005, 0.007, 0.01],
            "sl_ticks": [15, 20, 25, 30],
            "tp_ticks": [30, 40, 50, 70],
        }
