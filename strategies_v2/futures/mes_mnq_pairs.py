"""MES-MNQ Pairs Spread Mean Reversion for BacktesterV2.

FUT-008: Micro S&P 500 (MES) vs Micro Nasdaq (MNQ) Relative Value Pairs.
CME, combined margin ~$3,200 ($1,400 MES + $1,800 MNQ).

EDGE: The S&P 500 and Nasdaq 100 are highly correlated (rho > 0.90) but
diverge temporarily due to sector rotation (tech vs value). When the
normalized spread deviates beyond 2 standard deviations, mean reversion
is reliable over 3-10 day horizons. This is a classic statistical
arbitrage on index futures (Gatev, Goetzmann & Rouwenhorst, "Pairs Trading",
2006).

We normalize the spread as Z-score = (spread - SMA(20)) / StdDev(20).
Long the underperformer / short the outperformer when |Z| > threshold.

Rules:
- Spread: log(MES/MES_base) - log(MNQ/MNQ_base) rolling ratio
- Signal: Z-score of spread > 2.0 or < -2.0
- Long MES / Short MNQ when Z > 2.0 (Nasdaq overextended)
- Long MNQ / Short MES when Z < -2.0 (S&P overextended)
- Exit: Z-score returns to +/- 0.5 (partial mean reversion)
- Stop: Z-score exceeds +/- 3.5 (divergence, not convergence)
- Holding: 3-10 days typical
- ~4-6 trades/month
- Filter: correlation(20) > 0.80 (spread only works if correlated)
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MESMNQPairs(StrategyBase):
    """MES-MNQ pairs spread mean reversion.

    Trades the S&P 500 vs Nasdaq 100 relative value spread. When the
    Z-score of the log-ratio exceeds the threshold, enter a pairs trade
    betting on convergence. Natural hedge reduces directional risk.
    """

    SYMBOL_A = "MES"  # S&P 500
    SYMBOL_B = "MNQ"  # Nasdaq 100
    # We emit signals on MES; the engine must hedge with MNQ
    SYMBOL = "MES"

    def __init__(self) -> None:
        self.lookback: int = 20            # Z-score lookback
        self.z_entry: float = 2.0          # entry threshold
        self.z_exit: float = 0.5           # exit threshold (mean reversion target)
        self.z_stop: float = 3.5           # stop threshold (divergence)
        self.min_correlation: float = 0.80  # minimum rolling correlation
        self.sl_points: float = 30.0       # hard stop in MES points
        self.tp_points: float = 20.0       # target in MES points
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mes_mnq_pairs"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def _compute_zscore(self, bars_a, bars_b) -> float | None:
        """Compute Z-score of log-ratio spread between two series."""
        if len(bars_a) < self.lookback or len(bars_b) < self.lookback:
            return None

        # Align by taking last N bars from each
        close_a = bars_a["close"].values[-self.lookback:]
        close_b = bars_b["close"].values[-self.lookback:]

        if len(close_a) != len(close_b):
            min_len = min(len(close_a), len(close_b))
            close_a = close_a[-min_len:]
            close_b = close_b[-min_len:]

        if len(close_a) < self.lookback:
            return None

        # Check for zero/negative prices
        if np.any(close_a <= 0) or np.any(close_b <= 0):
            return None

        # Log ratio spread
        log_ratio = np.log(close_a) - np.log(close_b)
        spread_mean = np.mean(log_ratio)
        spread_std = np.std(log_ratio, ddof=1)

        if spread_std <= 0 or np.isnan(spread_std):
            return None

        # Z-score of the latest observation
        z = (log_ratio[-1] - spread_mean) / spread_std
        return float(z)

    def _compute_correlation(self, bars_a, bars_b) -> float | None:
        """Compute rolling correlation between two price series."""
        if len(bars_a) < self.lookback or len(bars_b) < self.lookback:
            return None

        ret_a = bars_a["close"].pct_change().dropna().values[-self.lookback:]
        ret_b = bars_b["close"].pct_change().dropna().values[-self.lookback:]

        min_len = min(len(ret_a), len(ret_b))
        if min_len < self.lookback - 2:
            return None

        ret_a = ret_a[-min_len:]
        ret_b = ret_b[-min_len:]

        if np.std(ret_a) == 0 or np.std(ret_b) == 0:
            return None

        corr = float(np.corrcoef(ret_a, ret_b)[0, 1])
        return corr

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        # Get bars for both symbols
        bars_a = self.data_feed.get_bars(self.SYMBOL_A, self.lookback + 5)
        bars_b = self.data_feed.get_bars(self.SYMBOL_B, self.lookback + 5)

        if bars_a is None or bars_b is None:
            return None
        if len(bars_a) < self.lookback or len(bars_b) < self.lookback:
            return None

        # Correlation filter
        corr = self._compute_correlation(bars_a, bars_b)
        if corr is None or corr < self.min_correlation:
            return None

        # Z-score of spread
        z = self._compute_zscore(bars_a, bars_b)
        if z is None:
            return None

        price = bar.close

        # Z > entry threshold: MES is rich vs MNQ -> SHORT MES, LONG MNQ
        # We emit SELL on MES (the engine/pairs manager handles the MNQ leg)
        if z > self.z_entry:
            return Signal(
                symbol=self.SYMBOL_A,
                side="SELL",
                strategy_name=self.name,
                stop_loss=price + self.sl_points,
                take_profit=price - self.tp_points,
                strength=min((z - self.z_entry) / 2.0, 1.0),
            )

        # Z < -entry threshold: MES is cheap vs MNQ -> LONG MES, SHORT MNQ
        if z < -self.z_entry:
            return Signal(
                symbol=self.SYMBOL_A,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price - self.sl_points,
                take_profit=price + self.tp_points,
                strength=min((-z - self.z_entry) / 2.0, 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "lookback": self.lookback,
            "z_entry": self.z_entry,
            "z_exit": self.z_exit,
            "z_stop": self.z_stop,
            "min_correlation": self.min_correlation,
            "sl_points": self.sl_points,
            "tp_points": self.tp_points,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "lookback": [15, 20, 25, 30],
            "z_entry": [1.5, 2.0, 2.5],
            "z_exit": [0.3, 0.5, 0.7],
            "z_stop": [3.0, 3.5, 4.0],
            "min_correlation": [0.75, 0.80, 0.85],
            "sl_points": [20.0, 30.0, 40.0],
            "tp_points": [15.0, 20.0, 30.0],
        }
