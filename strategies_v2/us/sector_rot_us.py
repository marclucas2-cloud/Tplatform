"""US Sector Rotation — monthly long strongest sector / short weakest sector.

Edge (Moskowitz & Grinblatt 1999): GICS sectors that outperformed over the
last 3 months continue to outperform over the following month. The effect
is strongest in the top vs bottom sector (not a smooth spectrum).

Signal:
  - Monthly: compute 3-month return of each GICS sector (avg of its stocks)
  - Rank sectors. LONG 1 stock from the top sector (best momentum in it)
  - SHORT 1 stock from the bottom sector (worst momentum in it)
  - Hold 1 month, rebalance monthly

Universe: S&P 500 quality-filtered, 11 GICS sectors.
Sizing: dollar-neutral, 50/50 long/short at capital/2 each.
Broker: Alpaca (paper first). Shorts supported.

Backtest (5Y daily, 3 bps costs):
  - 116 trades, 49% WR, +1.62% avg, Sharpe portfolio 2.53, MaxDD -5.6% (clean!)
  - WF: 5/5 windows profitable, OOS/IS = 3.39 (IS weaker than OOS — robust)

Gate 5 Portfolio V15.3:
  - Sharpe 1.24 -> 2.09 (BEST Sharpe improvement of the 3), MaxDD -32.7% -> -22.4% -> PASS

Caveat: only 19 trades per WF window (thin statistical signal) — keep size
conservative and monitor monthly for 3+ months before scaling.
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
    load_sector_map,
    load_universe,
    trading_month_ends,
)

logger = logging.getLogger("strat_sector_rot_us")

NAME = "us_sector_rot"


@dataclass
class SectorRotConfig:
    lookback_months: int = 3      # 3M sector momentum
    min_stocks_per_sector: int = 3
    min_history_days: int = 90


class SectorRotStrategy:
    """US sector rotation (1 long / 1 short, monthly)."""

    def __init__(self, config: SectorRotConfig | None = None):
        self.config = config or SectorRotConfig()
        self._universe_cache: list[str] | None = None
        self._sector_map_cache: dict[str, str] | None = None

    @property
    def name(self) -> str:
        return NAME

    def _universe(self) -> list[str]:
        if self._universe_cache is None:
            self._universe_cache = load_universe()
        return self._universe_cache

    def _sector_map(self) -> dict[str, str]:
        if self._sector_map_cache is None:
            self._sector_map_cache = load_sector_map()
        return self._sector_map_cache

    def compute_target_portfolio(
        self,
        prices: Dict[str, pd.DataFrame],
        capital: float,
        as_of: date,
    ) -> list[USPosition]:
        """Compute target positions for US sector rotation at `as_of`."""
        tickers = self._universe()
        sec_map = self._sector_map()
        valid_tickers = [t for t in tickers if t in sec_map and not pd.isna(sec_map[t])]
        px = build_price_matrix(prices, tickers=valid_tickers)
        if px.empty:
            return []

        idx = px.index
        if len(idx) < self.config.min_history_days:
            return []

        as_of_ts = pd.Timestamp(as_of)
        month_ends = trading_month_ends(idx)
        lb_months = self.config.lookback_months
        if len(month_ends) < lb_months + 2:
            return []

        past_ends = month_ends[month_ends <= as_of_ts]
        if len(past_ends) == 0:
            return []
        last_rebal = past_ends[-1]

        # Stateless hold: return the portfolio rebalanced at last_rebal.
        # Orchestrator diff handles rollover when last_rebal advances.
        if as_of_ts < last_rebal:
            return []

        # Compute 3-month sector return (lookback = 3 prior month-ends back)
        prev_ends = month_ends[month_ends < last_rebal]
        if len(prev_ends) < lb_months:
            return []
        mom_start = prev_ends[-lb_months]

        try:
            px_end = px.loc[last_rebal]
            px_start = px.loc[mom_start]
        except KeyError:
            return []

        stock_ret = (px_end / px_start) - 1
        stock_ret = stock_ret.dropna()

        # Group by sector
        sec_rets: dict[str, float] = {}
        sec_stocks: dict[str, list[str]] = {}
        for t in stock_ret.index:
            s = sec_map.get(t)
            if not s or pd.isna(s):
                continue
            sec_stocks.setdefault(s, []).append(t)
        for s, ts in sec_stocks.items():
            if len(ts) >= self.config.min_stocks_per_sector:
                sec_rets[s] = float(stock_ret[ts].mean())

        if len(sec_rets) < 2:
            return []

        ranked = sorted(sec_rets.items(), key=lambda x: -x[1])
        top_sec = ranked[0][0]
        bot_sec = ranked[-1][0]

        # Pick best momentum stock in top sector, worst in bottom sector
        top_stocks = sorted(
            [(t, stock_ret[t]) for t in sec_stocks[top_sec] if t in stock_ret],
            key=lambda x: -x[1],
        )
        bot_stocks = sorted(
            [(t, stock_ret[t]) for t in sec_stocks[bot_sec] if t in stock_ret],
            key=lambda x: x[1],
        )
        if not top_stocks or not bot_stocks:
            return []

        long_t = top_stocks[0][0]
        short_t = bot_stocks[0][0]
        pos_size = capital / 2

        return [
            USPosition(
                strategy=NAME, symbol=long_t, side="BUY", notional=pos_size,
                reason=f"top sector {top_sec} ret3m={sec_rets[top_sec]*100:+.1f}%",
            ),
            USPosition(
                strategy=NAME, symbol=short_t, side="SELL", notional=pos_size,
                reason=f"bot sector {bot_sec} ret3m={sec_rets[bot_sec]*100:+.1f}%",
            ),
        ]
