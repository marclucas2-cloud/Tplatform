"""Commodity Seasonality — paper monitoring.

Edge: Seasonal patterns in commodities (crude oil, gold).
- MCL: long February, exit April (pre-driving season)
- MGC: long July, exit September (pre-India jewelry season)

Backtest: 15 trades, 80% WR, +$13,452, Sharpe 4.59, WF 4/5.
Insuffisant (n<30). Paper until 30+ trades (~2028).
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


SEASONAL_WINDOWS = [
    {"symbol": "MCL", "entry_month": 2, "exit_month": 4, "direction": "BUY"},
    {"symbol": "MGC", "entry_month": 7, "exit_month": 9, "direction": "BUY"},
]


class CommoditySeason(StrategyBase):
    """Seasonal commodity trades. Paper only."""

    def __init__(self, symbol: str = "MCL") -> None:
        self._symbol = symbol
        self.data_feed: DataFeed | None = None
        self._window = next((w for w in SEASONAL_WINDOWS if w["symbol"] == symbol), None)

    @property
    def name(self) -> str:
        return f"season_{self._symbol.lower()}"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self._window is None or self.data_feed is None:
            return None

        ts = bar.timestamp
        if not hasattr(ts, "month"):
            return None

        # Entry: first 5 days of entry month
        if ts.month == self._window["entry_month"] and ts.day <= 5:
            return Signal(
                symbol=self._symbol,
                side=self._window["direction"],
                strategy_name=self.name,
                stop_loss=bar.close * 0.95,  # 5% SL
                take_profit=bar.close * 1.10,  # 10% TP
                strength=0.5,
            )

        return None

    def get_parameters(self) -> dict:
        return {"symbol": self._symbol, "window": self._window}

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
