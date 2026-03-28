"""Altcoin Relative Strength — Weekly ranking by BTC-adjusted alpha.

Ranks altcoins by 14d return adjusted for BTC. Longs top N, shorts
bottom N. Rebalances weekly on a configurable day.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class AltcoinRelativeStrength(StrategyBase):
    """Weekly ranking by BTC-adjusted 14d alpha, long top 3, short bottom 3."""

    broker = "BINANCE"

    def __init__(
        self,
        data_feed: DataFeed,
        symbol: str = "ALTBASKET",
        alt_symbols: Optional[List[str]] = None,
        btc_symbol: str = "BTCUSDT",
    ) -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        self.alt_symbols = alt_symbols or [
            "ETHUSDT", "SOLUSDT", "ADAUSDT", "DOTUSDT",
            "AVAXUSDT", "MATICUSDT", "LINKUSDT", "ATOMUSDT",
        ]
        self.btc_symbol = btc_symbol
        # Tunable parameters
        self.lookback_days = 14
        self.top_n = 3
        self.bottom_n = 3
        self.rebalance_day = 6  # 0=Mon, 6=Sun

    @property
    def name(self) -> str:
        return "altcoin_relative_strength"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_ALT_T2"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "lookback_days": self.lookback_days,
            "top_n": self.top_n,
            "bottom_n": self.bottom_n,
            "rebalance_day": self.rebalance_day,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "lookback_days": [7, 14, 21, 30],
            "top_n": [2, 3, 4],
            "bottom_n": [2, 3, 4],
            "rebalance_day": [0, 3, 6],
        }

    def _compute_alpha(self, sym: str, lookback: int) -> Optional[float]:
        """Return BTC-adjusted return over lookback bars."""
        bars = self.data_feed.get_bars(sym, lookback)
        btc_bars = self.data_feed.get_bars(self.btc_symbol, lookback)
        if len(bars) < lookback or len(btc_bars) < lookback:
            return None
        alt_ret = (bars["close"].iloc[-1] / bars["close"].iloc[0]) - 1.0
        btc_ret = (btc_bars["close"].iloc[-1] / btc_bars["close"].iloc[0]) - 1.0
        return alt_ret - btc_ret

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        # Only rebalance on the configured day
        if bar.timestamp.dayofweek != self.rebalance_day:
            return None

        alphas: Dict[str, float] = {}
        for sym in self.alt_symbols:
            alpha = self._compute_alpha(sym, self.lookback_days)
            if alpha is not None:
                alphas[sym] = alpha

        if len(alphas) < self.top_n + self.bottom_n:
            return None

        ranked = sorted(alphas.items(), key=lambda x: x[1], reverse=True)
        top = ranked[: self.top_n]
        bottom = ranked[-self.bottom_n :]

        # Emit signal for the strongest long candidate
        best_sym, best_alpha = top[0]
        if best_alpha > 0:
            return Signal(
                symbol=best_sym,
                side="BUY",
                strategy_name=self.name,
                strength=min(abs(best_alpha) * 10, 1.0),
            )

        # Emit signal for the weakest short candidate
        worst_sym, worst_alpha = bottom[-1]
        if worst_alpha < 0:
            return Signal(
                symbol=worst_sym,
                side="SELL",
                strategy_name=self.name,
                strength=min(abs(worst_alpha) * 10, 1.0),
            )

        return None
