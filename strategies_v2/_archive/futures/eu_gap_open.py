"""EU Gap Open — Fade large ESTX50 gaps at European open.

Edge: Gaps > 1% on ESTX50 tend to revert within the session.
Structural cause: overnight US moves overshoot EU fair value.

Backtest (3 years, portfolio context):
  8 trades, 50% WR, +$452, Sharpe 0.65, avg $56/trade
  Low frequency but high conviction when triggered.

Signal:
  If gap > +1% -> SELL (fade up gap)
  If gap < -1% -> BUY (fade down gap)
  SL: 1.5% from open, TP: 2% from open
  Exit: EOD (same day)

1 ESTX50 future contract.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class EUGapOpen(StrategyBase):
    """Fade large ESTX50 gaps at EU open."""

    SYMBOL = "ESTX50"

    def __init__(self) -> None:
        self.min_gap: float = 0.01  # 1%
        self.max_gap: float = 0.05  # 5%
        self.sl_pct: float = 0.015
        self.tp_pct: float = 0.02
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "eu_gap_open"

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

        bars = self.data_feed.get_bars(self.SYMBOL, 3)
        if bars is None or len(bars) < 2:
            return None

        prev_close = bars["close"].iloc[-2]
        today_open = bars["open"].iloc[-1]

        if prev_close == 0:
            return None

        gap = (today_open - prev_close) / prev_close

        if abs(gap) < self.min_gap or abs(gap) > self.max_gap:
            return None

        if gap > self.min_gap:
            # Gap up -> fade SHORT
            return Signal(
                symbol=self.SYMBOL,
                side="SELL",
                strategy_name=self.name,
                stop_loss=today_open * (1 + self.sl_pct),
                take_profit=today_open * (1 - self.tp_pct),
                strength=min(abs(gap) * 20, 1.0),
            )
        elif gap < -self.min_gap:
            # Gap down -> fade LONG
            return Signal(
                symbol=self.SYMBOL,
                side="BUY",
                strategy_name=self.name,
                stop_loss=today_open * (1 - self.sl_pct),
                take_profit=today_open * (1 + self.tp_pct),
                strength=min(abs(gap) * 20, 1.0),
            )

        return None

    def get_parameters(self) -> dict:
        return {
            "min_gap": self.min_gap,
            "max_gap": self.max_gap,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
