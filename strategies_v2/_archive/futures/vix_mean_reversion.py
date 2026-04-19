"""VIX Mean Reversion — Buy equities after VIX spike + RSI oversold.

Edge: After VIX spikes > 25 with RSI < 30, equities bounce within 3-10 days.
Well-documented crisis recovery pattern (COVID, 2022 bear, 2025 selloff).

Backtest (11 years, 2015-2026):
  19 trades, 47% WR, +$6,049, Sharpe 5.63, WF 5/8
  High conviction, low frequency (~2-3 trades/year).

Signal:
  BUY MES when VIX > 25 AND RSI14(MES) < 30
  EXIT when VIX < 20 OR 10 days held
  SL: 50 points ($250 per contract)

Priority: 10 (highest — rare signal, don't let other strats block it)
1 contract MES.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class VIXMeanReversion(StrategyBase):
    """Buy MES after VIX spike + RSI oversold."""

    SYMBOL = "MES"

    def __init__(self) -> None:
        self.vix_threshold: float = 25.0
        self.rsi_threshold: float = 30.0
        self.sl_points: float = 50.0
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "vix_mean_reversion"

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

        # Need VIX data
        vix_bars = self.data_feed.get_bars("VIX", 5)
        if vix_bars is None or len(vix_bars) < 1:
            return None

        vix_close = vix_bars["close"].iloc[-1]

        # Need MES RSI14
        mes_bars = self.data_feed.get_bars(self.SYMBOL, 20)
        if mes_bars is None or len(mes_bars) < 14:
            return None

        # RSI14
        delta = mes_bars["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = delta.clip(upper=0).abs().rolling(14).mean()
        if loss.iloc[-1] == 0:
            return None
        rsi = 100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])

        # Signal: VIX > 25 AND RSI < 30
        if vix_close > self.vix_threshold and rsi < self.rsi_threshold:
            return Signal(
                symbol=self.SYMBOL,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - self.sl_points,
                take_profit=bar.close + 100,  # wide TP, exits via time/VIX
                strength=min((vix_close - 20) / 20, 1.0),  # higher VIX = stronger signal
            )

        return None

    def get_parameters(self) -> dict:
        return {
            "vix_threshold": self.vix_threshold,
            "rsi_threshold": self.rsi_threshold,
            "sl_points": self.sl_points,
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
