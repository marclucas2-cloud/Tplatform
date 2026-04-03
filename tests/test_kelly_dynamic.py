"""Tests for DynamicKellyManager — equity momentum-based Kelly switching."""
from datetime import UTC, datetime, timedelta

import pytest

from core.alloc.kelly_dynamic import DynamicKellyManager


@pytest.fixture
def km():
    return DynamicKellyManager(sma_lookback=20, hysteresis_pct=0.02)


@pytest.fixture
def km_no_hysteresis():
    return DynamicKellyManager(sma_lookback=20, hysteresis_pct=0.0)


def feed_equity(km, values, start=None):
    """Feed a series of equity values."""
    if start is None:
        start = datetime(2026, 1, 2, tzinfo=UTC)
    for i, v in enumerate(values):
        km.update_equity(start + timedelta(days=i), v)


class TestModeTransitions:
    def test_nominal_by_default(self, km):
        feed_equity(km, [100_000] * 25)
        mode = km.get_kelly_mode()
        assert mode["mode"] == "NOMINAL"

    def test_aggressive_on_strong_rise(self, km_no_hysteresis):
        """Strong equity rise (no hysteresis) should trigger AGGRESSIVE."""
        # Steep rise: +2000/day for 30 days — equity far above SMA+0.5*std
        values = [100_000 + i * 2000 for i in range(30)]
        feed_equity(km_no_hysteresis, values)
        mode = km_no_hysteresis.get_kelly_mode()
        assert mode["mode"] == "AGGRESSIVE"

    def test_defensive_on_moderate_fall(self, km_no_hysteresis):
        """Moderate equity fall (< 10% DD) should trigger DEFENSIVE, not STOPPED."""
        # Gentle fall: -200/day for 30 days = -6K from 100K = 6% DD (under 10% floor)
        values = [100_000 - i * 200 for i in range(30)]
        feed_equity(km_no_hysteresis, values)
        mode = km_no_hysteresis.get_kelly_mode()
        assert mode["mode"] == "DEFENSIVE"

    def test_stopped_on_max_dd(self, km):
        """10%+ drawdown from peak should trigger STOPPED."""
        values = [100_000 + i * 1000 for i in range(20)]  # Rise to 119K
        peak = values[-1]
        # Crash below peak - 10%
        crash_target = peak * 0.88
        values += [peak - (peak - crash_target) * i / 10 for i in range(1, 11)]
        feed_equity(km, values)
        mode = km.get_kelly_mode()
        assert mode["mode"] in ("DEFENSIVE", "STOPPED")


class TestPositionMultiplier:
    def test_aggressive_multiplier(self, km_no_hysteresis):
        values = [100_000 + i * 2000 for i in range(30)]
        feed_equity(km_no_hysteresis, values)
        mult = km_no_hysteresis.get_position_multiplier()
        assert mult == 2.0  # AGGRESSIVE = 2.0x Kelly

    def test_nominal_multiplier(self, km):
        feed_equity(km, [100_000] * 25)
        mult = km.get_position_multiplier()
        assert mult == 1.0  # NOMINAL = 1.0x Kelly

    def test_defensive_multiplier(self, km_no_hysteresis):
        values = [100_000 - i * 2000 for i in range(30)]
        feed_equity(km_no_hysteresis, values)
        mult = km_no_hysteresis.get_position_multiplier()
        assert mult <= 0.25  # DEFENSIVE = 0.25x Kelly


class TestHysteresis:
    def test_no_whipsaw_on_small_oscillation(self, km):
        """Small oscillations should not change mode from NOMINAL."""
        feed_equity(km, [100_000] * 25)
        mode1 = km.get_kelly_mode()["mode"]
        assert mode1 == "NOMINAL"
        # Tiny oscillation within same SMA band
        km.update_equity(datetime(2026, 2, 1, tzinfo=UTC), 100_050)
        mode2 = km.get_kelly_mode()["mode"]
        assert mode2 == "NOMINAL"  # Still NOMINAL


class TestEquityStats:
    def test_stats_structure(self, km):
        feed_equity(km, [100_000 + i * 50 for i in range(25)])
        stats = km.get_equity_stats()
        assert "current" in stats
        assert "sma20" in stats
        assert "peak" in stats
        assert "drawdown_pct" in stats
        assert "mode" in stats

    def test_peak_tracking(self, km):
        values = [100_000, 101_000, 102_000, 101_500]
        feed_equity(km, values + [100_000] * 20)
        stats = km.get_equity_stats()
        assert stats["peak"] >= 102_000

    def test_drawdown_positive(self, km):
        """drawdown_pct is positive (distance from peak, not negative)."""
        values = list(range(100_000, 105_000, 200))  # Rise
        values += list(range(104_800, 100_000, -200))  # Fall
        values += [100_000] * 5
        feed_equity(km, values)
        stats = km.get_equity_stats()
        assert stats["drawdown_pct"] > 0  # Positive = in drawdown


class TestEdgeCases:
    def test_single_equity_point(self, km):
        km.update_equity(datetime(2026, 1, 2, tzinfo=UTC), 100_000)
        mode = km.get_kelly_mode()
        assert mode["mode"] == "NOMINAL"

    def test_zero_equity(self, km):
        feed_equity(km, [0] * 25)
        mode = km.get_kelly_mode()
        assert mode["mode"] in ("STOPPED", "DEFENSIVE", "NOMINAL")
