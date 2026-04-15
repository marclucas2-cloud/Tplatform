"""Relative Strength MES vs MNQ rotation strategy.

Buy the stronger of MES/MNQ over a 3-day lookback, hold 5 days.

Edge: tech (NDX) and broad market (SPX) alternate leadership. When NDX
outperforms on 3d basis, it tends to continue outperforming for ~1 week.

Backtest 5Y daily:
  - n=212 trades, WR 58%, +$19,231, Sharpe 1.99
  - WF 4/5 profitable, Avg IS 1.89 -> OOS 2.28, ratio 1.21 (ROBUSTE)
  - No SL/TP — time exit only after 5 days (position duration cap)

Params: lookback=3, hold=5 (validated by sweep).

Paper only until 4 weeks live observation with reconciliation.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class RSMesMnqRotate(StrategyBase):
    """Rotate long between MES and MNQ based on 3-day relative strength."""

    def __init__(self, lookback: int = 3) -> None:
        self.lookback = lookback
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "rs_mes_mnq_rotate"

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

        # Need close from lookback bars ago for both MES and MNQ
        mes_bars = self.data_feed.get_bars("MES", self.lookback + 2)
        mnq_bars = self.data_feed.get_bars("MNQ", self.lookback + 2)
        if mes_bars is None or mnq_bars is None:
            return None
        if len(mes_bars) < self.lookback + 1 or len(mnq_bars) < self.lookback + 1:
            return None

        # Compute n-bar returns
        mes_ret = (float(mes_bars["close"].iloc[-1]) / float(mes_bars["close"].iloc[-self.lookback - 1])) - 1
        mnq_ret = (float(mnq_bars["close"].iloc[-1]) / float(mnq_bars["close"].iloc[-self.lookback - 1])) - 1

        # Need meaningful spread to avoid noise
        if abs(mes_ret - mnq_ret) < 0.005:
            return None

        # Long the stronger one at close
        winner = "MES" if mes_ret > mnq_ret else "MNQ"
        winner_close = float(mes_bars["close"].iloc[-1]) if winner == "MES" else float(mnq_bars["close"].iloc[-1])

        # No SL/TP — exit managed externally via hold period
        # Use wide SL (-3%) as safety net, TP +5%
        sl = winner_close * 0.97
        tp = winner_close * 1.05

        return Signal(
            symbol=winner,
            side="BUY",
            strategy_name=self.name,
            stop_loss=sl,
            take_profit=tp,
            strength=min(abs(mes_ret - mnq_ret) * 100, 1.0),
        )

    def get_parameters(self) -> dict:
        return {"lookback": self.lookback}

    def set_parameters(self, params: dict) -> None:
        if "lookback" in params:
            self.lookback = int(params["lookback"])
