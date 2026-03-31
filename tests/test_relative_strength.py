"""Tests for RelativeStrengthFilter."""
import numpy as np
import pandas as pd
import pytest

from core.risk.relative_strength import RelativeStrengthFilter


@pytest.fixture
def rsf():
    return RelativeStrengthFilter(lookback_days=20, min_outperformance=0.0)


class TestAlphaScore:
    def test_positive_alpha(self, rsf):
        score = rsf.compute_alpha_score(0.05, 0.03)
        assert score == pytest.approx(0.02)

    def test_negative_alpha(self, rsf):
        score = rsf.compute_alpha_score(-0.02, 0.03)
        assert score == pytest.approx(-0.05)

    def test_zero_alpha(self, rsf):
        score = rsf.compute_alpha_score(0.03, 0.03)
        assert score == pytest.approx(0.0)


class TestBuyFilter:
    def test_outperformer_allowed(self, rsf):
        ok, reason = rsf.should_allow_buy("ASML", alpha_score=0.02)
        assert ok
        assert "OK" in reason

    def test_underperformer_blocked(self, rsf):
        ok, reason = rsf.should_allow_buy("ASML", alpha_score=-0.01)
        assert not ok
        assert "BLOCKED" in reason

    def test_zero_alpha_blocked(self, rsf):
        ok, _ = rsf.should_allow_buy("ASML", alpha_score=0.0)
        assert not ok  # Must strictly outperform


class TestShortFilter:
    def test_laggard_allowed(self, rsf):
        ok, reason = rsf.should_allow_short("TSLA", alpha_score=-0.03)
        assert ok
        assert "laggard" in reason

    def test_outperformer_blocked(self, rsf):
        ok, reason = rsf.should_allow_short("TSLA", alpha_score=0.02)
        assert not ok
        assert "BLOCKED" in reason


class TestMomentumDivergence:
    def test_bearish_divergence(self, rsf):
        stock = pd.Series([0.001, 0.000, -0.001, 0.000, 0.001])
        index = pd.Series([0.005, 0.004, 0.003, 0.004, 0.005])
        result = rsf.detect_momentum_divergence(stock, index, window=5)
        assert result["divergent"]
        assert result["type"] == "BEARISH_DIVERGENCE"

    def test_bullish_divergence(self, rsf):
        stock = pd.Series([0.001, 0.000, 0.001, 0.000, 0.001])
        index = pd.Series([-0.005, -0.004, -0.003, -0.004, -0.005])
        result = rsf.detect_momentum_divergence(stock, index, window=5)
        assert result["divergent"]
        assert result["type"] == "BULLISH_DIVERGENCE"

    def test_no_divergence(self, rsf):
        stock = pd.Series([0.005, 0.004, 0.003, 0.004, 0.005])
        index = pd.Series([0.005, 0.004, 0.003, 0.004, 0.005])
        result = rsf.detect_momentum_divergence(stock, index, window=5)
        assert not result["divergent"]

    def test_insufficient_data(self, rsf):
        stock = pd.Series([0.001])
        index = pd.Series([0.001])
        result = rsf.detect_momentum_divergence(stock, index, window=5)
        assert result["type"] == "INSUFFICIENT_DATA"


class TestBenchmarkMapping:
    def test_us_stock(self, rsf):
        assert rsf.get_sector_benchmark("AAPL") == "XLK"

    def test_eu_stock(self, rsf):
        assert rsf.get_sector_benchmark("MC.PA") == "VGK"

    def test_jp_stock(self, rsf):
        assert rsf.get_sector_benchmark("7203.T") == "EWJ"

    def test_unknown_defaults_us(self, rsf):
        assert rsf.get_sector_benchmark("UNKNOWN_TICKER") == "SPY"
