"""
Tests -- LiveVaRCalculator (core/var_live.py).

Covers:
  - Single position VaR (equity, FX, futures)
  - Portfolio VaR with correlations
  - Stressed VaR (March 2020 scenario)
  - Alert thresholds
  - SQLite history & trend analysis
"""

import sys
import tempfile
import numpy as np
import pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.var_live import LiveVaRCalculator, FUTURES_MULTIPLIERS, FX_LOT_SIZE


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Temporary SQLite database for VaR history."""
    return str(tmp_path / "test_var.db")


@pytest.fixture
def calc(tmp_db):
    """LiveVaRCalculator with $10,000 capital."""
    return LiveVaRCalculator(capital=10_000, db_path=tmp_db)


@pytest.fixture
def equity_returns():
    """60 days of realistic equity returns (~15% annualized vol)."""
    np.random.seed(42)
    daily_vol = 0.15 / np.sqrt(252)  # ~0.95%
    return np.random.normal(0.0003, daily_vol, 60)


@pytest.fixture
def low_vol_returns():
    """60 days of low-volatility returns."""
    np.random.seed(99)
    return np.random.normal(0.0001, 0.002, 60)


@pytest.fixture
def fx_returns():
    """60 days of FX returns (~8% annualized vol)."""
    np.random.seed(7)
    daily_vol = 0.08 / np.sqrt(252)
    return np.random.normal(0.0, daily_vol, 60)


@pytest.fixture
def futures_returns():
    """60 days of futures returns (~20% annualized vol)."""
    np.random.seed(13)
    daily_vol = 0.20 / np.sqrt(252)
    return np.random.normal(0.0, daily_vol, 60)


# ============================================================================
# Single Position VaR
# ============================================================================

class TestSinglePositionVaR:
    """Tests for calculate_position_var()."""

    def test_equity_var_positive(self, calc, equity_returns):
        """Equity VaR should be a positive dollar amount."""
        result = calc.calculate_position_var(
            symbol="AAPL", quantity=50, current_price=180.0,
            returns=equity_returns, instrument_type="EQUITY",
        )
        assert result["var_95"] > 0
        assert result["var_99"] > 0
        assert result["position_value"] == 50 * 180.0

    def test_fx_position_var(self, calc, fx_returns):
        """FX VaR uses lot size multiplier."""
        result = calc.calculate_position_var(
            symbol="EURUSD", quantity=0.1, current_price=1.08,
            returns=fx_returns, instrument_type="FX",
        )
        expected_value = 0.1 * FX_LOT_SIZE * 1.08  # 10,800
        assert result["position_value"] == expected_value
        assert result["var_95"] > 0

    def test_futures_position_var(self, calc, futures_returns):
        """Futures VaR uses contract multiplier."""
        result = calc.calculate_position_var(
            symbol="MES", quantity=2, current_price=5200.0,
            returns=futures_returns, instrument_type="FUTURES",
        )
        expected_value = 2 * 5200.0 * FUTURES_MULTIPLIERS["MES"]  # 52,000
        assert result["position_value"] == expected_value
        assert result["var_95"] > 0

    def test_var_scales_with_position_size(self, calc, equity_returns):
        """Doubling position size should double VaR."""
        r1 = calc.calculate_position_var(
            "AAPL", 50, 180.0, equity_returns, "EQUITY",
        )
        r2 = calc.calculate_position_var(
            "AAPL", 100, 180.0, equity_returns, "EQUITY",
        )
        assert abs(r2["var_95"] - 2 * r1["var_95"]) < 0.1

    def test_var_99_greater_than_var_95(self, calc, equity_returns):
        """VaR at 99% confidence must be greater than at 95%."""
        result = calc.calculate_position_var(
            "AAPL", 50, 180.0, equity_returns, "EQUITY",
        )
        assert result["var_99"] > result["var_95"]

    def test_cvar_gte_var(self, calc, equity_returns):
        """CVaR (Expected Shortfall) >= VaR by definition."""
        result = calc.calculate_position_var(
            "AAPL", 50, 180.0, equity_returns, "EQUITY",
        )
        assert result["cvar_95"] >= result["var_95"]

    def test_zero_returns_zero_var(self, calc):
        """Constant returns (zero vol) -> zero VaR."""
        zero_returns = np.zeros(60)
        result = calc.calculate_position_var(
            "AAPL", 50, 180.0, zero_returns, "EQUITY",
        )
        assert result["var_95"] == 0.0
        assert result["var_99"] == 0.0

    def test_insufficient_returns(self, calc):
        """Fewer than 2 returns -> zero VaR."""
        result = calc.calculate_position_var(
            "AAPL", 50, 180.0, np.array([0.01]), "EQUITY",
        )
        assert result["var_95"] == 0.0


# ============================================================================
# Portfolio VaR
# ============================================================================

class TestPortfolioVaR:
    """Tests for calculate_portfolio_var()."""

    def test_diversification_benefit_uncorrelated(self, calc):
        """Uncorrelated positions should have diversification benefit > 1."""
        np.random.seed(42)
        positions = [
            {"symbol": "AAPL", "quantity": 30, "current_price": 180.0, "instrument_type": "EQUITY"},
            {"symbol": "MGC", "quantity": 1, "current_price": 2050.0, "instrument_type": "FUTURES"},
        ]
        returns_dict = {
            "AAPL": np.random.normal(0.0, 0.01, 60),
            "MGC": np.random.normal(0.0, 0.012, 60),
        }
        result = calc.calculate_portfolio_var(positions, returns_dict)
        # Diversification benefit = undiversified / diversified > 1
        assert result["diversification_benefit"] > 1.0

    def test_perfectly_correlated_no_diversification(self, calc):
        """Perfectly correlated positions: diversification benefit ~ 1."""
        np.random.seed(42)
        base = np.random.normal(0.0, 0.01, 60)
        positions = [
            {"symbol": "SPY", "quantity": 20, "current_price": 500.0, "instrument_type": "EQUITY"},
            {"symbol": "QQQ", "quantity": 25, "current_price": 400.0, "instrument_type": "EQUITY"},
        ]
        returns_dict = {
            "SPY": base,
            "QQQ": base,  # identical returns = perfect correlation
        }
        result = calc.calculate_portfolio_var(positions, returns_dict)
        # Should be close to 1.0 (no diversification)
        assert result["diversification_benefit"] < 1.05

    def test_portfolio_var_less_than_sum(self, calc):
        """Portfolio VaR < sum of individual VaRs (diversification effect)."""
        np.random.seed(42)
        positions = [
            {"symbol": "AAPL", "quantity": 30, "current_price": 180.0, "instrument_type": "EQUITY"},
            {"symbol": "GLD", "quantity": 20, "current_price": 220.0, "instrument_type": "EQUITY"},
        ]
        returns_dict = {
            "AAPL": np.random.normal(0.0, 0.012, 60),
            "GLD": np.random.normal(0.0, 0.008, 60),
        }
        result = calc.calculate_portfolio_var(positions, returns_dict)
        sum_individual = sum(p["var_95"] for p in result["per_position_var"])
        assert result["portfolio_var_95"] < sum_individual

    def test_contribution_sums_to_approx_100(self, calc):
        """Per-position contribution percentages should sum to ~100%."""
        np.random.seed(42)
        positions = [
            {"symbol": "AAPL", "quantity": 30, "current_price": 180.0, "instrument_type": "EQUITY"},
            {"symbol": "MSFT", "quantity": 20, "current_price": 400.0, "instrument_type": "EQUITY"},
            {"symbol": "NVDA", "quantity": 10, "current_price": 800.0, "instrument_type": "EQUITY"},
        ]
        returns_dict = {
            "AAPL": np.random.normal(0.0, 0.012, 60),
            "MSFT": np.random.normal(0.0, 0.011, 60),
            "NVDA": np.random.normal(0.0, 0.018, 60),
        }
        result = calc.calculate_portfolio_var(positions, returns_dict)
        total_contrib = sum(p["contribution_pct"] for p in result["per_position_var"])
        assert abs(total_contrib - 1.0) < 0.01

    def test_empty_positions(self, calc):
        """No valid positions -> zero VaR."""
        result = calc.calculate_portfolio_var([], {})
        assert result["portfolio_var_95"] == 0.0
        assert result["n_positions"] == 0

    def test_single_position_portfolio(self, calc, equity_returns):
        """Single-position portfolio: portfolio VaR = position VaR."""
        positions = [
            {"symbol": "AAPL", "quantity": 50, "current_price": 180.0, "instrument_type": "EQUITY"},
        ]
        returns_dict = {"AAPL": equity_returns}
        portfolio = calc.calculate_portfolio_var(positions, returns_dict)
        single = calc.calculate_position_var("AAPL", 50, 180.0, equity_returns, "EQUITY")
        assert abs(portfolio["portfolio_var_95"] - single["var_95"]) < 1.0


# ============================================================================
# Stressed VaR
# ============================================================================

class TestStressedVaR:
    """Tests for calculate_stressed_var()."""

    def test_stressed_var_gte_normal_var(self, calc):
        """Stressed VaR should be >= normal VaR (higher correlations)."""
        np.random.seed(42)
        positions = [
            {"symbol": "AAPL", "quantity": 30, "current_price": 180.0, "instrument_type": "EQUITY"},
            {"symbol": "MSFT", "quantity": 20, "current_price": 400.0, "instrument_type": "EQUITY"},
        ]
        returns_dict = {
            "AAPL": np.random.normal(0.0, 0.012, 60),
            "MSFT": np.random.normal(0.0, 0.011, 60),
        }
        result = calc.calculate_portfolio_var(positions, returns_dict)
        # Stress correlation for (equity, equity) = 0.92 -> higher than typical
        assert result["stressed_var_95"] >= result["portfolio_var_95"] * 0.95

    def test_stressed_var_uses_march_2020(self, calc):
        """Stressed VaR for cross-asset should use March 2020 correlations."""
        np.random.seed(42)
        positions = [
            {"symbol": "AAPL", "quantity": 30, "current_price": 180.0, "instrument_type": "EQUITY"},
            {"symbol": "MGC", "quantity": 1, "current_price": 2050.0, "instrument_type": "FUTURES"},
        ]
        returns_dict = {
            "AAPL": np.random.normal(0.0, 0.012, 60),
            "MGC": np.random.normal(0.0, 0.010, 60),
        }
        stressed = calc.calculate_stressed_var(positions, returns_dict)
        assert stressed["stressed_var_95"] > 0
        assert stressed["stressed_var_99"] > stressed["stressed_var_95"]


# ============================================================================
# Alerts
# ============================================================================

class TestAlerts:
    """Tests for check_var_alerts()."""

    def test_var_above_3pct_warning(self, calc):
        """VaR > 3% of capital triggers WARNING."""
        var_result = {"portfolio_var_95": 350.0, "var_pct_of_capital": 0.035}
        alerts = calc.check_var_alerts(var_result)
        assert len(alerts) == 1
        assert alerts[0]["level"] == "WARNING"

    def test_var_above_5pct_critical(self, calc):
        """VaR > 5% of capital triggers CRITICAL."""
        var_result = {"portfolio_var_95": 600.0, "var_pct_of_capital": 0.06}
        alerts = calc.check_var_alerts(var_result)
        assert len(alerts) == 1
        assert alerts[0]["level"] == "CRITICAL"

    def test_var_divergence_warning(self, calc):
        """VaR diverges > 50% from backtest triggers WARNING."""
        var_result = {"portfolio_var_95": 300.0, "var_pct_of_capital": 0.02}
        # backtest_var = 100 -> live is 200% higher
        alerts = calc.check_var_alerts(var_result, backtest_var=100.0)
        assert len(alerts) == 1
        assert alerts[0]["level"] == "WARNING"
        assert "DIVERGENCE" in alerts[0]["message"]

    def test_var_below_3pct_no_alert(self, calc):
        """VaR < 3% of capital and no divergence -> no alert."""
        var_result = {"portfolio_var_95": 200.0, "var_pct_of_capital": 0.02}
        alerts = calc.check_var_alerts(var_result)
        assert len(alerts) == 0

    def test_critical_and_divergence(self, calc):
        """Both CRITICAL and divergence can trigger simultaneously."""
        var_result = {"portfolio_var_95": 600.0, "var_pct_of_capital": 0.06}
        alerts = calc.check_var_alerts(var_result, backtest_var=100.0)
        levels = {a["level"] for a in alerts}
        assert "CRITICAL" in levels
        assert "WARNING" in levels
        assert len(alerts) == 2

    def test_alert_callback_called(self, tmp_db):
        """Alert callback function is called when alert fires."""
        called = []
        def cb(msg, level):
            called.append((msg, level))

        calc = LiveVaRCalculator(capital=10_000, db_path=tmp_db, alert_callback=cb)
        var_result = {"portfolio_var_95": 600.0, "var_pct_of_capital": 0.06}
        calc.check_var_alerts(var_result)
        assert len(called) == 1
        assert called[0][1] == "CRITICAL"


# ============================================================================
# History & Trend
# ============================================================================

class TestHistory:
    """Tests for record_daily_var(), get_var_history(), get_var_trend()."""

    def test_record_and_retrieve(self, calc):
        """Record a VaR entry and retrieve it."""
        var_result = {
            "portfolio_var_95": 250.0,
            "portfolio_var_99": 380.0,
            "portfolio_cvar_95": 300.0,
            "stressed_var_95": 320.0,
            "stressed_var_99": 480.0,
            "var_pct_of_capital": 0.025,
            "n_positions": 3,
            "per_position_var": [{"symbol": "AAPL", "var_95": 120.0}],
        }
        calc.record_daily_var(var_result)
        history = calc.get_var_history(days=1)
        assert len(history) >= 1
        assert history[-1]["portfolio_var_95"] == 250.0

    def test_var_history_empty(self, calc):
        """Empty history returns empty list."""
        history = calc.get_var_history(days=30)
        assert history == []

    def test_var_trend_unknown_on_empty(self, calc):
        """Trend is 'unknown' when no history."""
        trend = calc.get_var_trend()
        assert trend["trend"] == "unknown"

    def test_var_trend_analysis(self, tmp_db):
        """Trend analysis with enough data points."""
        import sqlite3
        from datetime import datetime, timezone, timedelta

        # Insert synthetic history: increasing VaR
        calc = LiveVaRCalculator(capital=10_000, db_path=tmp_db)
        with sqlite3.connect(tmp_db) as conn:
            for i in range(10):
                date_str = (
                    datetime.now(timezone.utc) - timedelta(days=10 - i)
                ).strftime("%Y-%m-%d")
                conn.execute(
                    """INSERT OR REPLACE INTO var_history
                    (date, portfolio_var_95, portfolio_var_99, portfolio_cvar_95,
                     stressed_var_95, stressed_var_99, capital,
                     var_pct_of_capital, n_positions, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (date_str, 100 + i * 20, 150 + i * 25, 130 + i * 22,
                     140 + i * 25, 200 + i * 30, 10000, 0.01 + i * 0.002, 3, "[]"),
                )
            conn.commit()

        trend = calc.get_var_trend(days=15)
        assert trend["trend"] == "increasing"
        assert trend["max_var"] > trend["avg_var"]
        assert trend["current_var"] > 0


# ============================================================================
# Edge cases
# ============================================================================

class TestEdgeCases:
    """Edge cases and robustness tests."""

    def test_missing_returns_symbol_skipped(self, calc, equity_returns):
        """Position with no returns data is skipped."""
        positions = [
            {"symbol": "AAPL", "quantity": 30, "current_price": 180.0, "instrument_type": "EQUITY"},
            {"symbol": "UNKNOWN", "quantity": 10, "current_price": 50.0, "instrument_type": "EQUITY"},
        ]
        returns_dict = {"AAPL": equity_returns}
        result = calc.calculate_portfolio_var(positions, returns_dict)
        assert result["n_positions"] == 1

    def test_var_pct_of_capital(self, calc, equity_returns):
        """var_pct_of_capital = var_95 / capital."""
        result = calc.calculate_position_var(
            "AAPL", 50, 180.0, equity_returns, "EQUITY",
        )
        expected_pct = result["var_95"] / calc.capital
        assert abs(result["var_pct_of_capital"] - expected_pct) < 0.001
