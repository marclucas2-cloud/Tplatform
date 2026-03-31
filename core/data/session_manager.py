"""
Session-aware market hours manager.

Handles EU opening gaps, market session boundaries, and data filtering.
Designed for EU indices (DAX, CAC, ESTX50, FTSE) and multi-market trading.

Markets supported:
  EU     : 09:00-17:30 CET (Euronext / EUREX)
  UK     : 08:00-16:30 GMT (LSE / ICE Europe)
  US     : 09:30-16:00 ET  (NYSE / NASDAQ)
  FX     : Sun 17:00 - Fri 17:00 ET (continuous)
  CRYPTO : 24/7

Key features:
  - DST-aware via zoneinfo (CET/CEST, GMT/BST, EST/EDT)
  - Opening gap detection (> 1% between sessions)
  - Bar filtering to remove pre/post-market noise
  - Holiday calendar for EU and US markets
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

# -- Timezones --
_UTC = timezone.utc
_PARIS = ZoneInfo("Europe/Paris")
_LONDON = ZoneInfo("Europe/London")
_NEW_YORK = ZoneInfo("America/New_York")

# -- Market session definitions (local times) --
_MARKET_SESSIONS = {
    "EU": {
        "tz": _PARIS,
        "tz_name": "Europe/Paris",
        "open": time(9, 0),
        "close": time(17, 30),
        "pre_open_minutes": 30,
        "post_close_minutes": 30,
        "trading_days": {0, 1, 2, 3, 4},  # Mon-Fri
    },
    "UK": {
        "tz": _LONDON,
        "tz_name": "Europe/London",
        "open": time(8, 0),
        "close": time(16, 30),
        "pre_open_minutes": 30,
        "post_close_minutes": 30,
        "trading_days": {0, 1, 2, 3, 4},
    },
    "US": {
        "tz": _NEW_YORK,
        "tz_name": "America/New_York",
        "open": time(9, 30),
        "close": time(16, 0),
        "pre_open_minutes": 60,
        "post_close_minutes": 60,
        "trading_days": {0, 1, 2, 3, 4},
    },
    "FX": {
        "tz": _NEW_YORK,
        "tz_name": "America/New_York",
        # Continuous: Sun 17:00 ET to Fri 17:00 ET
        "open": time(17, 0),   # Sunday open
        "close": time(17, 0),  # Friday close
        "pre_open_minutes": 0,
        "post_close_minutes": 0,
        "trading_days": None,  # special handling
    },
    "CRYPTO": {
        "tz": ZoneInfo("UTC"),
        "tz_name": "UTC",
        "open": time(0, 0),
        "close": time(23, 59, 59),
        "pre_open_minutes": 0,
        "post_close_minutes": 0,
        "trading_days": {0, 1, 2, 3, 4, 5, 6},  # 7 days
    },
}

# -- EU holidays (Euronext / EUREX) --
EU_HOLIDAYS = {
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
    # 2027
    date(2027, 1, 1),    # New Year's Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 3, 29),   # Easter Monday
    date(2027, 5, 1),    # May Day
    date(2027, 12, 25),  # Christmas Day (Saturday -> no extra closure)
    date(2027, 12, 26),  # Boxing Day (Sunday -> observed Monday 27th)
}

# -- UK holidays (LSE / ICE Europe) --
UK_HOLIDAYS = {
    # 2025
    date(2025, 1, 1),    # New Year's Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 4, 21),   # Easter Monday
    date(2025, 5, 5),    # Early May Bank Holiday
    date(2025, 5, 26),   # Spring Bank Holiday
    date(2025, 8, 25),   # Summer Bank Holiday
    date(2025, 12, 25),  # Christmas Day
    date(2025, 12, 26),  # Boxing Day
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 6),    # Easter Monday
    date(2026, 5, 4),    # Early May Bank Holiday
    date(2026, 5, 25),   # Spring Bank Holiday
    date(2026, 8, 31),   # Summer Bank Holiday
    date(2026, 12, 25),  # Christmas Day
    date(2026, 12, 26),  # Boxing Day (Saturday -> observed Monday 28th)
    date(2026, 12, 28),  # Boxing Day observed
}

# -- US holidays (NYSE/NASDAQ) --
US_HOLIDAYS = {
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
    # 2027
    date(2027, 1, 1),    # New Year's Day
    date(2027, 1, 18),   # MLK Day
    date(2027, 2, 15),   # Presidents' Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 6, 18),   # Juneteenth (observed, actual is Saturday 19th)
    date(2027, 7, 5),    # Independence Day (observed, actual is Sunday 4th)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas (observed, actual is Saturday 25th)
}

# Holiday sets keyed by market
_HOLIDAY_CALENDARS = {
    "EU": EU_HOLIDAYS,
    "UK": UK_HOLIDAYS,
    "US": US_HOLIDAYS,
}


class SessionManager:
    """Session-aware market hours manager.

    Provides session boundaries, bar filtering, opening gap detection,
    and holiday awareness for multi-market trading.
    """

    def __init__(self) -> None:
        self._sessions = dict(_MARKET_SESSIONS)

    # ------------------------------------------------------------------
    # Session boundaries
    # ------------------------------------------------------------------

    def get_session(self, market: str, dt: date) -> Optional[dict]:
        """Return session boundaries for a market on a given date.

        Args:
            market: "EU", "UK", "US", "FX", or "CRYPTO"
            dt: Calendar date.

        Returns:
            {
                "open": datetime (aware, UTC),
                "close": datetime (aware, UTC),
                "pre_open": datetime (aware, UTC),
                "post_close": datetime (aware, UTC),
                "tz": str (canonical timezone name),
            }
            Returns None if the market is closed that day (weekend/holiday).
        """
        market = market.upper()
        if market not in self._sessions:
            raise ValueError(f"Unknown market: {market}")

        mdef = self._sessions[market]

        # FX: special continuous session handling
        if market == "FX":
            return self._get_fx_session(dt)

        # CRYPTO: always open
        if market == "CRYPTO":
            open_utc = datetime(dt.year, dt.month, dt.day, 0, 0, tzinfo=_UTC)
            close_utc = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=_UTC)
            return {
                "open": open_utc,
                "close": close_utc,
                "pre_open": open_utc,
                "post_close": close_utc,
                "tz": "UTC",
            }

        # Standard markets (EU, UK, US): check weekday and holidays
        if dt.weekday() not in mdef["trading_days"]:
            return None

        if self.is_holiday(market, dt):
            return None

        local_tz = mdef["tz"]
        open_local = datetime.combine(dt, mdef["open"], tzinfo=local_tz)
        close_local = datetime.combine(dt, mdef["close"], tzinfo=local_tz)

        pre_open_local = open_local - timedelta(minutes=mdef["pre_open_minutes"])
        post_close_local = close_local + timedelta(minutes=mdef["post_close_minutes"])

        return {
            "open": open_local.astimezone(_UTC),
            "close": close_local.astimezone(_UTC),
            "pre_open": pre_open_local.astimezone(_UTC),
            "post_close": post_close_local.astimezone(_UTC),
            "tz": mdef["tz_name"],
        }

    def _get_fx_session(self, dt: date) -> Optional[dict]:
        """Return FX session for a given date.

        FX is continuous from Sunday 17:00 ET to Friday 17:00 ET.
        For a weekday, the "session" is from 17:00 ET previous day
        to 17:00 ET current day. Saturday is closed.
        """
        wd = dt.weekday()

        # Saturday: closed
        if wd == 5:
            return None

        tz = _NEW_YORK

        if wd == 6:
            # Sunday: opens at 17:00 ET, session runs until Monday 17:00 ET
            open_local = datetime.combine(dt, time(17, 0), tzinfo=tz)
            close_local = datetime.combine(
                dt + timedelta(days=1), time(17, 0), tzinfo=tz
            )
        else:
            # Monday-Friday: session from previous day 17:00 to current day 17:00
            open_local = datetime.combine(
                dt - timedelta(days=1), time(17, 0), tzinfo=tz
            )
            close_local = datetime.combine(dt, time(17, 0), tzinfo=tz)

        open_utc = open_local.astimezone(_UTC)
        close_utc = close_local.astimezone(_UTC)

        return {
            "open": open_utc,
            "close": close_utc,
            "pre_open": open_utc,
            "post_close": close_utc,
            "tz": "America/New_York",
        }

    # ------------------------------------------------------------------
    # Market open check
    # ------------------------------------------------------------------

    def is_market_open(self, market: str, timestamp: datetime) -> bool:
        """Check if a market is open at a given timestamp.

        Args:
            market: "EU", "UK", "US", "FX", or "CRYPTO"
            timestamp: Timezone-aware datetime.

        Returns:
            True if the market is open at that exact moment.
        """
        market = market.upper()
        if market not in self._sessions:
            raise ValueError(f"Unknown market: {market}")

        # Ensure timezone-aware
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=_UTC)

        # CRYPTO: always open
        if market == "CRYPTO":
            return True

        # FX: continuous session check
        if market == "FX":
            return self._is_fx_open(timestamp)

        # Standard markets
        mdef = self._sessions[market]
        local_tz = mdef["tz"]
        ts_local = timestamp.astimezone(local_tz)
        d = ts_local.date()

        # Weekend
        if d.weekday() not in mdef["trading_days"]:
            return False

        # Holiday
        if self.is_holiday(market, d):
            return False

        # Check time within session
        t = ts_local.time()
        return mdef["open"] <= t < mdef["close"]

    def _is_fx_open(self, timestamp: datetime) -> bool:
        """Check if FX is open at the given timestamp.

        FX is closed Saturday all day and Sunday before 17:00 ET.
        Closed Friday after 17:00 ET.
        """
        ts_et = timestamp.astimezone(_NEW_YORK)
        wd = ts_et.weekday()
        t = ts_et.time()

        # Saturday: always closed
        if wd == 5:
            return False
        # Sunday: open only from 17:00 ET
        if wd == 6:
            return t >= time(17, 0)
        # Friday: close at 17:00 ET
        if wd == 4:
            return t < time(17, 0)
        # Mon-Thu: 24h open
        return True

    # ------------------------------------------------------------------
    # Bar filtering
    # ------------------------------------------------------------------

    def filter_session_bars(
        self, df: pd.DataFrame, market: str
    ) -> pd.DataFrame:
        """Filter bars to only include those within market hours.

        Removes pre-market and after-hours bars. The DataFrame must have
        a timezone-aware DatetimeIndex.

        Args:
            df: DataFrame with DatetimeIndex (timezone-aware or naive UTC).
            market: "EU", "UK", "US", "FX", or "CRYPTO"

        Returns:
            Filtered DataFrame containing only in-session bars.
        """
        market = market.upper()
        if market not in self._sessions:
            raise ValueError(f"Unknown market: {market}")

        if df.empty:
            return df.copy()

        # Ensure timezone-aware index
        idx = df.index
        if not isinstance(idx, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex")

        if idx.tz is None:
            idx = idx.tz_localize("UTC")
            df = df.copy()
            df.index = idx

        # CRYPTO: return everything
        if market == "CRYPTO":
            return df.copy()

        # FX: filter weekends
        if market == "FX":
            return self._filter_fx_bars(df)

        # Standard markets: filter by session hours
        mdef = self._sessions[market]
        local_tz = mdef["tz"]

        # Convert index to local timezone for comparison
        local_idx = idx.tz_convert(local_tz)
        local_times = local_idx.time
        local_dates = local_idx.date

        mask = pd.Series(False, index=df.index)

        for i in range(len(df)):
            d = local_dates[i]
            t = local_times[i]

            # Weekday check
            if d.weekday() not in mdef["trading_days"]:
                continue

            # Holiday check
            if self.is_holiday(market, d):
                continue

            # Time within session
            if mdef["open"] <= t < mdef["close"]:
                mask.iloc[i] = True

        return df[mask.values].copy()

    def _filter_fx_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter FX bars: remove Saturday and Sunday before 17:00 ET,
        Friday after 17:00 ET."""
        idx_et = df.index.tz_convert(_NEW_YORK)
        weekdays = idx_et.weekday
        times = idx_et.time

        mask = pd.Series(True, index=df.index)
        for i in range(len(df)):
            wd = weekdays[i]
            t = times[i]
            if wd == 5:
                # Saturday: always closed
                mask.iloc[i] = False
            elif wd == 6 and t < time(17, 0):
                # Sunday before 17:00 ET: closed
                mask.iloc[i] = False
            elif wd == 4 and t >= time(17, 0):
                # Friday after 17:00 ET: closed
                mask.iloc[i] = False

        return df[mask.values].copy()

    # ------------------------------------------------------------------
    # Opening gap detection
    # ------------------------------------------------------------------

    def detect_opening_gap(
        self,
        df: pd.DataFrame,
        market: str,
        threshold_pct: float = 1.0,
    ) -> list[dict]:
        """Detect opening gaps between previous session close and current session open.

        A gap is identified when the first bar of a session opens more than
        threshold_pct away from the last bar's close of the previous session.

        Args:
            df: DataFrame with OHLCV data and DatetimeIndex.
            market: "EU", "UK", "US", "FX", or "CRYPTO"
            threshold_pct: Minimum gap size in percent (default 1.0%).

        Returns:
            List of dicts: [{date, prev_close, open, gap_pct}]
        """
        market = market.upper()
        if market in ("FX", "CRYPTO"):
            # Continuous markets have no daily open gaps in the traditional sense
            return []

        if df.empty or "close" not in df.columns or "open" not in df.columns:
            return []

        # Filter to session bars first
        session_df = self.filter_session_bars(df, market)
        if session_df.empty:
            return []

        # Ensure timezone-aware
        idx = session_df.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
            session_df = session_df.copy()
            session_df.index = idx

        mdef = self._sessions[market]
        local_tz = mdef["tz"]
        local_idx = idx.tz_convert(local_tz)

        # Group bars by trading date
        trading_dates = pd.Series(
            [d.date() for d in local_idx], index=session_df.index
        )
        unique_dates = sorted(trading_dates.unique())

        gaps = []
        for i in range(1, len(unique_dates)):
            prev_date = unique_dates[i - 1]
            curr_date = unique_dates[i]

            prev_bars = session_df[trading_dates == prev_date]
            curr_bars = session_df[trading_dates == curr_date]

            if prev_bars.empty or curr_bars.empty:
                continue

            prev_close = prev_bars["close"].iloc[-1]
            curr_open = curr_bars["open"].iloc[0]

            if prev_close == 0:
                continue

            gap_pct = ((curr_open - prev_close) / prev_close) * 100.0

            if abs(gap_pct) >= threshold_pct:
                gaps.append({
                    "date": curr_date,
                    "prev_close": round(prev_close, 6),
                    "open": round(curr_open, 6),
                    "gap_pct": round(gap_pct, 4),
                })

        return gaps

    # ------------------------------------------------------------------
    # First bar of session
    # ------------------------------------------------------------------

    def get_first_bar_of_session(
        self,
        df: pd.DataFrame,
        market: str,
        dt: date,
    ) -> Optional[pd.Series]:
        """Return the first valid bar of a market session on a given date.

        Filters to in-session bars only, then returns the first bar of
        the requested date. Returns None if no session exists or no bars found.

        Args:
            df: DataFrame with DatetimeIndex and OHLCV columns.
            market: "EU", "UK", "US", "FX", or "CRYPTO"
            dt: Calendar date.

        Returns:
            pd.Series (the row) or None.
        """
        market = market.upper()
        session = self.get_session(market, dt)
        if session is None:
            return None

        if df.empty:
            return None

        # Ensure timezone-aware
        idx = df.index
        if not isinstance(idx, pd.DatetimeIndex):
            return None
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
            df = df.copy()
            df.index = idx

        # Filter bars within this session's open-close window
        session_open = session["open"]
        session_close = session["close"]

        mask = (df.index >= session_open) & (df.index < session_close)
        session_bars = df[mask]

        if session_bars.empty:
            return None

        return session_bars.iloc[0]

    # ------------------------------------------------------------------
    # Holiday check
    # ------------------------------------------------------------------

    def is_holiday(self, market: str, dt: date) -> bool:
        """Check if a date is a market holiday.

        Args:
            market: "EU", "UK", "US", "FX", or "CRYPTO"
            dt: Calendar date.

        Returns:
            True if the market is closed for a holiday.
        """
        market = market.upper()

        # FX and CRYPTO have no holidays (FX closes on weekends only)
        if market in ("FX", "CRYPTO"):
            return False

        holidays = _HOLIDAY_CALENDARS.get(market, set())
        return dt in holidays

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def get_trading_dates(
        self, market: str, start: date, end: date
    ) -> list[date]:
        """Return all trading dates for a market within a range.

        Args:
            market: Market identifier.
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            Sorted list of trading dates.
        """
        market = market.upper()
        dates = []
        current = start
        while current <= end:
            session = self.get_session(market, current)
            if session is not None:
                dates.append(current)
            current += timedelta(days=1)
        return dates

    def get_next_session_open(
        self, market: str, after: datetime
    ) -> Optional[datetime]:
        """Find the next session open time after a given timestamp.

        Args:
            market: Market identifier.
            after: Timezone-aware datetime.

        Returns:
            Next session open as aware datetime (UTC), or None.
        """
        market = market.upper()
        if after.tzinfo is None:
            after = after.replace(tzinfo=_UTC)

        dt = after.astimezone(_UTC).date()

        # Search up to 10 days ahead (covers weekends and holidays)
        for i in range(10):
            check_date = dt + timedelta(days=i)
            session = self.get_session(market, check_date)
            if session is not None and session["open"] > after:
                return session["open"]

        return None
