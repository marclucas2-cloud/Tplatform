"""EU Opening Range Breakout Paris strategy for BacktesterV2.

Hypothesis: The CAC40 opening range (09:00-09:30 CET) captures overnight
positioning and pre-market institutional orders. Breakouts from this range
with volume confirmation tend to follow through during the European morning
session as Euronext Paris liquidity builds.

Entry rules (09:30-12:00 CET = 08:30-11:00 UTC):
  1. Compute opening range: high/low of 08:00-08:30 UTC (09:00-09:30 CET)
  2. Volume confirmation: current bar volume > vol_mult * avg 20-bar volume
  3. LONG if close > opening_high + buffer (0.1 * range_width)
  4. SHORT if close < opening_low - buffer (0.1 * range_width)
  5. One trade per session maximum

Exit rules:
  - Stop loss: opposite side of opening range
  - Take profit: 2x risk from entry
  - Time exit: 11:00 UTC (12:00 CET) if neither SL nor TP hit

Symbol: CAC40 (Euronext Paris)
Timeframe: 15min or 1H bars
Expected: ~8-12 trades/month, win rate 45-55%, Sharpe 0.8-1.5
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

# Opening range: 08:00-08:30 UTC (= 09:00-09:30 CET)
_OR_START_HOUR_UTC = 8
_OR_END_HOUR_UTC = 8
_OR_END_MIN_UTC = 30

# Entry window: 08:30-11:00 UTC (= 09:30-12:00 CET)
_ENTRY_START_HOUR_UTC = 8
_ENTRY_START_MIN_UTC = 30
_ENTRY_END_HOUR_UTC = 11

# Time exit: 11:00 UTC (= 12:00 CET)
_TIME_EXIT_HOUR_UTC = 11

STRATEGY_CONFIG = {
    "name": "eu_orb_paris",
    "id": "EU-ORB-PAR",
    "market_type": "eu_equity",
    "broker": "ibkr",
    "timeframe": "1H",
}


class EUORBParis(StrategyBase):
    """Opening Range Breakout on CAC40 — first 30 min Paris session range."""

    SYMBOL = "CAC40"

    def __init__(self) -> None:
        # Range parameters
        self.buffer_pct: float = 0.1  # Buffer as fraction of range width
        self.vol_mult: float = 1.5  # Volume must exceed vol_mult * avg
        self.vol_avg_period: int = 20  # Bars for average volume
        self.tp_risk_mult: float = 2.0  # TP = entry +/- risk * tp_risk_mult

        # State tracking
        self._or_high: float | None = None
        self._or_low: float | None = None
        self._or_computed_date: str | None = None
        self._position_open: bool = False

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "eu_orb_paris"

    @property
    def asset_class(self) -> str:
        return "eu_equity"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        sym = self.SYMBOL
        hour = bar.timestamp.hour
        minute = bar.timestamp.minute if hasattr(bar.timestamp, "minute") else 0
        bar_minutes = hour * 60 + minute

        # Compute opening range once per day, after the OR period ends
        bar_date = str(bar.timestamp.date())
        if bar_date != self._or_computed_date and bar_minutes >= _OR_END_HOUR_UTC * 60 + _OR_END_MIN_UTC:
            self._compute_opening_range(sym, bar.timestamp)
            self._or_computed_date = bar_date
            self._position_open = False

        # Time exit: no new trades after 11:00 UTC
        if hour >= _TIME_EXIT_HOUR_UTC:
            return None

        # Only enter during entry window: 08:30-11:00 UTC
        if bar_minutes < _ENTRY_START_HOUR_UTC * 60 + _ENTRY_START_MIN_UTC:
            return None

        # Need valid opening range
        if self._or_high is None or self._or_low is None:
            return None

        # Already traded today
        if self._position_open:
            return None

        # Volume confirmation
        avg_vol = self._get_avg_volume(sym)
        if avg_vol is not None and avg_vol > 0:
            if bar.volume < self.vol_mult * avg_vol:
                return None

        range_width = self._or_high - self._or_low
        if range_width <= 0:
            return None

        buffer = self.buffer_pct * range_width

        # LONG breakout
        if bar.close > self._or_high + buffer:
            sl = self._or_low  # SL at opposite side of range
            risk = bar.close - sl
            tp = bar.close + self.tp_risk_mult * risk
            self._position_open = True
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=min(bar.volume / (avg_vol * self.vol_mult) if avg_vol and avg_vol > 0 else 1.0, 1.0),
            )

        # SHORT breakout
        if bar.close < self._or_low - buffer:
            sl = self._or_high  # SL at opposite side of range
            risk = sl - bar.close
            tp = bar.close - self.tp_risk_mult * risk
            self._position_open = True
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=min(bar.volume / (avg_vol * self.vol_mult) if avg_vol and avg_vol > 0 else 1.0, 1.0),
            )

        return None

    # ------------------------------------------------------------------
    # Opening range computation
    # ------------------------------------------------------------------

    def _compute_opening_range(self, symbol: str, current_ts) -> None:
        """Compute high/low of the opening range (08:00-08:30 UTC) for today.

        Anti-lookahead: DataFeed only returns closed bars before current_ts.
        """
        if self.data_feed is None:
            self._or_high = None
            self._or_low = None
            return

        # Fetch enough bars to cover the opening range
        bars_df = self.data_feed.get_bars(symbol, 24)
        if bars_df.empty:
            self._or_high = None
            self._or_low = None
            return

        today = current_ts.date()
        # Filter to opening range hours of today: 08:00-08:29 UTC
        or_mask = (
            (bars_df.index.date == today)
            & (bars_df.index.hour == _OR_START_HOUR_UTC)
            & (bars_df.index.minute < _OR_END_MIN_UTC)
        )
        # Also include bars at exactly 08:00 if hourly bars
        or_mask_hourly = (
            (bars_df.index.date == today)
            & (bars_df.index.hour == _OR_START_HOUR_UTC)
        )
        or_bars = bars_df[or_mask | or_mask_hourly]

        if or_bars.empty:
            self._or_high = None
            self._or_low = None
            return

        self._or_high = float(or_bars["high"].max())
        self._or_low = float(or_bars["low"].min())

    def _get_avg_volume(self, symbol: str) -> float | None:
        """Compute average volume over recent bars for volume confirmation."""
        if self.data_feed is None:
            return None

        bars_df = self.data_feed.get_bars(symbol, self.vol_avg_period)
        if bars_df.empty or "volume" not in bars_df.columns:
            return None

        return float(bars_df["volume"].mean())

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "buffer_pct": self.buffer_pct,
            "vol_mult": self.vol_mult,
            "vol_avg_period": self.vol_avg_period,
            "tp_risk_mult": self.tp_risk_mult,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "buffer_pct": [0.05, 0.1, 0.15, 0.2],
            "vol_mult": [1.2, 1.5, 1.8, 2.0],
            "vol_avg_period": [10, 20, 30],
            "tp_risk_mult": [1.5, 2.0, 2.5, 3.0],
        }

    def on_eod(self, timestamp) -> None:
        """Reset daily state at end of day."""
        self._position_open = False
        self._or_high = None
        self._or_low = None
        self._or_computed_date = None
