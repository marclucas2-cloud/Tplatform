"""EUR/GBP Mean Reversion strategy for BacktesterV2.

RSI oversold/overbought reversals confirmed by Bollinger Band touches.
Classic mean-reversion on a low-volatility FX pair.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class EURGBPMeanReversion(StrategyBase):
    """EUR/GBP mean reversion with RSI extremes + Bollinger confirmation."""

    SYMBOL = "EURGBP"

    def __init__(self) -> None:
        self.rsi_period: int = 14
        self.rsi_oversold: float = 30.0
        self.rsi_overbought: float = 70.0
        self.bb_period: int = 20
        self.bb_std: float = 2.0
        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "eurgbp_mean_reversion"

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

        rsi = self.data_feed.get_indicator(sym, "rsi", self.rsi_period)
        bb_upper = self.data_feed.get_indicator(sym, "bollinger_upper", self.bb_period)
        bb_lower = self.data_feed.get_indicator(sym, "bollinger_lower", self.bb_period)
        bb_mid = self.data_feed.get_indicator(sym, "bollinger_mid", self.bb_period)

        if any(v is None for v in (rsi, bb_upper, bb_lower, bb_mid)):
            return None

        # Long: RSI oversold + price at/below lower BB
        if rsi < self.rsi_oversold and bar.close <= bb_lower:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - (bb_mid - bb_lower) * 0.5,
                take_profit=bb_mid,
                strength=min((self.rsi_oversold - rsi) / 30.0, 1.0),
            )

        # Short: RSI overbought + price at/above upper BB
        if rsi > self.rsi_overbought and bar.close >= bb_upper:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + (bb_upper - bb_mid) * 0.5,
                take_profit=bb_mid,
                strength=min((rsi - self.rsi_overbought) / 30.0, 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "rsi_period": [10, 14, 20],
            "rsi_oversold": [25.0, 30.0, 35.0],
            "rsi_overbought": [65.0, 70.0, 75.0],
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
        }
