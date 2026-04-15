"""Turn-of-Month momentum strategy.

Edge (Ariel 1987, Lakonishok & Smidt 1988): US stocks drift positively between
the last trading day of a month and the 3rd trading day of the next month,
driven by month-end rebalancing, pension fund inflows, and salary-investment.

Signal:
  - On the last trading day of each month: LONG top 10 stocks ranked by their
    prior 1-month return (momentum)
  - Exit at the close of the 3rd trading day of the next month

Universe: S&P 500 quality-filtered (~496 tickers from data/us_stocks/_universe.json).
Sizing: equal-weight, notional = capital / 10 per position.
Broker: Alpaca (paper first).

Backtest (2021-2026, 5Y daily, 3 bps costs):
  - 600 trades, 58% WR, +1.29% avg net, total +772% in unit sizing
  - Portfolio Sharpe 6.18 (10 concurrent), MaxDD -11.9%
  - WF: 5/5 windows profitable, OOS/IS = 1.11

Gate 5 Portfolio V15.3:
  - Sharpe 1.24 -> 1.49, MaxDD -32.7% -> -19.8% -> PASS
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict

import pandas as pd

from strategies_v2.us._common import (
    USPosition,
    build_price_matrix,
    load_universe,
    trading_month_ends,
    nth_trading_day_of_month,
)

logger = logging.getLogger("strat_tom")

NAME = "us_tom"


@dataclass
class TOMConfig:
    n_stocks: int = 10
    momentum_lookback_months: int = 1    # return over prior month for ranking
    hold_days: int = 3                    # exit on 3rd trading day of new month
    min_history_days: int = 60


class TOMStrategy:
    """Turn-of-Month momentum."""

    def __init__(self, config: TOMConfig | None = None):
        self.config = config or TOMConfig()
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
        """Return target positions for TOM on `as_of`.

        Returns an empty list if today is NOT a hold day (i.e., before last
        trading day of month, or after 3rd trading day of the next month).

        Returns the target positions (LONG top N momentum stocks, equal weight)
        if today is within the hold window.
        """
        tickers = self._universe()
        px = build_price_matrix(prices, tickers=tickers)
        if px.empty:
            logger.warning(f"{NAME}: empty price matrix")
            return []

        idx = px.index
        if len(idx) < self.config.min_history_days:
            return []

        as_of_ts = pd.Timestamp(as_of)
        month_ends = trading_month_ends(idx)
        if len(month_ends) < 2:
            return []

        # Most recent month-end <= as_of (entry anchor)
        past_ends = month_ends[month_ends <= as_of_ts]
        if len(past_ends) == 0:
            return []
        last_month_end = past_ends[-1]

        # Hold window = [last_month_end, last_month_end + hold_days trading days]
        # Count trading days elapsed SINCE entry UP TO as_of (0 on entry day).
        trading_days_since = int(((idx > last_month_end) & (idx <= as_of_ts)).sum())
        if trading_days_since >= self.config.hold_days:
            return []  # past hold window, flat
        if as_of_ts < last_month_end:
            return []  # before entry (shouldn't happen with past_ends logic, kept as safety)

        # Momentum lookback = prior month-end
        prev_ends = month_ends[month_ends < last_month_end]
        if len(prev_ends) == 0:
            return []
        mom_start = prev_ends[-1]

        try:
            px_entry = px.loc[last_month_end]
            px_prev = px.loc[mom_start]
        except KeyError:
            return []

        ret_1m = (px_entry / px_prev) - 1
        ret_1m = ret_1m.dropna()
        if len(ret_1m) < self.config.n_stocks:
            return []

        top = ret_1m.sort_values(ascending=False).head(self.config.n_stocks).index.tolist()
        pos_size = capital / self.config.n_stocks
        return [
            USPosition(
                strategy=NAME,
                symbol=t,
                side="BUY",
                notional=pos_size,
                reason=f"TOM top10 ret_1m={ret_1m[t]*100:+.1f}%",
            )
            for t in top
        ]
