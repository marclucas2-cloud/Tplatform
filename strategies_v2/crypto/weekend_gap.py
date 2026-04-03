"""Weekend Gap — Buy weekend dips in BTC, sell on gap fill or timeout.

Crypto trades 24/7 but liquidity drops on weekends. This strategy
detects weekend dips (Saturday-Sunday) of -3% to -8% and enters long,
expecting a mean-reversion gap fill by Monday. Spot long only.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class WeekendGap(StrategyBase):
    """Weekend dip -3% to -8%, buy Sunday, sell gap fill or 48h."""

    broker = "BINANCE"

    def __init__(self, data_feed: DataFeed, symbol: str = "BTCUSDT") -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        # Tunable parameters
        self.dip_min = -0.03
        self.dip_crash = -0.08
        self.sl_pct = 0.05
        self.max_holding_hours = 48

    @property
    def name(self) -> str:
        return "weekend_gap"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "dip_min": self.dip_min,
            "dip_crash": self.dip_crash,
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
            "dip_min": [-0.02, -0.03, -0.04],
            "dip_crash": [-0.06, -0.08, -0.10],
            "sl_pct": [0.03, 0.05, 0.07],
            "max_holding_hours": [24, 48, 72],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        # Only trade on weekends (Saturday=5, Sunday=6)
        if bar.timestamp.dayofweek not in (5, 6):
            return None

        sym = bar.symbol
        # Need enough bars to compute Friday close reference
        bars = self.data_feed.get_bars(sym, 72)
        if len(bars) < 72:
            return None

        # Find last Friday close as reference (look back for dayofweek==4)
        friday_close = None
        for i in range(len(bars) - 1, -1, -1):
            if bars.index[i].dayofweek == 4:
                friday_close = float(bars["close"].iloc[i])
                break

        if friday_close is None or friday_close == 0:
            return None

        price = bar.close
        dip_pct = (price - friday_close) / friday_close

        # Skip if crash is too deep (black swan) or dip is too small
        if dip_pct <= self.dip_crash or dip_pct >= self.dip_min:
            return None

        # Weekend dip in the sweet spot -> long
        return Signal(
            symbol=sym,
            side="BUY",
            strategy_name=self.name,
            stop_loss=price * (1.0 + self.dip_crash),
            take_profit=friday_close,
            strength=min(abs(dip_pct) / abs(self.dip_crash), 1.0),
        )
