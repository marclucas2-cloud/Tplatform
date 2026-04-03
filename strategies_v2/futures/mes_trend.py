"""MES Trend Following strategy for BacktesterV2.

Micro E-mini S&P 500 (MES) trend following on CME.
$1,400 margin, tick size $0.25 = $1.25/tick. EMA crossover + ADX filter.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MESTrend(StrategyBase):
    """MES trend following with EMA crossover and ADX filter."""

    SYMBOL = "MES"
    TICK_SIZE = 0.25
    TICK_VALUE = 1.25  # $1.25 per tick

    def __init__(self) -> None:
        self.ema_fast: int = 20
        self.ema_slow: int = 50
        self.adx_threshold: float = 25.0
        self.sl_points: float = 20.0
        self.tp_points: float = 40.0
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mes_trend"

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

        ema_f = self.data_feed.get_indicator(sym, "ema", self.ema_fast)
        ema_s = self.data_feed.get_indicator(sym, "ema", self.ema_slow)
        adx = self.data_feed.get_indicator(sym, "adx", 14)
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)

        if any(v is None for v in (ema_f, ema_s, adx, rsi)):
            return None

        if adx < self.adx_threshold:
            return None

        # Long: fast EMA above slow, RSI not overbought
        if ema_f > ema_s and rsi < 75.0:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - self.sl_points,
                take_profit=bar.close + self.tp_points,
                strength=min((adx - self.adx_threshold) / 25.0, 1.0),
            )

        # Short: fast EMA below slow, RSI not oversold
        if ema_f < ema_s and rsi > 25.0:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + self.sl_points,
                take_profit=bar.close - self.tp_points,
                strength=min((adx - self.adx_threshold) / 25.0, 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "adx_threshold": self.adx_threshold,
            "sl_points": self.sl_points,
            "tp_points": self.tp_points,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "ema_fast": [10, 15, 20, 25],
            "ema_slow": [40, 50, 60, 80],
            "adx_threshold": [20.0, 25.0, 30.0],
            "sl_points": [10.0, 15.0, 20.0, 25.0],
            "tp_points": [25.0, 30.0, 40.0, 50.0],
        }
