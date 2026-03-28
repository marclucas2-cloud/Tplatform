"""Volatility Breakout — Vol compression then breakout with confirmation.

Detects low vol (7d/30d < threshold), enters on confirmed breakout with
elevated volume. BTC margin, long or short per breakout direction.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class VolBreakout(StrategyBase):
    """Vol compression (vol_7d/vol_30d < 0.5), breakout with confirmation."""

    broker = "BINANCE"

    def __init__(self, data_feed: DataFeed, symbol: str = "BTCUSDT") -> None:
        self.data_feed = data_feed
        self.symbol = symbol
        # Tunable parameters
        self.compression_ratio = 0.5
        self.breakout_atr_mult = 0.3
        self.confirmation_bars = 2
        self.volume_mult = 2.0
        self.sl_atr = 1.5
        self.tp_atr = 3.0

    @property
    def name(self) -> str:
        return "vol_breakout"

    @property
    def asset_class(self) -> str:
        return "CRYPTO_BTC"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "compression_ratio": self.compression_ratio,
            "breakout_atr_mult": self.breakout_atr_mult,
            "confirmation_bars": self.confirmation_bars,
            "volume_mult": self.volume_mult,
            "sl_atr": self.sl_atr,
            "tp_atr": self.tp_atr,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @staticmethod
    def get_parameter_grid() -> Dict[str, List[Any]]:
        return {
            "compression_ratio": [0.4, 0.5, 0.6],
            "breakout_atr_mult": [0.2, 0.3, 0.5],
            "confirmation_bars": [1, 2, 3],
            "volume_mult": [1.5, 2.0, 3.0],
            "sl_atr": [1.0, 1.5, 2.0],
            "tp_atr": [2.0, 3.0, 4.0],
        }

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        sym = bar.symbol
        bars_30 = self.data_feed.get_bars(sym, 30)
        if len(bars_30) < 30:
            return None

        atr = self.data_feed.get_indicator(sym, "atr", 14)
        if atr is None:
            return None

        # Vol ratio: 7d std / 30d std
        returns = bars_30["close"].pct_change().dropna()
        if len(returns) < 20:
            return None
        vol_7d, vol_30d = float(returns.iloc[-7:].std()), float(returns.std())
        if vol_30d == 0:
            return None
        ratio = vol_7d / vol_30d
        if ratio >= self.compression_ratio:
            return None

        # Breakout: N consecutive same-direction bars + volume spike
        n = self.confirmation_bars
        recent = bars_30.iloc[-n:]
        moves = recent["close"].values - recent["open"].values
        avg_vol = float(bars_30["volume"].mean())
        price = bar.close

        if all(m > 0 for m in moves) and bar.volume > avg_vol * self.volume_mult:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price - self.sl_atr * atr,
                take_profit=price + self.tp_atr * atr,
                strength=min((self.compression_ratio - ratio) * 5, 1.0),
            )

        if all(m < 0 for m in moves) and bar.volume > avg_vol * self.volume_mult:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=price + self.sl_atr * atr,
                take_profit=price - self.tp_atr * atr,
                strength=min((self.compression_ratio - ratio) * 5, 1.0),
            )

        return None
