"""EU equity market calendar — Euronext hours."""

from __future__ import annotations

from datetime import date, time
from typing import Set, Tuple

import pandas as pd

from core.backtester_v2.calendars.base import CalendarFactory, MarketCalendar

# Major Euronext holidays for 2025-2026
EU_HOLIDAYS: Set[date] = {
    # 2025
    date(2025, 1, 1),    # New Year's Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 4, 21),   # Easter Monday
    date(2025, 5, 1),    # May Day
    date(2025, 12, 25),  # Christmas Day
    date(2025, 12, 26),  # Boxing Day / St Stephen's
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 6),    # Easter Monday
    date(2026, 5, 1),    # May Day
    date(2026, 12, 25),  # Christmas Day
    date(2026, 12, 26),  # Boxing Day / St Stephen's
}

_CET = "Europe/Paris"
_MARKET_OPEN = time(9, 0)
_MARKET_CLOSE = time(17, 30)


class EUMarketCalendar(MarketCalendar):
    """Euronext calendar: 9:00-17:30 CET."""

    def is_open(self, timestamp: pd.Timestamp) -> bool:
        """Check if Euronext is open at *timestamp*.

        Args:
            timestamp: Timezone-aware timestamp.
        """
        ts = timestamp.tz_convert(_CET) if timestamp.tzinfo else timestamp.tz_localize(_CET)
        d = ts.date()

        # Weekends
        if ts.weekday() >= 5:
            return False
        # Holidays
        if d in EU_HOLIDAYS:
            return False

        t = ts.time()
        return _MARKET_OPEN <= t < _MARKET_CLOSE

    def get_session_times(self, dt: date) -> Tuple[time, time]:
        """Return (open, close) for a given date.

        Args:
            dt: Calendar date.

        Returns:
            (09:00, 17:30) CET.
        """
        return (_MARKET_OPEN, _MARKET_CLOSE)


CalendarFactory.register("eu_equity", EUMarketCalendar)
