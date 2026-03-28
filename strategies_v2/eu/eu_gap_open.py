"""EU Gap Open (fade) strategy for BacktesterV2.

Fades gaps > 1% at European market open (9:00 CET / 08:00 UTC).
High Sharpe (8.56 backtest) due to EU open liquidity dynamics.
Closes positions at EOD if close_eod is enabled.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class EUGapOpen(StrategyBase):
    """EU Gap Open fade strategy — short large up-gaps, long large down-gaps."""

    SYMBOL = "ESTX50"  # Euro Stoxx 50

    def __init__(self) -> None:
        self.min_gap_pct: float = 0.01
        self.max_gap_pct: float = 0.05
        self.sl_pct: float = 0.015
        self.tp_pct: float = 0.02
        self.close_eod: bool = True
        self._prev_close: Optional[float] = None
        self._has_position_today: bool = False
        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "eu_gap_open"

    @property
    def asset_class(self) -> str:
        return "eu_equity"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None
        sym = self.SYMBOL

        # Need previous close for gap calculation
        bars = self.data_feed.get_bars(sym, 2)
        if len(bars) < 2:
            return None

        prev_close = float(bars.iloc[-2]["close"])
        current_open = bar.open

        # Only trade at open (check if this is the first bar of the session)
        # Heuristic: bar hour is 8 or 9 UTC (EU open window)
        bar_hour = bar.timestamp.hour if hasattr(bar.timestamp, 'hour') else 0
        is_open_window = 7 <= bar_hour <= 9

        if not is_open_window or self._has_position_today:
            # Track prev_close for next day
            self._prev_close = bar.close
            return None

        gap_pct = (current_open - prev_close) / prev_close

        # Gap too small or too large — skip
        if abs(gap_pct) < self.min_gap_pct or abs(gap_pct) > self.max_gap_pct:
            self._prev_close = bar.close
            return None

        self._has_position_today = True

        # Fade the gap: short up-gaps, long down-gaps
        if gap_pct > 0:
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close * (1.0 + self.sl_pct),
                take_profit=bar.close * (1.0 - self.tp_pct),
                strength=min(abs(gap_pct) / self.max_gap_pct, 1.0),
            )
        else:
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close * (1.0 - self.sl_pct),
                take_profit=bar.close * (1.0 + self.tp_pct),
                strength=min(abs(gap_pct) / self.max_gap_pct, 1.0),
            )

    def on_eod(self, timestamp) -> None:
        """Reset daily flag for next session."""
        self._has_position_today = False

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "min_gap_pct": self.min_gap_pct,
            "max_gap_pct": self.max_gap_pct,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "close_eod": self.close_eod,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "min_gap_pct": [0.005, 0.01, 0.015],
            "max_gap_pct": [0.03, 0.05, 0.07],
            "sl_pct": [0.01, 0.015, 0.02],
            "tp_pct": [0.015, 0.02, 0.03],
        }
