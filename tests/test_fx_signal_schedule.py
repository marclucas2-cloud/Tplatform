"""Tests for FX signal scheduling — peak vs off-peak frequency."""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestFXSignalSchedule:
    """Test peak/off-peak signal evaluation."""

    def _make_adapter(self):
        """Create FXLiveAdapter with mocked dependencies."""
        from core.fx_live_adapter import FXLiveAdapter
        adapter = FXLiveAdapter.__new__(FXLiveAdapter)
        adapter._signal_schedule = {
            "EUR_USD": {
                "peak_hours_start": "07:00",
                "peak_hours_end": "17:00",
                "peak_frequency_minutes": 60,
                "off_peak_frequency_minutes": 240,
            },
            "AUD_JPY": {
                "peak_hours_start": "00:00",
                "peak_hours_end": "08:00",
                "peak_frequency_minutes": 60,
                "off_peak_frequency_minutes": 240,
            },
        }
        return adapter

    def test_first_eval_always_true(self):
        adapter = self._make_adapter()
        now = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        assert adapter.should_evaluate_signal("EUR.USD", now) is True

    def test_peak_hours_60min_interval(self):
        adapter = self._make_adapter()
        t1 = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        adapter.should_evaluate_signal("EUR.USD", t1)

        t2 = t1 + timedelta(minutes=30)
        assert adapter.should_evaluate_signal("EUR.USD", t2) is False

        t3 = t1 + timedelta(minutes=61)
        assert adapter.should_evaluate_signal("EUR.USD", t3) is True

    def test_off_peak_240min_interval(self):
        adapter = self._make_adapter()
        t1 = datetime(2026, 3, 27, 20, 0, tzinfo=timezone.utc)  # 20:00 = off-peak
        adapter.should_evaluate_signal("EUR.USD", t1)

        t2 = t1 + timedelta(minutes=120)
        assert adapter.should_evaluate_signal("EUR.USD", t2) is False

        t3 = t1 + timedelta(minutes=241)
        assert adapter.should_evaluate_signal("EUR.USD", t3) is True

    def test_unknown_pair_always_evaluate(self):
        adapter = self._make_adapter()
        now = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        assert adapter.should_evaluate_signal("NZD.USD", now) is True

    def test_audjpy_asian_session_peak(self):
        adapter = self._make_adapter()
        t1 = datetime(2026, 3, 27, 3, 0, tzinfo=timezone.utc)  # 03:00 = Asia peak
        adapter.should_evaluate_signal("AUD.JPY", t1)

        t2 = t1 + timedelta(minutes=61)
        assert adapter.should_evaluate_signal("AUD.JPY", t2) is True

    def test_parse_time_to_minutes(self):
        from core.fx_live_adapter import FXLiveAdapter
        assert FXLiveAdapter._parse_time_to_minutes("07:00") == 420
        assert FXLiveAdapter._parse_time_to_minutes("17:30") == 1050
        assert FXLiveAdapter._parse_time_to_minutes("00:00") == 0
