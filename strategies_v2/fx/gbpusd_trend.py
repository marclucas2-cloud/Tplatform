"""GBP/USD Trend Following strategy for BacktesterV2.

EMA20/50 crossover + ADX>25 trend filter + RSI 45-75 momentum band.
Same core logic as EUR/USD trend but tuned for GBP/USD characteristics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class GBPUSDTrend(StrategyBase):
    """GBP/USD trend following with EMA crossover, ADX filter, RSI band."""

    SYMBOL = "GBPUSD"

    def __init__(self) -> None:
        self.ema_fast: int = 20
        self.ema_slow: int = 50
        self.adx_threshold: float = 25.0
        self.rsi_low: float = 45.0
        self.rsi_high: float = 75.0
        self.sl_atr: float = 2.5
        self.tp_atr: float = 4.0
        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "gbpusd_trend"

    @property
    def asset_class(self) -> str:
        return "fx"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None
        sym = self.SYMBOL

        ema_f = self.data_feed.get_indicator(sym, "ema", self.ema_fast)
        ema_s = self.data_feed.get_indicator(sym, "ema", self.ema_slow)
        adx = self.data_feed.get_indicator(sym, "adx", 14)
        rsi = self.data_feed.get_indicator(sym, "rsi", 14)
        atr = self.data_feed.get_indicator(sym, "atr", 14)

        if any(v is None for v in (ema_f, ema_s, adx, rsi, atr)):
            return None

        if adx < self.adx_threshold:
            return None

        # Long: fast EMA above slow + RSI in momentum band
        if ema_f > ema_s and self.rsi_low <= rsi <= self.rsi_high:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - self.sl_atr * atr,
                take_profit=bar.close + self.tp_atr * atr,
                strength=min((adx - self.adx_threshold) / 25.0, 1.0),
            )

        # Short: fast EMA below slow + RSI in inverse band
        if ema_f < ema_s and (100 - self.rsi_high) <= rsi <= (100 - self.rsi_low):
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + self.sl_atr * atr,
                take_profit=bar.close - self.tp_atr * atr,
                strength=min((adx - self.adx_threshold) / 25.0, 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "adx_threshold": self.adx_threshold,
            "rsi_low": self.rsi_low,
            "rsi_high": self.rsi_high,
            "sl_atr": self.sl_atr,
            "tp_atr": self.tp_atr,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "ema_fast": [10, 15, 20, 25],
            "ema_slow": [40, 50, 60, 80],
            "adx_threshold": [20.0, 25.0, 30.0],
            "rsi_low": [40.0, 45.0, 50.0],
            "rsi_high": [70.0, 75.0, 80.0],
            "sl_atr": [2.0, 2.5, 3.0],
            "tp_atr": [3.0, 4.0, 5.0],
        }
