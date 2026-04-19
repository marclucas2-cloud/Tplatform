"""Gold-Equity Divergence — Mean reversion on MES/MGC spread.

Edge: When gold and equities diverge (>2% vs <-1% over 5 days),
they tend to mean-revert within 5 days. Structural decorrelation
between safe haven (gold) and risk assets (equities).

Backtest (11 years, 2015-2026):
  114 trades, 53% WR, +$5,009, Sharpe 1.35, WF 5/8
  MES/MGC correlation = -0.34 (natural diversifier).

Signal:
  LONG MES if 5d return MES < -2% AND 5d return MGC > +1%
  SHORT MES if 5d return MES > +2% AND 5d return MGC < -1%
  EXIT: 5 days hold
  SL: 40 points ($200 per contract)

Paper first. 1 contract MES.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class GoldEquityDivergence(StrategyBase):
    """Mean reversion on gold/equity divergence."""

    SYMBOL = "MES"

    def __init__(self) -> None:
        self.mes_threshold: float = 0.02  # 2%
        self.mgc_threshold: float = 0.01  # 1%
        self.sl_points: float = 40.0
        self.tp_points: float = 60.0
        self.lookback: int = 5
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "gold_equity_divergence"

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

        mes_bars = self.data_feed.get_bars(self.SYMBOL, self.lookback + 2)
        mgc_bars = self.data_feed.get_bars("MGC", self.lookback + 2)

        if mes_bars is None or mgc_bars is None:
            return None
        if len(mes_bars) < self.lookback or len(mgc_bars) < self.lookback:
            return None

        mes_ret = mes_bars["close"].iloc[-1] / mes_bars["close"].iloc[-self.lookback] - 1
        mgc_ret = mgc_bars["close"].iloc[-1] / mgc_bars["close"].iloc[-self.lookback] - 1

        # MES up big + MGC down -> SHORT MES (expect reversal)
        if mes_ret > self.mes_threshold and mgc_ret < -self.mgc_threshold:
            return Signal(
                symbol=self.SYMBOL,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + self.sl_points,
                take_profit=bar.close - self.tp_points,
                strength=min(abs(mes_ret - mgc_ret) * 10, 1.0),
            )

        # MES down big + MGC up -> LONG MES (expect bounce)
        if mes_ret < -self.mes_threshold and mgc_ret > self.mgc_threshold:
            return Signal(
                symbol=self.SYMBOL,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - self.sl_points,
                take_profit=bar.close + self.tp_points,
                strength=min(abs(mes_ret - mgc_ret) * 10, 1.0),
            )

        return None

    def get_parameters(self) -> dict:
        return {
            "mes_threshold": self.mes_threshold,
            "mgc_threshold": self.mgc_threshold,
            "sl_points": self.sl_points,
            "lookback": self.lookback,
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
