"""BTC/ETH Dual Momentum — EMA crossover + ADX trend filter + RSI guard.

Trades BTC and ETH on Binance margin (long/short). Enters when fast EMA
crosses slow EMA, ADX confirms trend strength, and RSI is in the
acceptable zone to avoid overbought/oversold entries.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class BTCETHDualMomentum(StrategyBase):
    """EMA20/50 + ADX>25 + RSI filter, margin long/short."""

    broker = "BINANCE"

    def __init__(self, data_feed: DataFeed, symbol: str = "BTCUSDT") -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        # Tunable parameters
        self.ema_fast = 20
        self.ema_slow = 50
        self.adx_threshold = 25
        self.rsi_long_min = 45
        self.rsi_long_max = 75
        self.sl_atr = 2.5
        self.tp_atr = 4.0
        self.max_holding_days = 21

    @property
    def name(self) -> str:
        return "btc_eth_dual_momentum"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "adx_threshold": self.adx_threshold,
            "rsi_long_min": self.rsi_long_min,
            "rsi_long_max": self.rsi_long_max,
            "sl_atr": self.sl_atr,
            "tp_atr": self.tp_atr,
            "max_holding_days": self.max_holding_days,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "ema_fast": [10, 15, 20, 25],
            "ema_slow": [40, 50, 60],
            "adx_threshold": [20, 25, 30],
            "rsi_long_min": [40, 45, 50],
            "rsi_long_max": [70, 75, 80],
            "sl_atr": [2.0, 2.5, 3.0],
            "tp_atr": [3.0, 4.0, 5.0],
            "max_holding_days": [14, 21, 28],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        sym = bar.symbol
        ema_f = self.data_feed.get_indicator(sym, "ema", self.ema_fast)
        ema_s = self.data_feed.get_indicator(sym, "ema", self.ema_slow)
        adx = self.data_feed.get_indicator(sym, "adx", 14)
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)
        atr = self.data_feed.get_indicator(sym, "atr", 14)

        if any(v is None for v in (ema_f, ema_s, adx, rsi, atr)):
            return None

        if adx < self.adx_threshold:
            return None

        price = bar.close

        # Long signal: fast > slow and RSI in acceptable range
        if ema_f > ema_s and self.rsi_long_min <= rsi <= self.rsi_long_max:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price - self.sl_atr * atr,
                take_profit=price + self.tp_atr * atr,
                strength=min((adx - self.adx_threshold) / 20.0, 1.0),
            )

        # Short signal: fast < slow and RSI outside long zone (inverted)
        rsi_short_min = 100 - self.rsi_long_max
        rsi_short_max = 100 - self.rsi_long_min
        if ema_f < ema_s and rsi_short_min <= rsi <= rsi_short_max:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=price + self.sl_atr * atr,
                take_profit=price - self.tp_atr * atr,
                strength=min((adx - self.adx_threshold) / 20.0, 1.0),
            )

        return None
