"""Thursday rally — buy Thursday close, exit after 3 days.

Edge: day-of-week effect — Thursday close has been historically followed
by a positive drift over the subsequent days (pre-weekend positioning).
Captured both on MES and MNQ with consistent results.

Backtest 5Y daily (MES):
  - n=264 trades, WR 57%, +\$8,635, Sharpe 1.31
  - WF 3/5 profitable, IS 1.21 -> OOS 1.61 (ratio 1.33)

Backtest 5Y daily (MNQ):
  - n=264 trades, WR 54%, +\$11,459, Sharpe 0.99
  - WF 3/5 profitable, IS 1.18 -> OOS 0.92 (ratio 0.78)

Params: SL 2.5%, TP 4%, hold 3 days.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class ThursdayRally(StrategyBase):
    """Long futures at Thursday close."""

    def __init__(self, symbol: str = "MES", sl_pct: float = 0.025, tp_pct: float = 0.04) -> None:
        self._symbol = symbol
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"thursday_rally_{self._symbol.lower()}"

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
        ts = bar.timestamp
        if not hasattr(ts, "weekday"):
            return None
        if ts.weekday() != 3:  # Thursday
            return None

        return Signal(
            symbol=self._symbol,
            side="BUY",
            strategy_name=self.name,
            stop_loss=bar.close * (1 - self.sl_pct),
            take_profit=bar.close * (1 + self.tp_pct),
            strength=0.6,
        )

    def get_parameters(self) -> dict:
        return {"symbol": self._symbol, "sl_pct": self.sl_pct, "tp_pct": self.tp_pct}
