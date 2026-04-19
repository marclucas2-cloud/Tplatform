"""EUR/JPY Carry strategy for BacktesterV2.

Long when positive carry direction confirmed by trend (EMA filter).
Carry trades profit from interest rate differentials when aligned with trend.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class EURJPYCarry(StrategyBase):
    """EUR/JPY carry trade with EMA trend filter."""

    SYMBOL = "EURJPY"

    def __init__(self) -> None:
        self.ema_period: int = 50
        self.carry_threshold: float = 0.001
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "eurjpy_carry"

    @property
    def asset_class(self) -> str:
        return "fx"

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

        ema = self.data_feed.get_indicator(sym, "ema", self.ema_period)
        atr = self.data_feed.get_indicator(sym, "atr", 14)
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)

        if any(v is None for v in (ema, atr, rsi)):
            return None

        # Positive carry on EUR/JPY = long (EUR higher rates than JPY)
        # Only enter when price above EMA (trend confirmation)
        carry_direction = 1.0  # EUR/JPY typically positive carry

        if bar.close > ema and carry_direction >= self.carry_threshold:
            if 40.0 <= rsi <= 70.0:  # avoid extremes
                return Signal(
                    symbol=sym,
                    side="BUY",
                    strategy_name=self.name,
                    stop_loss=bar.close - 2.5 * atr,
                    take_profit=bar.close + 3.5 * atr,
                    strength=0.7,
                )

        # Short only if strong downtrend (carry reversal protection)
        if bar.close < ema and rsi < 35.0:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + 2.5 * atr,
                take_profit=bar.close - 3.5 * atr,
                strength=0.5,
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "ema_period": self.ema_period,
            "carry_threshold": self.carry_threshold,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "ema_period": [30, 50, 75, 100],
            "carry_threshold": [0.0005, 0.001, 0.002],
        }
