"""
Tests unitaires du CostTracker.

Verifie :
  - Enregistrement de commission
  - Cost report par strategie
  - Calcul du cost_ratio
  - Alerte quand ratio > 30%
  - Kill recommendation quand ratio > 50%
  - Viabilite de strategie
  - Filtrage par periode
  - Strategies multiples
  - Gestion donnees vides
  - Validation des entrees
"""
import os
import sys
import pytest
import sqlite3
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.cost_tracker import CostTracker


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite db path."""
    return tmp_path / "test_execution_metrics.db"


@pytest.fixture
def tracker(tmp_db):
    """CostTracker with no alert callback and temp db."""
    return CostTracker(db_path=tmp_db, alert_callback=None)


@pytest.fixture
def mock_alert():
    """Mock alert callback."""
    return MagicMock()


@pytest.fixture
def tracker_with_alert(tmp_db, mock_alert):
    """CostTracker with mock alert callback."""
    return CostTracker(db_path=tmp_db, alert_callback=mock_alert)


def _insert_old_commission(db_path, trade_id, strategy, days_ago,
                           commission=0.50, notional=1000.0, pnl=10.0):
    """Insert a commission with a timestamp in the past for period filtering tests."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cost_ratio = commission / abs(pnl) if pnl else None
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO cost_log
               (trade_id, timestamp, strategy, instrument, instrument_type,
                commission, notional_value, pnl_gross, cost_ratio)
               VALUES (?, ?, ?, 'SPY', 'EQUITY', ?, ?, ?, ?)""",
            (trade_id, ts, strategy, commission, notional, pnl, cost_ratio),
        )
        conn.commit()


# =============================================================================
# TESTS — Record commission
# =============================================================================

class TestRecordCommission:
    """Tests for record_commission method."""

    def test_record_basic(self, tracker):
        """Record a commission and verify output."""
        result = tracker.record_commission(
            trade_id="T001",
            strategy="momentum",
            instrument="SPY",
            instrument_type="EQUITY",
            commission=0.50,
            notional_value=10000.0,
            pnl_gross=25.0,
        )
        assert result["trade_id"] == "T001"
        assert result["commission"] == 0.50
        assert result["cost_ratio"] == 0.02  # 0.50 / 25.0 = 0.02
        assert result["cost_per_dollar_traded"] > 0

    def test_record_without_pnl(self, tracker):
        """Record commission without P&L — cost_ratio should be None."""
        result = tracker.record_commission(
            trade_id="T002",
            strategy="pairs",
            instrument="MU",
            instrument_type="EQUITY",
            commission=0.25,
            notional_value=5000.0,
        )
        assert result["cost_ratio"] is None

    def test_record_with_zero_pnl(self, tracker):
        """Record commission with zero P&L — cost_ratio should be None."""
        result = tracker.record_commission(
            trade_id="T003",
            strategy="pairs",
            instrument="AMAT",
            instrument_type="EQUITY",
            commission=0.30,
            notional_value=5000.0,
            pnl_gross=0.0,
        )
        assert result["cost_ratio"] is None

    def test_invalid_negative_commission(self, tracker):
        """Should raise ValueError for negative commission."""
        with pytest.raises(ValueError, match="commission must be >= 0"):
            tracker.record_commission(
                trade_id="ERR1",
                strategy="test",
                instrument="BAD",
                instrument_type="EQUITY",
                commission=-1.0,
                notional_value=1000.0,
            )

    def test_invalid_zero_notional(self, tracker):
        """Should raise ValueError for non-positive notional_value."""
        with pytest.raises(ValueError, match="notional_value must be > 0"):
            tracker.record_commission(
                trade_id="ERR2",
                strategy="test",
                instrument="BAD",
                instrument_type="EQUITY",
                commission=0.50,
                notional_value=0,
            )

    def test_cost_ratio_with_negative_pnl(self, tracker):
        """Cost ratio uses abs(pnl) — works with losses."""
        result = tracker.record_commission(
            trade_id="T004",
            strategy="momentum",
            instrument="SPY",
            instrument_type="EQUITY",
            commission=0.50,
            notional_value=10000.0,
            pnl_gross=-20.0,
        )
        # cost_ratio = 0.50 / abs(-20.0) = 0.025
        assert result["cost_ratio"] == 0.025


# =============================================================================
# TESTS — Cost report
# =============================================================================

class TestCostReport:
    """Tests for get_cost_report method."""

    def test_basic_cost_report(self, tracker):
        """Basic cost report with one strategy."""
        tracker.record_commission("R001", "momentum", "SPY", "EQUITY", 0.50, 10000.0, 50.0)
        tracker.record_commission("R002", "momentum", "QQQ", "EQUITY", 0.60, 12000.0, 30.0)

        report = tracker.get_cost_report(period="30d")

        assert report["total_commission"] == 1.10
        assert report["total_pnl_gross"] == 80.0
        assert report["total_pnl_net"] == 80.0 - 1.10
        assert report["n_trades"] == 2
        assert report["avg_commission_per_trade"] == 0.55
        # cost_ratio = 1.10 / 80.0 = 0.01375
        assert abs(report["cost_ratio"] - 1.10 / 80.0) < 0.001

    def test_report_by_strategy(self, tracker):
        """Report includes breakdown by strategy."""
        tracker.record_commission("R010", "alpha", "SPY", "EQUITY", 0.50, 10000.0, 50.0)
        tracker.record_commission("R011", "beta", "QQQ", "EQUITY", 0.80, 8000.0, 20.0)

        report = tracker.get_cost_report(period="30d")

        assert "alpha" in report["by_strategy"]
        assert "beta" in report["by_strategy"]
        assert report["by_strategy"]["alpha"]["cost_ratio"] == 0.01  # 0.50/50 = 0.01
        assert report["by_strategy"]["beta"]["cost_ratio"] == 0.04   # 0.80/20 = 0.04

    def test_report_strategy_filter(self, tracker):
        """Report filters by strategy name."""
        tracker.record_commission("R020", "alpha", "SPY", "EQUITY", 0.50, 10000.0, 50.0)
        tracker.record_commission("R021", "beta", "QQQ", "EQUITY", 0.80, 8000.0, 20.0)

        report = tracker.get_cost_report(strategy="alpha", period="30d")
        assert report["n_trades"] == 1
        assert report["total_commission"] == 0.50

    def test_report_empty_data(self, tracker):
        """Report handles empty data gracefully."""
        report = tracker.get_cost_report(period="30d")
        assert report["total_commission"] == 0.0
        assert report["total_pnl_gross"] == 0.0
        assert report["n_trades"] == 0
        assert report["cost_ratio"] == 0.0

    def test_cost_per_dollar_traded(self, tracker):
        """Cost per dollar traded (in bps)."""
        tracker.record_commission("R030", "s", "SPY", "EQUITY", 1.0, 10000.0, 50.0)
        report = tracker.get_cost_report(period="30d")
        # 1.0 / 10000.0 * 10000 = 1.0 bps
        assert report["cost_per_dollar_traded"] == 1.0


# =============================================================================
# TESTS — Alerts
# =============================================================================

class TestCostAlerts:
    """Tests for check_cost_alerts method."""

    def test_warning_at_30_percent(self, tracker_with_alert, mock_alert):
        """Alert WARNING when cost_ratio > 30%."""
        for i in range(10):
            tracker_with_alert.record_commission(
                f"W{i:03d}", "expensive_strat", "SPY", "EQUITY",
                commission=5.0, notional_value=1000.0, pnl_gross=10.0,
            )

        mock_alert.reset_mock()
        alerts = tracker_with_alert.check_cost_alerts()

        assert len(alerts) >= 1
        alert = [a for a in alerts if a["strategy"] == "expensive_strat"][0]
        assert alert["level"] == "warning"
        assert alert["cost_ratio"] >= 0.30

    def test_critical_kill_at_50_percent(self, tracker_with_alert, mock_alert):
        """CRITICAL kill recommendation when ratio > 50% on 30+ trades."""
        for i in range(35):
            tracker_with_alert.record_commission(
                f"K{i:03d}", "doomed_strat", "SPY", "EQUITY",
                commission=10.0, notional_value=1000.0, pnl_gross=15.0,
            )

        mock_alert.reset_mock()
        alerts = tracker_with_alert.check_cost_alerts()

        critical = [a for a in alerts if a["strategy"] == "doomed_strat"]
        assert len(critical) == 1
        assert critical[0]["level"] == "critical"
        assert "KILL" in critical[0]["recommendation"]

    def test_no_kill_under_30_trades(self, tracker_with_alert, mock_alert):
        """No CRITICAL kill if < 30 trades even with high cost_ratio."""
        for i in range(10):
            tracker_with_alert.record_commission(
                f"NK{i:03d}", "small_strat", "SPY", "EQUITY",
                commission=10.0, notional_value=1000.0, pnl_gross=15.0,
            )

        mock_alert.reset_mock()
        alerts = tracker_with_alert.check_cost_alerts()

        critical = [a for a in alerts if a["strategy"] == "small_strat" and a["level"] == "critical"]
        assert len(critical) == 0

    def test_no_alert_healthy_strategy(self, tracker_with_alert, mock_alert):
        """No alert for healthy cost ratio."""
        for i in range(20):
            tracker_with_alert.record_commission(
                f"H{i:03d}", "healthy_strat", "SPY", "EQUITY",
                commission=0.10, notional_value=10000.0, pnl_gross=100.0,
            )

        mock_alert.reset_mock()
        alerts = tracker_with_alert.check_cost_alerts()

        healthy = [a for a in alerts if a["strategy"] == "healthy_strat"]
        assert len(healthy) == 0


# =============================================================================
# TESTS — Strategy viability
# =============================================================================

class TestStrategyViability:
    """Tests for get_strategy_viability method."""

    def test_viable_strategy(self, tracker):
        """Strategy is viable when cost_ratio < 50%."""
        for i in range(40):
            tracker.record_commission(
                f"V{i:03d}", "good_strat", "SPY", "EQUITY",
                commission=0.50, notional_value=10000.0, pnl_gross=50.0,
            )

        result = tracker.get_strategy_viability("good_strat")

        assert result["viable"] is True
        assert result["sufficient_data"] is True
        assert result["cost_ratio"] < 0.50
        assert result["n_trades"] == 40

    def test_non_viable_strategy(self, tracker):
        """Strategy is NOT viable when cost_ratio >= 50% on enough trades."""
        for i in range(35):
            tracker.record_commission(
                f"NV{i:03d}", "bad_strat", "SPY", "EQUITY",
                commission=8.0, notional_value=1000.0, pnl_gross=10.0,
            )

        result = tracker.get_strategy_viability("bad_strat")
        assert result["viable"] is False
        assert result["cost_ratio"] >= 0.50

    def test_insufficient_data(self, tracker):
        """Insufficient data — strategy marked viable by default."""
        for i in range(5):
            tracker.record_commission(
                f"ID{i:03d}", "new_strat", "SPY", "EQUITY",
                commission=5.0, notional_value=1000.0, pnl_gross=8.0,
            )

        result = tracker.get_strategy_viability("new_strat")
        assert result["sufficient_data"] is False
        assert result["viable"] is True  # Not enough data to condemn

    def test_break_even_sharpe(self, tracker):
        """Break-even Sharpe increases with cost ratio."""
        for i in range(30):
            tracker.record_commission(
                f"BE{i:03d}", "mediocre_strat", "SPY", "EQUITY",
                commission=2.0, notional_value=1000.0, pnl_gross=10.0,
            )

        result = tracker.get_strategy_viability("mediocre_strat")
        # cost_ratio = 60/300 = 0.2 → break_even_sharpe = 0.2/0.8 = 0.25
        assert result["break_even_sharpe"] > 0
        assert result["break_even_sharpe"] < float("inf")

    def test_viability_unknown_strategy(self, tracker):
        """Unknown strategy returns default viable with 0 trades."""
        result = tracker.get_strategy_viability("unknown_strat")
        assert result["n_trades"] == 0
        assert result["sufficient_data"] is False
        assert result["viable"] is True


# =============================================================================
# TESTS — Period filtering
# =============================================================================

class TestPeriodFiltering:
    """Tests for period-based filtering."""

    def test_30d_filter_excludes_old(self, tracker, tmp_db):
        """30d filter excludes trades older than 30 days."""
        tracker.record_commission("R100", "strat", "SPY", "EQUITY", 0.50, 1000.0, 10.0)
        _insert_old_commission(tmp_db, "OLD100", "strat", days_ago=45, commission=5.0, pnl=10.0)

        report = tracker.get_cost_report(period="30d")
        assert report["n_trades"] == 1
        assert report["total_commission"] == 0.50

    def test_7d_filter(self, tracker, tmp_db):
        """7d filter works for short periods."""
        tracker.record_commission("R200", "strat", "SPY", "EQUITY", 0.50, 1000.0, 10.0)
        _insert_old_commission(tmp_db, "MID200", "strat", days_ago=10, commission=3.0, pnl=20.0)

        report = tracker.get_cost_report(period="7d")
        assert report["n_trades"] == 1


# =============================================================================
# TESTS — Multiple strategies
# =============================================================================

class TestMultipleStrategies:
    """Tests for multi-strategy scenarios."""

    def test_mixed_strategies(self, tracker):
        """Report correctly separates multiple strategies."""
        tracker.record_commission("M001", "alpha", "SPY", "EQUITY", 0.50, 10000.0, 100.0)
        tracker.record_commission("M002", "alpha", "QQQ", "EQUITY", 0.60, 12000.0, 80.0)
        tracker.record_commission("M003", "beta", "IWM", "EQUITY", 1.00, 5000.0, 20.0)
        tracker.record_commission("M004", "gamma", "TLT", "EQUITY", 0.20, 8000.0, 50.0)

        report = tracker.get_cost_report(period="30d")

        assert len(report["by_strategy"]) == 3
        assert report["by_strategy"]["alpha"]["n_trades"] == 2
        assert report["by_strategy"]["beta"]["n_trades"] == 1
        assert report["by_strategy"]["gamma"]["n_trades"] == 1

        # Total should be sum of all
        assert abs(report["total_commission"] - 2.30) < 0.01
        assert report["n_trades"] == 4


# =============================================================================
# TESTS — DB persistence
# =============================================================================

class TestPersistence:
    """Tests for database persistence."""

    def test_data_persists_across_instances(self, tmp_db):
        """Data persists when creating a new tracker instance."""
        t1 = CostTracker(db_path=tmp_db, alert_callback=None)
        t1.record_commission("P001", "strat", "SPY", "EQUITY", 0.50, 1000.0, 10.0)

        t2 = CostTracker(db_path=tmp_db, alert_callback=None)
        report = t2.get_cost_report(period="30d")
        assert report["n_trades"] == 1
        assert report["total_commission"] == 0.50
