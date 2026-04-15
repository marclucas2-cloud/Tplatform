"""Overnight Buy-Close-Sell-Open for MES/MNQ — REJECTED 15 avril 2026.

Edge: Equity indices SHOULD have a positive overnight return premium.
Buy at close, sell at next day open. EMA20 trend filter.

ORIGINAL claim (UNVERIFIED, from some old backtest):
  MES: 208 trades, 60% WR, +$13,546, Sharpe 3.85, PF 2.5, WF 4/5
  MNQ: 186 trades, 58% WR, +$27,499, Sharpe 4.14, PF 2.6, WF 5/5

REAL backtest 15 avril 2026 (this production logic, 5Y daily, 60 combo sweep):
  MES: 322 trades, 39% WR, +$593, **Sharpe 0.07**, WF OOS -0.68, overfit IS 0.71->OOS 0.01
  MNQ: similar or worse (sweep bottom: Sharpe -13.65 on tight SL variants)
  Best OF ALL 60 combos: MES 30/50 ema50 none → Sharpe 0.07 (statistical noise)

Verdict: NO edge. Strategy disabled in worker.py per user decision 15 avril.
Kept in code for potential v2 iteration with additional filters (regime,
VIX, ADX) in research mode only — NOT for live deploy without full WF
rerun with real prod logic matching.

Old claim was PROPAGATED through CLAUDE.md + dashboard/api/chat.py without
ever being re-verified. This is a governance lesson — docstring claims
must be independently re-backtested before live deployment.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class OvernightBuyClose(StrategyBase):
    """Buy at close, sell at next open. EMA20 trend filter."""

    def __init__(self, symbol: str = "MES") -> None:
        self._symbol = symbol
        self.ema_period: int = 20
        self.sl_points: float = 30.0  # safety net SL
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"overnight_bc_{self._symbol.lower()}"

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

        ema = self.data_feed.get_indicator(self._symbol, "ema", self.ema_period)
        if ema is None:
            return None

        if bar.close > ema:
            return Signal(
                symbol=self._symbol,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - self.sl_points,
                take_profit=bar.close + 50,
                strength=min((bar.close - ema) / ema * 100, 1.0),
            )
        return None

    def get_parameters(self) -> dict:
        return {"symbol": self._symbol, "ema_period": self.ema_period}

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
