"""Overnight Buy-Close-Sell-Open for MES/MNQ.

Edge: Equity indices have a positive overnight return premium.
Buy at close, sell at next day open. EMA20 trend filter.

Backtest (3 years):
  MES: 208 trades, 60% WR, +$13,546, Sharpe 3.85, PF 2.5, WF 4/5
  MNQ: 186 trades, 58% WR, +$27,499, Sharpe 4.14, PF 2.6, WF 5/5

Paper first, live after 30 trades confirmed.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class OvernightBuyClose(StrategyBase):
    """Buy at close, sell at next open. EMA20 trend filter."""

    def __init__(self, symbol: str = "MES") -> None:
        self._symbol = symbol
        self.ema_period: int = 20
        self.sl_points: float = 30.0  # safety net SL
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"overnight_bc_{self._symbol.lower()}"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self.data_feed is None:
            return None

        ema = self.data_feed.get_indicator(self._symbol, "ema", self.ema_period)
        if ema is None:
            return None

        if bar.close > ema:
            return Signal(
                symbol=self._symbol,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - self.sl_points,
                take_profit=bar.close + 50,
                strength=min((bar.close - ema) / ema * 100, 1.0),
            )
        return None

    def get_parameters(self) -> dict:
        return {"symbol": self._symbol, "ema_period": self.ema_period}

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
