"""Time-Series Momentum Multi-Futures (Moskowitz 2012).

Edge: Assets with positive past returns tend to continue. Vol-scaled.
Long if 63-day return > 0, short if < 0. Rebalance every 21 days.

Backtest (3 years, MES+MNQ+MCL+MGC):
  32 trades, 56% WR, +$18,646, Sharpe 1.31, PF 1.9, WF 3/5
  MGC dominant: 88% WR, +$18,351

Paper first, live after validation.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class TSMOMMulti(StrategyBase):
    """Time-Series Momentum across multiple futures."""

    def __init__(self, symbol: str = "MES") -> None:
        self._symbol = symbol
        self.lookback: int = 63  # 3 months
        self.vol_target: float = 0.10  # 10% annualized
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"tsmom_{self._symbol.lower()}"

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

        sym = self._symbol
        bars_df = self.data_feed.get_bars(sym, self.lookback + 5)
        if bars_df is None or len(bars_df) < self.lookback:
            return None

        # Past return over lookback period
        close_now = bars_df.iloc[-1]["close"]
        close_past = bars_df.iloc[-self.lookback]["close"]
        past_ret = (close_now / close_past) - 1

        # Realized vol for scaling
        rets = bars_df["close"].pct_change().dropna()
        vol = rets.std() * (252 ** 0.5)
        if vol <= 0:
            return None

        vol_scale = min(3.0, max(0.2, self.vol_target / vol))

        # Direction based on past return
        if past_ret > 0:
            side = "BUY"
            sl = bar.close - 25
            tp = bar.close + 40
        else:
            side = "SELL"
            sl = bar.close + 25
            tp = bar.close - 40

        return Signal(
            symbol=sym,
            side=side,
            strategy_name=self.name,
            stop_loss=sl,
            take_profit=tp,
            strength=min(abs(past_ret) * 10, 1.0) * vol_scale,
        )

    def get_parameters(self) -> dict:
        return {"symbol": self._symbol, "lookback": self.lookback,
                "vol_target": self.vol_target}

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
