"""Test _is_eu_market_closed guard sur paper_cycles EU.

Regression bug 2026-04-20: MIB/ESTX50 SPREAD loggait "yfinance returned
empty data" sur jours non-tradables. Fix: skip weekend + holidays EU.
"""
from __future__ import annotations

from datetime import date

from core.worker.cycles.paper_cycles import (
    _EU_HOLIDAYS_2026,
    _is_eu_market_closed,
)


class TestIsEuMarketClosed:

    def test_weekday_open(self):
        """2026-04-20 lundi - marche ouvert (pas ferie)."""
        assert _is_eu_market_closed(date(2026, 4, 20)) is False

    def test_saturday_closed(self):
        assert _is_eu_market_closed(date(2026, 4, 25)) is True

    def test_sunday_closed(self):
        assert _is_eu_market_closed(date(2026, 4, 26)) is True

    def test_easter_monday_closed(self):
        """Easter Monday 2026 = 6 avril, marche EU ferme."""
        assert _is_eu_market_closed(date(2026, 4, 6)) is True

    def test_good_friday_closed(self):
        """Good Friday 2026 = 3 avril."""
        assert _is_eu_market_closed(date(2026, 4, 3)) is True

    def test_labour_day_closed(self):
        """1er mai - Labour Day."""
        assert _is_eu_market_closed(date(2026, 5, 1)) is True

    def test_christmas_closed(self):
        assert _is_eu_market_closed(date(2026, 12, 25)) is True

    def test_new_year_closed(self):
        assert _is_eu_market_closed(date(2026, 1, 1)) is True

    def test_holidays_set_contains_easter_monday(self):
        assert date(2026, 4, 6) in _EU_HOLIDAYS_2026
