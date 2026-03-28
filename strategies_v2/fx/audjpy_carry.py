"""AUD/JPY Carry strategy for BacktesterV2.

Long when positive carry direction confirmed by trend (EMA filter).
AUD/JPY is a classic risk-on carry pair with higher volatility than EUR/JPY.
Uses wider stops to accommodate AUD's commodity-driven moves.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class AUDJPYCarry(StrategyBase):
    """AUD/JPY carry trade with EMA trend filter and wider stops."""

    SYMBOL = "AUDJPY"

    def __init__(self) -> None:
        self.ema_period: int = 50
        self.carry_threshold: float = 0.001
        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "audjpy_carry"

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
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None
        sym = self.SYMBOL

        ema = self.data_feed.get_indicator(sym, "ema", self.ema_period)
        atr = self.data_feed.get_indicator(sym, "atr", 14)
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)

        if any(v is None for v in (ema, atr, rsi)):
            return None

        # AUD/JPY positive carry = long (AUD higher rates than JPY)
        # Wider ATR multipliers vs EUR/JPY due to higher vol
        carry_direction = 1.0  # AUD/JPY typically strong positive carry

        if bar.close > ema and carry_direction >= self.carry_threshold:
            if 40.0 <= rsi <= 65.0:  # tighter RSI band (AUD more volatile)
                return Signal(
                    symbol=sym,
                    side="BUY",
                    strategy_name=self.name,
                    stop_loss=bar.close - 3.0 * atr,
                    take_profit=bar.close + 4.0 * atr,
                    strength=0.6,
                )

        # Short on strong downtrend — risk-off protection
        if bar.close < ema and rsi < 30.0:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + 3.0 * atr,
                take_profit=bar.close - 4.0 * atr,
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
