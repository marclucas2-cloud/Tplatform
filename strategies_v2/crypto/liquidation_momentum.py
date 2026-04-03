"""Liquidation Momentum — Trade cascading liquidation effects.

OI drop + volume spike + price move -> enter in cascade direction.
Margin trade on Binance. Falls back to volume proxy if OI missing.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class LiquidationMomentum(StrategyBase):
    """OI drop + volume spike + price move, margin trade."""

    broker = "BINANCE"

    def __init__(self, data_feed: DataFeed, symbol: str = "BTCUSDT") -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        # Tunable parameters
        self.oi_drop_threshold = 0.08
        self.price_move_threshold = 0.04
        self.volume_mult = 3.0
        self.sl_pct = 0.015
        self.tp_pct = 0.03
        self.max_holding_hours = 24

    @property
    def name(self) -> str:
        return "liquidation_momentum"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "oi_drop_threshold": self.oi_drop_threshold,
            "price_move_threshold": self.price_move_threshold,
            "volume_mult": self.volume_mult,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "max_holding_hours": self.max_holding_hours,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "oi_drop_threshold": [0.05, 0.08, 0.10],
            "price_move_threshold": [0.03, 0.04, 0.05],
            "volume_mult": [2.0, 3.0, 4.0],
            "sl_pct": [0.01, 0.015, 0.02],
            "tp_pct": [0.02, 0.03, 0.05],
            "max_holding_hours": [12, 24, 48],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        sym = bar.symbol
        bars = self.data_feed.get_bars(sym, 25)
        if len(bars) < 25:
            return None

        avg_vol = float(bars["volume"].iloc[:-1].mean())
        if avg_vol == 0:
            return None
        vol_ratio = float(bar.volume) / avg_vol
        if vol_ratio < self.volume_mult:
            return None

        prev_close = float(bars["close"].iloc[-2])
        if prev_close == 0:
            return None
        price_change = (bar.close - prev_close) / prev_close
        if abs(price_change) < self.price_move_threshold:
            return None

        # OI proxy: extreme volume spike suggests forced liquidations
        oi_proxy_drop = vol_ratio > (self.volume_mult * 1.5)
        price = bar.close

        if price_change < -self.price_move_threshold and oi_proxy_drop:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=price * (1.0 + self.sl_pct),
                take_profit=price * (1.0 - self.tp_pct),
                strength=min(abs(price_change) / 0.10, 1.0),
            )

        # Bullish liquidation cascade (shorts liquidated -> long)
        if price_change > self.price_move_threshold and oi_proxy_drop:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price * (1.0 - self.sl_pct),
                take_profit=price * (1.0 + self.tp_pct),
                strength=min(abs(price_change) / 0.10, 1.0),
            )

        return None
