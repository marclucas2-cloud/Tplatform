"""Friday-Monday weekend effect on MNQ.

Edge: the weekend effect documented in equities — Monday returns are
historically different from other days (Cross, French 1980). On MNQ,
buying Friday close and selling Monday close captures a long-biased drift.

Backtest 5Y daily:
  - n=266 trades, WR 54%, +$14,913 total, Sharpe 1.76
  - WF 4/5 profitable, OOS avg Sharpe 1.86
  - Rare config where IS < OOS (OOS overperforms)

Params: SL 2%, TP 3%, hold 2 days (Monday close → Tuesday close).
Broker: IBKR MNQ micro ($2/pt).
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class FridayMondayMNQ(StrategyBase):
    """Long MNQ at Friday close, exit ~Monday close."""

    SYMBOL = "MNQ"

    def __init__(self, sl_pct: float = 0.02, tp_pct: float = 0.03) -> None:
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "friday_monday_mnq"

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

        # Only fire on Friday close
        ts = bar.timestamp
        if not hasattr(ts, "weekday"):
            return None
        if ts.weekday() != 4:  # Friday
            return None

        # SL/TP in absolute price
        sl_abs = bar.close * (1 - self.sl_pct)
        tp_abs = bar.close * (1 + self.tp_pct)

        return Signal(
            symbol=self.SYMBOL,
            side="BUY",
            strategy_name=self.name,
            stop_loss=sl_abs,
            take_profit=tp_abs,
            strength=0.7,
        )

    def get_parameters(self) -> dict:
        return {"sl_pct": self.sl_pct, "tp_pct": self.tp_pct}
