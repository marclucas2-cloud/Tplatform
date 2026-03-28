"""FX market calendar — 24/5, Sunday 17:00 to Friday 17:00 ET."""

from __future__ import annotations

from datetime import date, time
from typing import Tuple

import pandas as pd

from core.backtester_v2.calendars.base import CalendarFactory, MarketCalendar

_ET = "US/Eastern"
_SUNDAY_OPEN = time(17, 0)
_FRIDAY_CLOSE = time(17, 0)


class FXCalendar(MarketCalendar):
    """FX calendar: 24h Sunday 17:00 ET through Friday 17:00 ET."""

    def is_open(self, timestamp: pd.Timestamp) -> bool:
        """Check if FX market is open at *timestamp*.

        Closed Saturday all day and Sunday before 17:00 ET.
        Closed Friday after 17:00 ET.

        Args:
            timestamp: Timezone-aware timestamp.
        """
        ts = timestamp.tz_convert(_ET) if timestamp.tzinfo else timestamp.tz_localize(_ET)
        wd = ts.weekday()  # Mon=0 .. Sun=6
        t = ts.time()

        # Saturday: always closed
        if wd == 5:
            return False
        # Sunday: open only from 17:00 ET
        if wd == 6:
            return t >= _SUNDAY_OPEN
        # Friday: close at 17:00 ET
        if wd == 4:
            return t < _FRIDAY_CLOSE
        # Mon-Thu: 24h open
        return True

    def get_session_times(self, dt: date) -> Tuple[time, time]:
        """Return nominal session times.

        Args:
            dt: Calendar date (not heavily used for 24h markets).

        Returns:
            (00:00, 23:59) representing continuous trading.
        """
        return (time(0, 0), time(23, 59))


CalendarFactory.register("fx", FXCalendar)
