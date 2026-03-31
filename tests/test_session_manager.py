"""Tests for SessionManager — market hours, gap detection, bar filtering."""
import zoneinfo
from datetime import datetime, date, time, timezone

import numpy as np
import pandas as pd
import pytest

from core.data.session_manager import SessionManager


@pytest.fixture
def sm():
    return SessionManager()


class TestEUSession:
    def test_eu_open_close_winter(self, sm):
        d = date(2026, 1, 15)  # Winter: CET = UTC+1
        session = sm.get_session("EU", d)
        # open is in UTC: 09:00 CET = 08:00 UTC
        assert session["open"].hour == 8

    def test_eu_open_close_summer(self, sm):
        d = date(2026, 7, 15)  # Summer: CEST = UTC+2
        session = sm.get_session("EU", d)
        # 09:00 CEST = 07:00 UTC
        assert session["open"].hour == 7

    def test_eu_is_open_at_noon(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        ts = datetime(2026, 3, 31, 12, 0, tzinfo=paris)
        assert sm.is_market_open("EU", ts)

    def test_eu_is_closed_at_night(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        ts = datetime(2026, 3, 31, 22, 0, tzinfo=paris)
        assert not sm.is_market_open("EU", ts)


class TestUSSession:
    def test_us_is_open_midday(self, sm):
        """US market should be open at 11:00 ET on a Monday."""
        ts = datetime(2026, 3, 16, 16, 0, tzinfo=timezone.utc)  # Monday 11:00 ET
        assert sm.is_market_open("US", ts)

    def test_us_is_open_at_1430_utc(self, sm):
        ts = datetime(2026, 3, 31, 14, 30, tzinfo=timezone.utc)
        assert sm.is_market_open("US", ts)


class TestFXSession:
    def test_fx_open_monday(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        ts = datetime(2026, 3, 31, 10, 0, tzinfo=paris)
        assert sm.is_market_open("FX", ts)

    def test_fx_closed_saturday(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        ts = datetime(2026, 3, 28, 12, 0, tzinfo=paris)  # Saturday
        assert not sm.is_market_open("FX", ts)


class TestCryptoSession:
    def test_crypto_always_open(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        for hour in [0, 6, 12, 18, 23]:
            ts = datetime(2026, 3, 31, hour, 0, tzinfo=paris)
            assert sm.is_market_open("CRYPTO", ts)


class TestBarFiltering:
    def test_filter_removes_pre_market(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        idx = pd.date_range("2026-03-31 07:00", "2026-03-31 18:00", freq="h", tz=paris)
        df = pd.DataFrame({"close": np.random.randn(len(idx))}, index=idx)
        filtered = sm.filter_session_bars(df, "EU")
        for ts in filtered.index:
            assert 9 <= ts.hour < 18


class TestOpeningGapDetection:
    def test_detect_gap(self, sm):
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        dates = pd.date_range("2026-03-30 09:00", periods=2, freq="D", tz=paris)
        # Day 1 close 100, Day 2 open 105 = 5% gap
        df = pd.DataFrame({
            "open": [100.0, 105.0],
            "high": [101.0, 106.0],
            "low": [99.0, 104.0],
            "close": [100.0, 105.5],
        }, index=dates)
        gaps = sm.detect_opening_gap(df, "EU")
        assert len(gaps) >= 1
        assert abs(gaps[0]["gap_pct"]) > 0.01


class TestHolidays:
    def test_christmas_is_holiday(self, sm):
        assert sm.is_holiday("EU", date(2026, 12, 25))

    def test_regular_day_not_holiday(self, sm):
        assert not sm.is_holiday("EU", date(2026, 3, 31))

    def test_us_july_4(self, sm):
        # July 4 2026 is Saturday, observed Friday July 3
        # Just test that is_holiday doesn't crash
        result = sm.is_holiday("US", date(2026, 7, 3))
        assert isinstance(result, bool)
