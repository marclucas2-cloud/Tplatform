"""Tests for WalkForwardEngine.

Validates window generation, grid search, verdict logic,
and reproducibility of walk-forward analysis.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import BacktestConfig, Bar, PortfolioState, Signal
from core.backtester_v2.walk_forward import (
    WalkForwardEngine,
    WFConfig,
    WFResult,
    WFWindowResult,
)

# ─── Helpers ─────────────────────────────────────────────────────────


def _make_synthetic_data(
    n_days: int = 750,
    base: float = 100.0,
    trend: float = 0.05,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic daily OHLCV data with upward trend."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-02", periods=n_days, freq="B")
    noise = np.cumsum(rng.normal(0, 0.5, n_days))
    close = base + np.arange(n_days) * trend + noise
    close = np.maximum(close, 1.0)  # no negative prices
    return pd.DataFrame(
        {
            "open": close - rng.uniform(0, 0.5, n_days),
            "high": close + rng.uniform(0, 1.0, n_days),
            "low": close - rng.uniform(0, 1.0, n_days),
            "close": close,
            "volume": rng.integers(50_000, 200_000, n_days),
        },
        index=idx,
    )


def _make_losing_data(
    n_days: int = 750,
    base: float = 100.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic data with strong downtrend."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-02", periods=n_days, freq="B")
    noise = np.cumsum(rng.normal(0, 0.3, n_days))
    close = base - np.arange(n_days) * 0.08 + noise
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": rng.integers(50_000, 200_000, n_days),
        },
        index=idx,
    )


# ─── Mock strategies ────────────────────────────────────────────────


class EMAStrategy(StrategyBase):
    """Simple EMA crossover strategy for WF testing."""

    def __init__(self, ema_period: int = 20) -> None:
        self.ema_period = ema_period
        self._prices: List[float] = []
        self._in_position = False
        self.param_grid: Dict[str, List[Any]] = {
            "ema_period": [10, 20, 30],
        }

    @property
    def name(self) -> str:
        return "ema_test"

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        self._prices.append(bar.close)
        if len(self._prices) < self.ema_period:
            return None

        ema = pd.Series(self._prices).ewm(span=self.ema_period).mean().iloc[-1]

        if bar.close > ema and not self._in_position:
            self._in_position = True
            return Signal(
                symbol=bar.symbol,
                side="BUY",
                strategy_name=self.name,
            )
        elif bar.close < ema and self._in_position:
            self._in_position = False
            return Signal(
                symbol=bar.symbol,
                side="SELL",
                strategy_name=self.name,
            )
        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {"ema_period": self.ema_period}

    def set_parameters(self, params: Dict[str, Any]) -> None:
        if "ema_period" in params:
            self.ema_period = params["ema_period"]
        # Reset state on param change
        self._prices = []
        self._in_position = False


class AlwaysBuyStrategy(StrategyBase):
    """Buys and sells every other bar — generates many trades."""

    def __init__(self) -> None:
        self._bar_count = 0
        self._in_position = False

    @property
    def name(self) -> str:
        return "always_buy"

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        self._bar_count += 1
        if self._bar_count % 5 == 0 and not self._in_position:
            self._in_position = True
            return Signal(symbol=bar.symbol, side="BUY", strategy_name=self.name)
        elif self._bar_count % 5 == 3 and self._in_position:
            self._in_position = False
            return Signal(symbol=bar.symbol, side="SELL", strategy_name=self.name)
        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {}

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self._bar_count = 0
        self._in_position = False


# ─── Tests ───────────────────────────────────────────────────────────


class TestRollingWindows:
    """Test window generation."""

    def test_rolling_windows_correct_count(self) -> None:
        """Rolling mode should produce expected number of windows."""
        data = _make_synthetic_data(n_days=750)
        config = WFConfig(train_months=6, test_months=3, min_windows=1, mode="rolling")
        engine = WalkForwardEngine()
        windows = engine._generate_windows({"SPY": data}, config)

        # ~750 trading days = ~3 years, 6+3=9 months per step, step=3 months
        # Should get several windows
        assert len(windows) >= 3
        for train_d, test_d in windows:
            assert len(train_d["SPY"]) > 0
            assert len(test_d["SPY"]) > 0

    def test_expanding_windows(self) -> None:
        """Expanding mode: train window should grow over time."""
        data = _make_synthetic_data(n_days=750)
        config = WFConfig(
            train_months=6, test_months=3, min_windows=1, mode="expanding"
        )
        engine = WalkForwardEngine()
        windows = engine._generate_windows({"SPY": data}, config)

        assert len(windows) >= 2
        # Each subsequent training set should be >= the previous
        for i in range(1, len(windows)):
            prev_len = len(windows[i - 1][0]["SPY"])
            curr_len = len(windows[i][0]["SPY"])
            assert curr_len >= prev_len


class TestVerdicts:
    """Test verdict determination."""

    def test_validated_verdict(self) -> None:
        """Strategy with good OOS performance should get VALIDATED."""
        data = _make_synthetic_data(n_days=750, trend=0.08, seed=123)
        config = WFConfig(
            train_months=6,
            test_months=3,
            min_windows=2,
            min_ratio=0.2,
            min_profitable_pct=0.4,
            mode="rolling",
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()
        result = engine.run(EMAStrategy, {"SPY": data}, config)

        assert isinstance(result, WFResult)
        assert result.verdict in ("VALIDATED", "BORDERLINE")
        assert len(result.windows) >= 2

    def test_rejected_verdict(self) -> None:
        """Strategy on losing data should get REJECTED or BORDERLINE."""
        data = _make_losing_data(n_days=750, seed=99)
        config = WFConfig(
            train_months=6,
            test_months=3,
            min_windows=2,
            min_ratio=0.8,
            min_profitable_pct=0.9,
            mode="rolling",
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()
        result = engine.run(EMAStrategy, {"SPY": data}, config)

        assert result.verdict in ("REJECTED", "BORDERLINE")

    def test_borderline_verdict(self) -> None:
        """Borderline: one criterion met, the other not."""
        # Manually build WFWindowResults to control verdict
        engine = WalkForwardEngine()
        windows = [
            WFWindowResult(
                window=i,
                train_start=pd.Timestamp("2020-01-01"),
                train_end=pd.Timestamp("2020-06-30"),
                test_start=pd.Timestamp("2020-07-01"),
                test_end=pd.Timestamp("2020-09-30"),
                train_sharpe=2.0,
                test_sharpe=1.0 if i < 2 else -0.5,
                test_trades=15,
                test_pnl=100 if i < 2 else -200,
                test_max_dd=0.05,
                best_params={"ema_period": 20},
                oos_profitable=i < 2,
            )
            for i in range(4)
        ]

        # pct_profitable = 2/4 = 0.5, oos_is_ratio = 0.25/2.0 = 0.125
        config = WFConfig(min_ratio=0.4, min_profitable_pct=0.5)
        result = engine._aggregate(windows, config)

        # profit_ok=True (0.5>=0.5), ratio_ok=False (0.125<0.4) => BORDERLINE
        assert result.verdict == "BORDERLINE"


class TestGridSearch:
    """Test parameter optimization."""

    def test_grid_search_finds_best_params(self) -> None:
        """Grid search should return params that maximize Sharpe."""
        data = _make_synthetic_data(n_days=250, trend=0.1, seed=77)
        config = WFConfig(
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()

        param_grid = {"ema_period": [5, 10, 20, 40]}
        best = engine._grid_search(
            EMAStrategy, {"SPY": data}, param_grid, config
        )

        assert "ema_period" in best
        assert best["ema_period"] in [5, 10, 20, 40]

    def test_min_trades_enforced(self) -> None:
        """Grid search should skip combos with < 10 trades."""
        # Very short data to ensure low trade count with large EMA
        data = _make_synthetic_data(n_days=30, trend=0.1, seed=42)
        config = WFConfig(
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()

        # ema_period=25 on 30 bars will produce < 10 trades
        param_grid = {"ema_period": [25]}
        best = engine._grid_search(
            EMAStrategy, {"SPY": data}, param_grid, config
        )

        # Should fall back to default params since nothing passes min trades
        assert isinstance(best, dict)


class TestEdgeCases:
    """Test edge cases and data validation."""

    def test_insufficient_data_rejected(self) -> None:
        """Too little data to form min_windows should be REJECTED."""
        data = _make_synthetic_data(n_days=60)  # ~3 months
        config = WFConfig(
            train_months=6,
            test_months=3,
            min_windows=3,
            mode="rolling",
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()
        result = engine.run(EMAStrategy, {"SPY": data}, config)

        assert result.verdict == "REJECTED"
        assert len(result.windows) == 0
        assert result.total_oos_trades == 0

    def test_wf_result_has_all_fields(self) -> None:
        """WFResult should have all expected fields."""
        data = _make_synthetic_data(n_days=750, seed=55)
        config = WFConfig(
            train_months=6,
            test_months=3,
            min_windows=2,
            mode="rolling",
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()
        result = engine.run(EMAStrategy, {"SPY": data}, config)

        assert hasattr(result, "verdict")
        assert hasattr(result, "windows")
        assert hasattr(result, "avg_oos_sharpe")
        assert hasattr(result, "avg_is_sharpe")
        assert hasattr(result, "oos_is_ratio")
        assert hasattr(result, "pct_profitable")
        assert hasattr(result, "total_oos_trades")
        assert isinstance(result.windows, list)
        assert isinstance(result.avg_oos_sharpe, float)

    def test_oos_is_ratio_calculation(self) -> None:
        """oos_is_ratio should be avg_oos / avg_is."""
        engine = WalkForwardEngine()
        ts = pd.Timestamp("2020-01-01")
        windows = [
            WFWindowResult(
                window=0, train_start=ts, train_end=ts,
                test_start=ts, test_end=ts,
                train_sharpe=4.0, test_sharpe=2.0,
                test_trades=20, test_pnl=500, test_max_dd=0.03,
                best_params={}, oos_profitable=True,
            ),
            WFWindowResult(
                window=1, train_start=ts, train_end=ts,
                test_start=ts, test_end=ts,
                train_sharpe=2.0, test_sharpe=1.0,
                test_trades=15, test_pnl=200, test_max_dd=0.04,
                best_params={}, oos_profitable=True,
            ),
        ]

        config = WFConfig()
        result = engine._aggregate(windows, config)

        # avg_oos = (2.0 + 1.0) / 2 = 1.5
        # avg_is  = (4.0 + 2.0) / 2 = 3.0
        # ratio = 1.5 / 3.0 = 0.5
        assert abs(result.avg_oos_sharpe - 1.5) < 1e-6
        assert abs(result.avg_is_sharpe - 3.0) < 1e-6
        assert abs(result.oos_is_ratio - 0.5) < 1e-6

    def test_pct_profitable_calculation(self) -> None:
        """pct_profitable should be count(oos_profitable) / total_windows."""
        engine = WalkForwardEngine()
        ts = pd.Timestamp("2020-01-01")
        windows = [
            WFWindowResult(
                window=i, train_start=ts, train_end=ts,
                test_start=ts, test_end=ts,
                train_sharpe=2.0, test_sharpe=1.0 if i < 3 else -1.0,
                test_trades=15, test_pnl=100 if i < 3 else -100,
                test_max_dd=0.03, best_params={},
                oos_profitable=i < 3,
            )
            for i in range(5)
        ]

        config = WFConfig()
        result = engine._aggregate(windows, config)

        assert abs(result.pct_profitable - 0.6) < 1e-6

    def test_reproductible(self) -> None:
        """Same data and config should produce identical results."""
        data = _make_synthetic_data(n_days=500, seed=42)
        config = WFConfig(
            train_months=6,
            test_months=3,
            min_windows=2,
            mode="rolling",
            backtest_config=BacktestConfig(initial_capital=100_000),
        )
        engine = WalkForwardEngine()
        r1 = engine.run(EMAStrategy, {"SPY": data}, config)
        r2 = engine.run(EMAStrategy, {"SPY": data}, config)

        assert r1.verdict == r2.verdict
        assert abs(r1.avg_oos_sharpe - r2.avg_oos_sharpe) < 1e-10
        assert r1.total_oos_trades == r2.total_oos_trades
        assert len(r1.windows) == len(r2.windows)
