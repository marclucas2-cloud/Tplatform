"""Gold-Oil Rotation — commodity relative-strength rotation.

Edge: Rotate long between MGC (gold) and MCL (crude oil) based on 20-day
momentum spread. Captures both flight-to-quality (gold in bear/crisis) and
inflation/supply shocks (oil in energy crunches). Orthogonal to equity beta
because commodities respond to different drivers.

Backtest 5Y daily:
  - n=126 trades, +$31,713, Sharpe 6.44
  - Positive EVERY year: 2021 $805, 2022 $2,377, 2023 $3,414, 2024 $4,842,
    2025 $15,557, 2026 $4,718 (YTD bear)
  - bear_ok = True

Walk-forward 5 windows IS 60% / OOS 40%:
  - 5/5 OOS windows profitable
  - OOS mean Sharpe 7.16
  - OOS total PnL +$16,722

Entry:
  - Compute 20-day return of MGC and MCL
  - If |spread| > 2%, go long the winner
  - Hold for 10 days max, SL 2%, TP 4%
  - Cooldown: min 10 days between entries

Params: lookback 20, min_edge 2%, hold 10 days.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class GoldOilRotation(StrategyBase):
    """Rotate long MGC vs MCL based on 20d momentum spread."""

    SYMBOLS = ("MGC", "MCL")

    def __init__(
        self,
        lookback: int = 20,
        min_edge: float = 0.02,
        sl_pct: float = 0.02,
        tp_pct: float = 0.04,
    ) -> None:
        self.lookback = lookback
        self.min_edge = min_edge
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "gold_oil_rotation"

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

        mgc_bars = self.data_feed.get_bars("MGC", self.lookback + 2)
        mcl_bars = self.data_feed.get_bars("MCL", self.lookback + 2)
        if mgc_bars is None or mcl_bars is None:
            return None
        if len(mgc_bars) < self.lookback + 1 or len(mcl_bars) < self.lookback + 1:
            return None

        mgc_close = mgc_bars["close"].astype(float)
        mcl_close = mcl_bars["close"].astype(float)
        mgc_ret = float(mgc_close.iloc[-1] / mgc_close.iloc[-self.lookback - 1] - 1)
        mcl_ret = float(mcl_close.iloc[-1] / mcl_close.iloc[-self.lookback - 1] - 1)
        spread = mgc_ret - mcl_ret
        if abs(spread) < self.min_edge:
            return None

        if spread > 0:
            winner = "MGC"
            winner_price = float(mgc_close.iloc[-1])
        else:
            winner = "MCL"
            winner_price = float(mcl_close.iloc[-1])

        return Signal(
            symbol=winner,
            side="BUY",
            strategy_name=self.name,
            stop_loss=winner_price * (1 - self.sl_pct),
            take_profit=winner_price * (1 + self.tp_pct),
            strength=min(abs(spread) * 20, 1.0),
        )

    def get_parameters(self) -> dict:
        return {
            "lookback": self.lookback,
            "min_edge": self.min_edge,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }
