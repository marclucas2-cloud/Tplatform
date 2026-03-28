"""US equity market calendar — NYSE/NASDAQ hours."""

from __future__ import annotations

from datetime import date, time
from typing import Set, Tuple

import pandas as pd

from core.backtester_v2.calendars.base import CalendarFactory, MarketCalendar

# Major US market holidays for 2025-2026
US_HOLIDAYS_2025_2026: Set[date] = {
    # 2025
    date(2025, 1, 1),    # New Year's Day
    date(2025, 1, 20),   # MLK Day
    date(2025, 2, 17),   # Presidents' Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7, 4),    # Independence Day
    date(2025, 9, 1),    # Labor Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}

# Early close at 13:00 ET (day before Independence Day, after Thanksgiving, Christmas Eve)
US_EARLY_CLOSE: Set[date] = {
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 7, 2),
    date(2026, 11, 27),
    date(2026, 12, 24),
}

_ET = "US/Eastern"
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)


class USMarketCalendar(MarketCalendar):
    """NYSE/NASDAQ calendar: 9:30-16:00 ET, early close 13:00 ET."""

    def is_open(self, timestamp: pd.Timestamp) -> bool:
        """Check if US equity markets are open at *timestamp*.

        Args:
            timestamp: Timezone-aware timestamp.
        """
        ts = timestamp.tz_convert(_ET) if timestamp.tzinfo else timestamp.tz_localize(_ET)
        d = ts.date()

        # Weekends
        if ts.weekday() >= 5:
            return False
        # Holidays
        if d in US_HOLIDAYS_2025_2026:
            return False

        t = ts.time()
        close = _EARLY_CLOSE if d in US_EARLY_CLOSE else _MARKET_CLOSE
        return _MARKET_OPEN <= t < close

    def get_session_times(self, dt: date) -> Tuple[time, time]:
        """Return (open, close) for a given date.

        Args:
            dt: Calendar date.

        Returns:
            (9:30, 16:00) or (9:30, 13:00) on early-close days.
        """
        close = _EARLY_CLOSE if dt in US_EARLY_CLOSE else _MARKET_CLOSE
        return (_MARKET_OPEN, close)


CalendarFactory.register("equity", USMarketCalendar)
CalendarFactory.register("us_equity", USMarketCalendar)
