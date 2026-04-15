"""Relative Strength vs SPY — cross-sectional market-neutral.

Edge: stocks that outperform SPY over the prior month continue to outperform
over the following month. Symmetrically, underperformers keep underperforming.
This is the cross-sectional equity version of the well-documented momentum
factor (Jegadeesh & Titman 1993).

Signal:
  - Last trading day of each month, compute 1-month alpha = ret_stock - ret_SPY
  - LONG top 5 (strongest alpha)
  - SHORT bottom 5 (weakest alpha)
  - Hold 1 month, rebalance monthly

Universe: S&P 500 quality-filtered.
Sizing: dollar-neutral, notional = capital / 10 per position.
Broker: Alpaca (paper first). Shorts supported.

Backtest (5Y daily, 3 bps costs):
  - 600 trades, 54% WR, +1.47% avg, Sharpe 3.83 portfolio
  - WF: 3/5 windows profitable, OOS/IS 0.79
  - MaxDD -31% (significant vs tom/sector_rot — sensitive to regime)

Gate 5 Portfolio V15.3:
  - Sharpe 1.24 -> 1.63, MaxDD -32.7% -> -21.1% -> PASS

Warning: 2 consecutive losing windows mid-backtest (regime sensitivity).
Size conservatively in paper.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict

import pandas as pd

from strategies_v2.us._common import (
    USPosition,
    build_price_matrix,
    load_universe,
    trading_month_ends,
)

logger = logging.getLogger("strat_rs_spy")

NAME = "us_rs_spy"


@dataclass
class RSSpyConfig:
    top_n: int = 5
    lookback_months: int = 1
    min_history_days: int = 60


class RSSpyStrategy:
    """Relative Strength vs SPY (market-neutral, top N / bottom N)."""

    def __init__(self, config: RSSpyConfig | None = None):
        self.config = config or RSSpyConfig()
        self._universe_cache: list[str] | None = None

    @property
    def name(self) -> str:
        return NAME

    def _universe(self) -> list[str]:
        if self._universe_cache is None:
            self._universe_cache = load_universe()
        return self._universe_cache

    def compute_target_portfolio(
        self,
        prices: Dict[str, pd.DataFrame],
        capital: float,
        as_of: date,
    ) -> list[USPosition]:
        """Compute target portfolio for RS vs SPY at `as_of`.

        Holds positions from last trading day of previous month through last
        trading day of current month. Rebalanced monthly.
        """
        if "SPY" not in prices:
            logger.warning(f"{NAME}: SPY price series missing")
            return []

        tickers = self._universe()
        px = build_price_matrix(prices, tickers=tickers)
        if px.empty:
            return []

        idx = px.index
        if len(idx) < self.config.min_history_days:
            return []

        as_of_ts = pd.Timestamp(as_of)
        month_ends = trading_month_ends(idx)
        if len(month_ends) < 2:
            return []

        past_ends = month_ends[month_ends <= as_of_ts]
        if len(past_ends) == 0:
            return []
        last_rebal = past_ends[-1]

        # Stateless hold: as long as as_of >= last_rebal, return the portfolio
        # rebalanced at last_rebal. When month changes and last_rebal updates,
        # the orchestrator diff handles the rollover.
        if as_of_ts < last_rebal:
            return []

        # Compute 1-month alpha up to last_rebal (lookback = prior month-end)
        prev_ends = month_ends[month_ends < last_rebal]
        if len(prev_ends) == 0:
            return []
        prev = prev_ends[-1]

        try:
            px_end = px.loc[last_rebal]
            px_prev = px.loc[prev]
            spy_end = prices["SPY"]["close"].loc[last_rebal]
            spy_prev = prices["SPY"]["close"].loc[prev]
        except KeyError:
            return []

        stock_ret = (px_end / px_prev) - 1
        spy_ret = (spy_end / spy_prev) - 1
        alpha = (stock_ret - spy_ret).dropna()
        if len(alpha) < 2 * self.config.top_n:
            return []

        sorted_alpha = alpha.sort_values(ascending=False)
        longs = sorted_alpha.head(self.config.top_n).index.tolist()
        shorts = sorted_alpha.tail(self.config.top_n).index.tolist()

        pos_size = capital / (2 * self.config.top_n)
        positions = []
        for t in longs:
            positions.append(USPosition(
                strategy=NAME, symbol=t, side="BUY", notional=pos_size,
                reason=f"RS long alpha={alpha[t]*100:+.1f}%",
            ))
        for t in shorts:
            positions.append(USPosition(
                strategy=NAME, symbol=t, side="SELL", notional=pos_size,
                reason=f"RS short alpha={alpha[t]*100:+.1f}%",
            ))
        return positions
