"""MES Trend+MR Hybrid strategy for BacktesterV2.

Micro E-mini S&P 500 (MES). Combines trend direction with mean reversion timing.
- Above EMA50: buy RSI(2) < 15 dips (mean reversion in uptrend)
- Below EMA50: short RSI(2) > 85 rips (mean reversion in downtrend)
- SL: 20 points ($100 per contract)
- TP: RSI(2) reversal or max 5 days

Backtest: 21 trades, 76% WR, Sharpe +1.29, PF 2.0, WF OOS 4/5 +$148.
PO verdict: paper first, min 2 weeks before live.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MESTrendMR(StrategyBase):
    """MES Trend + Mean Reversion hybrid."""

    SYMBOL = "MES"
    TICK_SIZE = 0.25
    TICK_VALUE = 1.25

    def __init__(self) -> None:
        self.ema_period: int = 50
        self.rsi_period: int = 2
        self.rsi_long_entry: float = 15.0
        self.rsi_short_entry: float = 85.0
        self.rsi_long_exit: float = 70.0
        self.rsi_short_exit: float = 30.0
        self.sl_points: float = 20.0  # $100 per contract SL
        self.tp_points: float = 30.0  # $150 per contract TP
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mes_trend_mr"

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

        ema50 = self.data_feed.get_indicator(sym, "ema", self.ema_period)
        rsi2 = self.data_feed.get_indicator(sym, "rsi", self.rsi_period)

        if ema50 is None or rsi2 is None:
            return None

        price = bar.close

        # Above EMA50: buy dips (RSI2 < 15)
        if price > ema50 and rsi2 < self.rsi_long_entry:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price - self.sl_points,
                take_profit=price + self.tp_points,
                strength=min((self.rsi_long_entry - rsi2) / 15.0, 1.0),
            )

        # Below EMA50: short rips (RSI2 > 85)
        if price < ema50 and rsi2 > self.rsi_short_entry:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=price + self.sl_points,
                take_profit=price - self.tp_points,
                strength=min((rsi2 - self.rsi_short_entry) / 15.0, 1.0),
            )

        return None

    def get_parameters(self) -> dict:
        return {
            "ema_period": self.ema_period,
            "rsi_period": self.rsi_period,
            "rsi_long_entry": self.rsi_long_entry,
            "rsi_short_entry": self.rsi_short_entry,
            "sl_points": self.sl_points,
            "tp_points": self.tp_points,
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
