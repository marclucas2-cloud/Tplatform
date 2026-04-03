"""Borrow Rate Carry — Earn allocation based on APY thresholds.

Monitors Binance Earn APY rates. When USDT APY is high (>8%), deposits
into earn. When all rates are low (<3%), redeems to hold cash/spot.
Uses special signal side values EARN_DEPOSIT and EARN_REDEEM.

Requires a "USDT_APY" symbol in the DataFeed whose close column
represents the current annualized yield as a decimal (e.g. 0.08 = 8%).
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class BorrowRateCarry(StrategyBase):
    """Earn allocation based on APY thresholds."""

    broker = "BINANCE"

    def __init__(
        self,
        data_feed: DataFeed,
        symbol: str = "USDT_APY",
    ) -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        # Tunable parameters
        self.high_usdt_threshold = 0.08
        self.low_all_threshold = 0.03

    @property
    def name(self) -> str:
        return "borrow_rate_carry"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "high_usdt_threshold": self.high_usdt_threshold,
            "low_all_threshold": self.low_all_threshold,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "high_usdt_threshold": [0.06, 0.08, 0.10, 0.12],
            "low_all_threshold": [0.02, 0.03, 0.04],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        sym = bar.symbol
        # bar.close represents the current APY as a decimal
        apy = bar.close

        if apy is None:
            return None

        sma = self.data_feed.get_indicator(sym, "sma", 7)
        if sma is None:
            return None

        # High yield -> deposit into earn
        if apy >= self.high_usdt_threshold and sma >= self.high_usdt_threshold:
            return Signal(
                symbol=sym,
                side="EARN_DEPOSIT",
                strategy_name=self.name,
                strength=min(apy / self.high_usdt_threshold, 1.0),
            )

        # Low yield -> redeem from earn
        if apy < self.low_all_threshold and sma < self.low_all_threshold:
            return Signal(
                symbol=sym,
                side="EARN_REDEEM",
                strategy_name=self.name,
                strength=1.0 - min(apy / self.low_all_threshold, 1.0),
            )

        return None
