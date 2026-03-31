"""EU BCE Press Conference strategy for BacktesterV2.

Event-driven strategy trading ECB (BCE) press conference days. The ECB
holds 8 monetary policy meetings per year with press conferences at 13:45
CET. This strategy enters 15 minutes after the statement release (14:00
CET = 13:00 UTC) based on the initial price reaction of bank stocks.

Hypothesis: Bank stocks overreact to ECB rate decisions in the first 15
minutes as algorithmic traders parse the statement. Continuation in the
direction of the initial move is profitable because institutional
positioning follows within the same session.

Entry (14:00 CET / 13:00 UTC on ECB meeting days):
  - Compute price change of bank basket in first 15 min after statement
  - If reaction > +0.3% (dovish): LONG bank stocks (rates lower = margin squeeze priced in)
  - If reaction < -0.3% (hawkish): SHORT bank stocks (rates higher = risk of loan losses)
  - Reaction between -0.3% and +0.3%: no trade (ambiguous)

Symbols: BNP.PA (BNP Paribas), DBK.DE (Deutsche Bank), ING.AS (ING Group)
SL: 1.5%, TP: 3%
Exit: EOD if neither SL nor TP hit

Timeframe: Intraday (hourly bars minimum)
Expected: ~8 trades/year, high win rate on directional moves
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


# Bank stocks basket
BANK_SYMBOLS = ["BNP.PA", "DBK.DE", "ING.AS"]

# ECB press conference schedule: approximate dates (month, day) for 2025-2026.
# In production, this should be loaded from a calendar or config file.
# Meetings are typically on Thursdays.
ECB_MEETING_MONTHS_DAYS = [
    (1, 30), (3, 6), (4, 17), (6, 5),
    (7, 24), (9, 11), (10, 30), (12, 18),
]

# ECB statement release is 13:45 CET = 12:45 UTC.
# Entry at 14:00 CET = 13:00 UTC.
_ENTRY_HOUR_UTC = 13
_EOD_EXIT_HOUR_UTC = 16  # close by 16:00 UTC if no SL/TP hit

# Minimum reaction threshold to trade
_MIN_REACTION_PCT = 0.003  # 0.3%

STRATEGY_CONFIG = {
    "name": "EU BCE Press Conference",
    "id": "EU-BCE-PC",
    "symbols": BANK_SYMBOLS,
    "market_type": "eu_equity",
    "broker": "ibkr",
    "timeframe": "1H",
    "frequency": "event_driven",
    "allocation_pct": 0.05,
    "events_per_year": 8,
}


class EUBCEPressConference(StrategyBase):
    """EU BCE Press Conference -- event-driven bank stock trading on ECB days."""

    def __init__(self, symbol: str = "BNP.PA") -> None:
        if symbol not in BANK_SYMBOLS:
            raise ValueError(
                f"Unsupported symbol {symbol}. Must be one of {BANK_SYMBOLS}"
            )
        self._symbol = symbol

        # Parameters (tunable via WF)
        self.min_reaction_pct: float = _MIN_REACTION_PCT
        self.sl_pct: float = 0.015  # 1.5% stop loss
        self.tp_pct: float = 0.03   # 3% take profit
        self.close_eod: bool = True

        # State
        self._traded_today: bool = False
        self._pre_announcement_price: Optional[float] = None
        self._is_ecb_day: bool = False
        self._last_checked_date: Optional[str] = None

        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return f"eu_bce_pc_{self._symbol.lower().replace('.', '_')}"

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
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None

        sym = self._symbol
        hour = bar.timestamp.hour
        bar_date = str(bar.timestamp.date())

        # Check if today is an ECB meeting day (once per day)
        if bar_date != self._last_checked_date:
            self._last_checked_date = bar_date
            self._traded_today = False
            self._pre_announcement_price = None
            month = bar.timestamp.month
            day = bar.timestamp.day
            self._is_ecb_day = (month, day) in ECB_MEETING_MONTHS_DAYS

        if not self._is_ecb_day:
            return None

        if self._traded_today:
            return None

        # Capture pre-announcement price (bar before entry window)
        # The bar closing at 13:00 UTC (12:00-13:00) is the last bar before statement
        if hour == 12 and self._pre_announcement_price is None:
            self._pre_announcement_price = bar.close
            return None

        # Entry window: 13:00 UTC (14:00 CET, 15 min after statement)
        if hour != _ENTRY_HOUR_UTC:
            return None

        if self._pre_announcement_price is None:
            # Fallback: use previous bar's close
            bars = self.data_feed.get_bars(sym, 2)
            if len(bars) < 2:
                return None
            self._pre_announcement_price = float(bars.iloc[-2]["close"])

        # Calculate price reaction
        reaction = (bar.close - self._pre_announcement_price) / self._pre_announcement_price

        # Ambiguous reaction: no trade
        if abs(reaction) < self.min_reaction_pct:
            self._traded_today = True  # do not retry later
            return None

        self._traded_today = True
        price = bar.close

        # Dovish (price up): long banks (lower rates = cheap funding)
        # Hawkish (price down): short banks (higher rates = loan risk)
        if reaction > 0:
            side = "BUY"
            sl = price * (1.0 - self.sl_pct)
            tp = price * (1.0 + self.tp_pct)
        else:
            side = "SELL"
            sl = price * (1.0 + self.sl_pct)
            tp = price * (1.0 - self.tp_pct)

        strength = min(abs(reaction) / 0.01, 1.0)  # 1% reaction = max strength

        return Signal(
            symbol=sym,
            side=side,
            strategy_name=self.name,
            stop_loss=sl,
            take_profit=tp,
            strength=strength,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_eod(self, timestamp) -> None:
        """Reset daily state."""
        self._traded_today = False
        self._pre_announcement_price = None
        self._is_ecb_day = False
        self._last_checked_date = None

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self._symbol,
            "min_reaction_pct": self.min_reaction_pct,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "close_eod": self.close_eod,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "min_reaction_pct": [0.002, 0.003, 0.005],
            "sl_pct": [0.01, 0.015, 0.02],
            "tp_pct": [0.02, 0.03, 0.04],
        }
