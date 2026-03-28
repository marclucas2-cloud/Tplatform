"""Crypto market calendar — 24/7 with optional maintenance window."""

from __future__ import annotations

from datetime import date, time
from typing import Tuple

import pandas as pd

from core.backtester_v2.calendars.base import CalendarFactory, MarketCalendar

_UTC = "UTC"
# Optional maintenance window: Tuesday 06:00-06:30 UTC (common on exchanges)
_MAINT_DAY = 1  # Tuesday (weekday index)
_MAINT_START = time(6, 0)
_MAINT_END = time(6, 30)


class CryptoCalendar(MarketCalendar):
    """Crypto calendar: 24/7 with optional maintenance window.

    By default, models a Tuesday 06:00-06:30 UTC maintenance window
    (common on Binance and others). Set *maintenance=False* to disable.
    """

    def __init__(self, maintenance: bool = True) -> None:
        self.maintenance = maintenance

    def is_open(self, timestamp: pd.Timestamp) -> bool:
        """Check if crypto markets are open at *timestamp*.

        Args:
            timestamp: Timezone-aware timestamp.
        """
        if not self.maintenance:
            return True

        ts = timestamp.tz_convert(_UTC) if timestamp.tzinfo else timestamp.tz_localize(_UTC)

        if ts.weekday() == _MAINT_DAY:
            t = ts.time()
            if _MAINT_START <= t < _MAINT_END:
                return False
        return True

    def get_session_times(self, dt: date) -> Tuple[time, time]:
        """Return nominal session times (24h).

        Args:
            dt: Calendar date.

        Returns:
            (00:00, 23:59) representing continuous 24/7 trading.
        """
        return (time(0, 0), time(23, 59))


CalendarFactory.register("crypto", CryptoCalendar)
