"""Futures calendar — CME Globex hours for micro contracts."""

from __future__ import annotations

from datetime import date, time
from typing import Tuple

import pandas as pd

from core.backtester_v2.calendars.base import CalendarFactory, MarketCalendar

_ET = "US/Eastern"

# CME Globex: Sunday 18:00 ET to Friday 17:00 ET
# Daily maintenance halt: 17:00-18:00 ET (Mon-Thu)
_SUNDAY_OPEN = time(18, 0)
_FRIDAY_CLOSE = time(17, 0)
_HALT_START = time(17, 0)
_HALT_END = time(18, 0)


class FuturesCalendar(MarketCalendar):
    """CME Globex calendar for MES, MNQ, MCL.

    Session: Sunday 18:00 ET through Friday 17:00 ET.
    Daily halt: 17:00-18:00 ET Monday through Thursday.
    """

    def is_open(self, timestamp: pd.Timestamp) -> bool:
        """Check if CME Globex is open at *timestamp*.

        Args:
            timestamp: Timezone-aware timestamp.
        """
        ts = timestamp.tz_convert(_ET) if timestamp.tzinfo else timestamp.tz_localize(_ET)
        wd = ts.weekday()  # Mon=0 .. Sun=6
        t = ts.time()

        # Saturday: always closed
        if wd == 5:
            return False
        # Sunday: open from 18:00 ET
        if wd == 6:
            return t >= _SUNDAY_OPEN
        # Friday: close at 17:00 ET
        if wd == 4:
            return t < _FRIDAY_CLOSE
        # Mon-Thu: open except 17:00-18:00 halt
        if _HALT_START <= t < _HALT_END:
            return False
        return True

    def get_session_times(self, dt: date) -> Tuple[time, time]:
        """Return nominal Globex session times.

        Args:
            dt: Calendar date.

        Returns:
            (18:00, 17:00) representing the overnight session boundaries.
        """
        return (time(18, 0), time(17, 0))


CalendarFactory.register("futures", FuturesCalendar)
