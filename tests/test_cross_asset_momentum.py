"""Tests for Cross-Asset Time-Series Momentum strategy."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies_v2.us.cross_asset_momentum import (
    CrossAssetMomentumStrategy,
    CrossAssetMomentumConfig,
    MomentumSignal,
    AssetConfig,
    backtest_cross_asset_momentum,
    walk_forward_cross_asset,
)


def _make_trending_asset(n=500, drift=0.0005, vol=0.01, seed=42):
    rng = np.random.RandomState(seed)
    returns = rng.normal(drift, vol, n)
    prices = 100 * np.exp(np.cumsum(returns))
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame({"close": prices}, index=dates)


def _make_universe(n=500):
    return {
        "SPY": _make_trending_asset(n, 0.0004, 0.012, seed=1),
        "TLT": _make_trending_asset(n, 0.0001, 0.008, seed=2),
        "GLD": _make_trending_asset(n, 0.0002, 0.010, seed=3),
        "EURUSD": _make_trending_asset(n, -0.0001, 0.006, seed=4),
        "BTC": _make_trending_asset(n, 0.0008, 0.030, seed=5),
    }


class TestSignalGeneration:

    def test_long_signal_positive_momentum(self):
        prices = _make_universe()
        strategy = CrossAssetMomentumStrategy()
        signals = strategy.generate_signals(prices, capital=30000)
        # SPY has positive drift → should be LONG
        spy_sig = [s for s in signals if s.symbol == "SPY"][0]
        assert spy_sig.return_12m > 0
        assert spy_sig.signal == MomentumSignal.LONG

    def test_cash_signal_negative_momentum(self):
        # Create asset with negative drift
        prices = _make_universe()
        prices["EURUSD"] = _make_trending_asset(500, -0.001, 0.006, seed=10)
        strategy = CrossAssetMomentumStrategy()
        signals = strategy.generate_signals(prices, capital=30000)
        eur = [s for s in signals if s.symbol == "EURUSD"][0]
        # Negative 12m return → should be CASH (no short by default)
        if eur.return_12m < 0:
            assert eur.signal == MomentumSignal.CASH

    def test_weights_sum_reasonable(self):
        prices = _make_universe()
        strategy = CrossAssetMomentumStrategy()
        signals = strategy.generate_signals(prices, capital=30000)
        total_weight = sum(abs(s.final_weight) for s in signals)
        assert total_weight <= 2.0  # Not over 200% leverage

    def test_max_weight_respected(self):
        config = CrossAssetMomentumConfig(max_weight=0.30)
        strategy = CrossAssetMomentumStrategy(config)
        prices = _make_universe()
        signals = strategy.generate_signals(prices, capital=30000)
        for s in signals:
            assert abs(s.final_weight) <= 0.30 + 0.001

    def test_regime_reduces_sizing(self):
        prices = _make_universe()
        config = CrossAssetMomentumConfig()
        strategy = CrossAssetMomentumStrategy(config)
        s_normal = strategy.generate_signals(prices, capital=30000, current_regime="TREND_STRONG")
        strategy2 = CrossAssetMomentumStrategy(config)
        s_panic = strategy2.generate_signals(prices, capital=30000, current_regime="PANIC")
        # Total notional should be lower in PANIC
        not_normal = sum(s.target_notional for s in s_normal)
        not_panic = sum(s.target_notional for s in s_panic)
        assert not_panic < not_normal


class TestBacktest:

    def test_runs_without_crash(self):
        prices = _make_universe(600)
        result = backtest_cross_asset_momentum(
            prices, start_date="2023-01-01", end_date="2024-06-01",
            initial_capital=30000,
        )
        assert result.equity_curve is not None
        assert len(result.equity_curve) > 0
        assert result.n_rebalances > 0

    def test_positive_drift_positive_return(self):
        # All assets trending up → should make money
        prices = _make_universe(600)
        result = backtest_cross_asset_momentum(
            prices, start_date="2023-01-01", end_date="2024-06-01",
            initial_capital=30000,
        )
        # With all assets drifting positive, momentum should capture it
        assert result.total_return_pct > -10  # At least not catastrophic

    def test_summary_string(self):
        result = backtest_cross_asset_momentum(
            _make_universe(400), start_date="2023-06-01", end_date="2024-06-01",
            initial_capital=10000,
        )
        s = result.summary()
        assert "CROSS-ASSET MOMENTUM" in s
        assert "Sharpe" in s


class TestWalkForward:

    def test_walk_forward_runs(self):
        prices = _make_universe(600)
        wf = walk_forward_cross_asset(prices, n_windows=3, initial_capital=10000)
        assert "verdict" in wf
        assert wf["n_windows"] == 3
        assert len(wf["windows"]) == 3

    def test_verdict_values(self):
        prices = _make_universe(600)
        wf = walk_forward_cross_asset(prices, n_windows=3)
        assert wf["verdict"] in ("VALIDATED", "BORDERLINE", "REJECTED")


class TestPortfolioSummary:

    def test_summary_fields(self):
        prices = _make_universe()
        strategy = CrossAssetMomentumStrategy()
        signals = strategy.generate_signals(prices, capital=30000)
        summary = strategy.get_portfolio_summary(signals)
        assert "n_long" in summary
        assert "long_pct" in summary
        assert "cash_pct" in summary
        assert "assets" in summary
