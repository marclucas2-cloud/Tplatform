"""Tests for market calendars — US, EU, FX, futures, crypto."""

import pandas as pd

from core.backtester_v2.calendars.base import CalendarFactory
from core.backtester_v2.calendars.crypto_calendar import CryptoCalendar
from core.backtester_v2.calendars.eu_calendar import EUMarketCalendar
from core.backtester_v2.calendars.futures_calendar import FuturesCalendar
from core.backtester_v2.calendars.fx_calendar import FXCalendar
from core.backtester_v2.calendars.us_calendar import USMarketCalendar

# ---------------------------------------------------------------------------
# US Calendar
# ---------------------------------------------------------------------------

class TestUSCalendar:
    cal = USMarketCalendar()

    def test_us_weekday_open(self):
        """Tuesday 11:00 ET should be open."""
        ts = pd.Timestamp("2025-06-17 11:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_us_market_open_boundary(self):
        """Exactly 9:30 ET should be open."""
        ts = pd.Timestamp("2025-06-17 09:30", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_us_before_open(self):
        """9:29 ET should be closed."""
        ts = pd.Timestamp("2025-06-17 09:29", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_us_close_boundary(self):
        """Exactly 16:00 ET should be closed (half-open interval)."""
        ts = pd.Timestamp("2025-06-17 16:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_us_weekend_closed(self):
        """Saturday should be closed."""
        ts = pd.Timestamp("2025-06-14 12:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_us_holiday_closed(self):
        """Independence Day 2025 (Jul 4) should be closed."""
        ts = pd.Timestamp("2025-07-04 11:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_us_early_close(self):
        """Day before July 4 2025 (Jul 3): open at 12:00, closed at 13:30."""
        ts_open = pd.Timestamp("2025-07-03 12:00", tz="US/Eastern")
        ts_closed = pd.Timestamp("2025-07-03 13:30", tz="US/Eastern")
        assert self.cal.is_open(ts_open) is True
        assert self.cal.is_open(ts_closed) is False

    def test_us_session_times_normal(self):
        from datetime import date, time
        open_t, close_t = self.cal.get_session_times(date(2025, 6, 17))
        assert open_t == time(9, 30)
        assert close_t == time(16, 0)

    def test_us_session_times_early(self):
        from datetime import date, time
        open_t, close_t = self.cal.get_session_times(date(2025, 7, 3))
        assert close_t == time(13, 0)

    def test_us_utc_conversion(self):
        """Timestamp in UTC should be correctly converted to ET."""
        # 15:00 UTC = 11:00 ET (summer, EDT = UTC-4)
        ts = pd.Timestamp("2025-06-17 15:00", tz="UTC")
        assert self.cal.is_open(ts) is True


# ---------------------------------------------------------------------------
# EU Calendar
# ---------------------------------------------------------------------------

class TestEUCalendar:
    cal = EUMarketCalendar()

    def test_eu_open(self):
        """Wednesday 14:00 CET should be open."""
        ts = pd.Timestamp("2025-06-18 14:00", tz="Europe/Paris")
        assert self.cal.is_open(ts) is True

    def test_eu_before_open(self):
        ts = pd.Timestamp("2025-06-18 08:59", tz="Europe/Paris")
        assert self.cal.is_open(ts) is False

    def test_eu_closed_after_hours(self):
        ts = pd.Timestamp("2025-06-18 17:30", tz="Europe/Paris")
        assert self.cal.is_open(ts) is False

    def test_eu_holiday_closed(self):
        """Christmas 2025 should be closed."""
        ts = pd.Timestamp("2025-12-25 12:00", tz="Europe/Paris")
        assert self.cal.is_open(ts) is False

    def test_eu_weekend_closed(self):
        ts = pd.Timestamp("2025-06-15 12:00", tz="Europe/Paris")  # Sunday
        assert self.cal.is_open(ts) is False


# ---------------------------------------------------------------------------
# FX Calendar
# ---------------------------------------------------------------------------

class TestFXCalendar:
    cal = FXCalendar()

    def test_fx_weekday_open(self):
        """Tuesday 03:00 ET (middle of night) should be open."""
        ts = pd.Timestamp("2025-06-17 03:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_fx_weekend_closed_saturday(self):
        ts = pd.Timestamp("2025-06-14 12:00", tz="US/Eastern")  # Saturday
        assert self.cal.is_open(ts) is False

    def test_fx_sunday_before_open(self):
        """Sunday 16:00 ET should be closed (opens at 17:00)."""
        ts = pd.Timestamp("2025-06-15 16:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_fx_sunday_after_open(self):
        """Sunday 17:30 ET should be open."""
        ts = pd.Timestamp("2025-06-15 17:30", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_fx_friday_before_close(self):
        ts = pd.Timestamp("2025-06-13 16:59", tz="US/Eastern")  # Friday
        assert self.cal.is_open(ts) is True

    def test_fx_friday_after_close(self):
        ts = pd.Timestamp("2025-06-13 17:00", tz="US/Eastern")  # Friday
        assert self.cal.is_open(ts) is False


# ---------------------------------------------------------------------------
# Futures Calendar
# ---------------------------------------------------------------------------

class TestFuturesCalendar:
    cal = FuturesCalendar()

    def test_futures_session_open(self):
        """Tuesday 10:00 ET should be open."""
        ts = pd.Timestamp("2025-06-17 10:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_futures_daily_halt(self):
        """Wednesday 17:30 ET should be in daily halt."""
        ts = pd.Timestamp("2025-06-18 17:30", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_futures_halt_boundary_start(self):
        """17:00 ET is the start of halt (closed)."""
        ts = pd.Timestamp("2025-06-17 17:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_futures_halt_boundary_end(self):
        """18:00 ET is end of halt (open again)."""
        ts = pd.Timestamp("2025-06-17 18:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_futures_sunday_open(self):
        """Sunday 18:30 ET should be open."""
        ts = pd.Timestamp("2025-06-15 18:30", tz="US/Eastern")
        assert self.cal.is_open(ts) is True

    def test_futures_saturday_closed(self):
        ts = pd.Timestamp("2025-06-14 12:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False

    def test_futures_friday_close(self):
        """Friday 17:00 ET should be closed."""
        ts = pd.Timestamp("2025-06-13 17:00", tz="US/Eastern")
        assert self.cal.is_open(ts) is False


# ---------------------------------------------------------------------------
# Crypto Calendar
# ---------------------------------------------------------------------------

class TestCryptoCalendar:
    def test_crypto_always_open(self):
        """Saturday 03:00 should be open (24/7)."""
        cal = CryptoCalendar(maintenance=False)
        ts = pd.Timestamp("2025-06-14 03:00", tz="UTC")
        assert cal.is_open(ts) is True

    def test_crypto_maintenance_closed(self):
        """Tuesday 06:15 UTC in maintenance window."""
        cal = CryptoCalendar(maintenance=True)
        ts = pd.Timestamp("2025-06-17 06:15", tz="UTC")
        assert cal.is_open(ts) is False

    def test_crypto_maintenance_before(self):
        """Tuesday 05:59 UTC should be open."""
        cal = CryptoCalendar(maintenance=True)
        ts = pd.Timestamp("2025-06-17 05:59", tz="UTC")
        assert cal.is_open(ts) is True

    def test_crypto_maintenance_after(self):
        """Tuesday 06:30 UTC should be open (end of window)."""
        cal = CryptoCalendar(maintenance=True)
        ts = pd.Timestamp("2025-06-17 06:30", tz="UTC")
        assert cal.is_open(ts) is True

    def test_crypto_no_maintenance(self):
        """With maintenance=False, Tuesday 06:15 UTC is open."""
        cal = CryptoCalendar(maintenance=False)
        ts = pd.Timestamp("2025-06-17 06:15", tz="UTC")
        assert cal.is_open(ts) is True


# ---------------------------------------------------------------------------
# CalendarFactory
# ---------------------------------------------------------------------------

class TestCalendarFactory:
    def test_factory_creates_registered(self):
        """Factory should create calendars for registered asset classes."""
        # Ensure registrations from imports

        cals = CalendarFactory.create(["equity", "fx", "crypto"])
        assert "equity" in cals
        assert "fx" in cals
        assert "crypto" in cals
        assert isinstance(cals["equity"], USMarketCalendar)

    def test_factory_ignores_unknown(self):
        """Unknown asset classes are silently skipped."""
        cals = CalendarFactory.create(["unknown_asset"])
        assert len(cals) == 0
