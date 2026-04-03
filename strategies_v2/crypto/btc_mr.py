"""BTC Mean Reversion — RSI + Bollinger Band lower + ADX range filter.

Spot long-only strategy. Buys when RSI is oversold, price touches the
lower Bollinger Band, and ADX confirms a ranging market (no strong trend).
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class BTCMeanReversion(StrategyBase):
    """RSI<30 + BB lower + ADX<20 (range only), spot long only."""

    broker = "BINANCE"

    def __init__(self, data_feed: DataFeed, symbol: str = "BTCUSDT") -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        # Tunable parameters
        self.rsi_threshold = 30
        self.bb_period = 20
        self.bb_std = 2.0
        self.adx_max = 20
        self.sl_pct = 0.03
        self.max_holding_hours = 48

    @property
    def name(self) -> str:
        return "btc_mean_reversion"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "rsi_threshold": self.rsi_threshold,
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "adx_max": self.adx_max,
            "sl_pct": self.sl_pct,
            "max_holding_hours": self.max_holding_hours,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "rsi_threshold": [25, 30, 35],
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "adx_max": [15, 20, 25],
            "sl_pct": [0.02, 0.03, 0.04],
            "max_holding_hours": [24, 48, 72],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        sym = bar.symbol
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)
        bb_lower = self.data_feed.get_indicator(
            sym, "bollinger_lower", self.bb_period
        )
        adx = self.data_feed.get_indicator(sym, "adx", 14)

        if any(v is None for v in (rsi, bb_lower, adx)):
            return None

        # Range market only
        if adx > self.adx_max:
            return None

        price = bar.close

        # Oversold + touching lower band -> long
        if rsi < self.rsi_threshold and price <= bb_lower:
            bb_mid = self.data_feed.get_indicator(
                sym, "bollinger_mid", self.bb_period
            )
            tp = bb_mid if bb_mid is not None else price * 1.03
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price * (1.0 - self.sl_pct),
                take_profit=tp,
                strength=min((self.rsi_threshold - rsi) / 20.0, 1.0),
            )

        return None
