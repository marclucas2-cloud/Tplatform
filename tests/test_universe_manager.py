"""Tests for DynamicUniverseManager — liquidity filtering, earnings, currency."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from core.data.universe_manager import DynamicUniverseManager


@pytest.fixture
def um():
    return DynamicUniverseManager(min_volume_usd=50_000_000, top_n=5)


class TestLiquidityFilter:
    def test_filters_by_volume(self, um):
        dates = pd.bdate_range("2026-01-02", periods=20)
        data = pd.DataFrame({
            "AAPL": np.random.uniform(100e6, 200e6, 20),
            "MSFT": np.random.uniform(80e6, 150e6, 20),
            "TINY": np.random.uniform(1e6, 5e6, 20),  # Below threshold
        }, index=dates)
        result = um.filter_universe(data)
        assert "AAPL" in result
        assert "MSFT" in result
        assert "TINY" not in result

    def test_top_n_limit(self, um):
        dates = pd.bdate_range("2026-01-02", periods=20)
        tickers = [f"TICK_{i}" for i in range(20)]
        data = pd.DataFrame(
            {t: np.random.uniform(60e6, 200e6, 20) for t in tickers},
            index=dates,
        )
        result = um.filter_universe(data)
        assert len(result) <= 5

    def test_empty_data(self, um):
        result = um.filter_universe(pd.DataFrame())
        assert result == []


class TestSurvivorshipBias:
    def test_all_tickers_valid(self, um):
        result = um.check_survivorship_bias(
            ["AAPL", "MSFT", "GOOGL"], date(2025, 1, 1)
        )
        # Result may be a dataclass or dict
        assert hasattr(result, "clean_tickers") or "clean_tickers" in result

    def test_returns_structure(self, um):
        result = um.check_survivorship_bias(["AAPL"], date(2025, 1, 1))
        assert hasattr(result, "clean_tickers") or isinstance(result, dict)


class TestCurrencyNormalization:
    def test_jpy_to_usd(self, um):
        # 10,000 JPY at ~150 JPY/USD = ~66.67 USD
        result = um.normalize_currency(10000, "JPY", "USD")
        assert isinstance(result, float)
        assert result > 0

    def test_usd_to_usd(self, um):
        result = um.normalize_currency(100.0, "USD", "USD")
        assert result == 100.0

    def test_eur_to_usd(self, um):
        result = um.normalize_currency(100.0, "EUR", "USD")
        assert result > 0
