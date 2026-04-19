"""Sector Rotation EU — DAX vs CAC40 momentum weekly rebalance.

Edge: German industrials (DAX) and French diversified (CAC40) alternate
leadership driven by macro cycles. 20-day momentum captures this.

Backtest (3 years, portfolio context):
  71 trades, 61% WR, +$4,541, Sharpe 1.23, avg $64/trade
  Rebalance weekly (Mondays), hold 5 days.

Signal:
  If DAX 20d momentum > CAC40 20d momentum + 2% -> BUY DAX
  If CAC40 20d momentum > DAX 20d momentum + 2% -> BUY CAC40
  SL: -4%, TP: +8%
  Rebalance every Monday

1 position. Paper -> live after 10 trades.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class SectorRotationEU(StrategyBase):
    """DAX vs CAC40 momentum rotation."""

    def __init__(self) -> None:
        self.momentum_period: int = 20
        self.threshold: float = 0.02  # 2% momentum difference
        self.sl_pct: float = 0.04
        self.tp_pct: float = 0.08
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "sector_rotation_eu"

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

        dax_bars = self.data_feed.get_bars("DAX", self.momentum_period + 2)
        cac_bars = self.data_feed.get_bars("CAC40", self.momentum_period + 2)

        if dax_bars is None or cac_bars is None:
            return None
        if len(dax_bars) < self.momentum_period or len(cac_bars) < self.momentum_period:
            return None

        dax_mom = dax_bars["close"].iloc[-1] / dax_bars["close"].iloc[-self.momentum_period] - 1
        cac_mom = cac_bars["close"].iloc[-1] / cac_bars["close"].iloc[-self.momentum_period] - 1

        # Only rebalance on Mondays
        if bar.timestamp.weekday() != 0:
            return None

        if dax_mom > cac_mom + self.threshold:
            # DAX leads — long DAX proxy
            return Signal(
                symbol="DAX",
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close * (1 - self.sl_pct),
                take_profit=bar.close * (1 + self.tp_pct),
                strength=min(abs(dax_mom - cac_mom) * 10, 1.0),
            )
        elif cac_mom > dax_mom + self.threshold:
            return Signal(
                symbol="CAC40",
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close * (1 - self.sl_pct),
                take_profit=bar.close * (1 + self.tp_pct),
                strength=min(abs(cac_mom - dax_mom) * 10, 1.0),
            )

        return None

    def get_parameters(self) -> dict:
        return {
            "momentum_period": self.momentum_period,
            "threshold": self.threshold,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
