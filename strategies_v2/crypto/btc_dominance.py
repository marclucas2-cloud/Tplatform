"""BTC Dominance — Trade based on BTC dominance EMA crossover.

Uses BTC.D (dominance %) data via a special dominance symbol in the
DataFeed. When dominance is rising (EMA7 > EMA21 by dead_zone), go
long BTC and short alts. When falling, the opposite.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class BTCDominance(StrategyBase):
    """EMA7/21 on BTC dominance, dead zone 0.5%."""

    broker = "BINANCE"

    def __init__(
        self,
        data_feed: DataFeed,
        symbol: str = "BTCUSDT",
        dominance_symbol: str = "BTC.D",
    ) -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        self.dominance_symbol = dominance_symbol
        # Tunable parameters
        self.ema_fast = 7
        self.ema_slow = 21
        self.dead_zone = 0.005

    @property
    def name(self) -> str:
        return "btc_dominance"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "dead_zone": self.dead_zone,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "ema_fast": [5, 7, 10],
            "ema_slow": [14, 21, 30],
            "dead_zone": [0.003, 0.005, 0.008],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        dom_sym = self.dominance_symbol
        ema_f = self.data_feed.get_indicator(dom_sym, "ema", self.ema_fast)
        ema_s = self.data_feed.get_indicator(dom_sym, "ema", self.ema_slow)

        if ema_f is None or ema_s is None:
            return None

        spread = (ema_f - ema_s) / ema_s if ema_s != 0 else 0.0

        # Dead zone: no signal if spread is too small
        if abs(spread) < self.dead_zone:
            return None

        # Rising dominance -> long BTC (BTC outperforms alts)
        if spread > self.dead_zone:
            return Signal(
                symbol=self.symbol,
                side="BUY",
                strategy_name=self.name,
                strength=min(abs(spread) / 0.02, 1.0),
            )

        # Falling dominance -> short BTC (alts outperform)
        if spread < -self.dead_zone:
            return Signal(
                symbol=self.symbol,
                side="SELL",
                strategy_name=self.name,
                strength=min(abs(spread) / 0.02, 1.0),
            )

        return None
