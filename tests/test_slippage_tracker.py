"""
Tests unitaires du SlippageTracker.

Verifie :
  - Enregistrement de fill et calcul du slippage
  - Slippage adverse vs favorable
  - Summary par strategy/instrument_type/order_type
  - Alerte quand > 2x backtest (warning)
  - Alerte quand > 3x backtest (critical)
  - Slippage FX (pip-based)
  - Slippage Futures (point-based)
  - Filtrage par periode (7d, 30d)
  - Gestion donnees vides
  - Rapport d'amelioration (market vs limit)
  - Worst trades dans le summary
  - Ratio real vs backtest global
"""
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.slippage_tracker import SlippageTracker

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite db path."""
    return tmp_path / "test_execution_metrics.db"


@pytest.fixture
def tracker(tmp_db):
    """SlippageTracker with no alert callback and temp db."""
    return SlippageTracker(db_path=tmp_db, alert_callback=None)


@pytest.fixture
def mock_alert():
    """Mock alert callback."""
    return MagicMock()


@pytest.fixture
def tracker_with_alert(tmp_db, mock_alert):
    """SlippageTracker with mock alert callback."""
    return SlippageTracker(db_path=tmp_db, alert_callback=mock_alert)


def _insert_old_trade(db_path, trade_id, strategy, days_ago, slippage_bps=1.0):
    """Insert a trade with a timestamp in the past for period filtering tests."""
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO slippage_log
               (trade_id, timestamp, strategy, instrument, instrument_type,
                side, order_type, requested_price, filled_price,
                slippage_bps, backtest_slippage_bps, ratio_real_vs_backtest,
                market_spread_bps, volume_at_fill)
               VALUES (?, ?, ?, 'SPY', 'EQUITY', 'BUY', 'MARKET',
                       100.0, 100.01, ?, 2.0, ?, NULL, NULL)""",
            (trade_id, ts, strategy, slippage_bps, slippage_bps / 2.0),
        )
        conn.commit()


# =============================================================================
# TESTS — Basic record and slippage calculation
# =============================================================================

class TestRecordFill:
    """Tests for record_fill method."""

    def test_record_fill_basic(self, tracker):
        """Record a fill and verify slippage calculation."""
        result = tracker.record_fill(
            trade_id="T001",
            strategy="momentum",
            instrument="SPY",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=100.00,
            filled_price=100.02,
        )
        # slippage = (100.02 - 100.00) / 100.00 * 10000 = 2.0 bps
        assert result["slippage_bps"] == 2.0
        assert result["direction"] == "adverse"
        assert result["trade_id"] == "T001"

    def test_adverse_slippage_buy(self, tracker):
        """BUY: filled > requested = adverse (you paid more)."""
        result = tracker.record_fill(
            trade_id="T002",
            strategy="momentum",
            instrument="AAPL",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=150.00,
            filled_price=150.03,
        )
        assert result["slippage_bps"] == 2.0
        assert result["direction"] == "adverse"

    def test_favorable_slippage_buy(self, tracker):
        """BUY: filled < requested = favorable (you paid less)."""
        result = tracker.record_fill(
            trade_id="T003",
            strategy="momentum",
            instrument="AAPL",
            instrument_type="EQUITY",
            side="BUY",
            order_type="LIMIT",
            requested_price=150.00,
            filled_price=149.97,
        )
        assert result["slippage_bps"] == -2.0
        assert result["direction"] == "favorable"

    def test_adverse_slippage_sell(self, tracker):
        """SELL: filled < requested = adverse (you got less)."""
        result = tracker.record_fill(
            trade_id="T004",
            strategy="pairs",
            instrument="MU",
            instrument_type="EQUITY",
            side="SELL",
            order_type="MARKET",
            requested_price=80.00,
            filled_price=79.984,
        )
        # slippage = (80.00 - 79.984) / 80.00 * 10000 = 2.0 bps
        assert result["slippage_bps"] == 2.0
        assert result["direction"] == "adverse"

    def test_favorable_slippage_sell(self, tracker):
        """SELL: filled > requested = favorable (you got more)."""
        result = tracker.record_fill(
            trade_id="T005",
            strategy="pairs",
            instrument="AMAT",
            instrument_type="EQUITY",
            side="SELL",
            order_type="LIMIT",
            requested_price=120.00,
            filled_price=120.024,
        )
        assert result["slippage_bps"] == -2.0
        assert result["direction"] == "favorable"

    def test_zero_slippage(self, tracker):
        """Perfect fill at requested price."""
        result = tracker.record_fill(
            trade_id="T006",
            strategy="momentum",
            instrument="QQQ",
            instrument_type="EQUITY",
            side="BUY",
            order_type="LIMIT",
            requested_price=350.00,
            filled_price=350.00,
        )
        assert result["slippage_bps"] == 0.0

    def test_invalid_requested_price(self, tracker):
        """Should raise ValueError for non-positive requested_price."""
        with pytest.raises(ValueError, match="requested_price must be > 0"):
            tracker.record_fill(
                trade_id="T_ERR",
                strategy="test",
                instrument="BAD",
                instrument_type="EQUITY",
                side="BUY",
                order_type="MARKET",
                requested_price=0,
                filled_price=10.0,
            )

    def test_record_with_optional_fields(self, tracker):
        """Record fill with market_spread_bps and volume_at_fill."""
        result = tracker.record_fill(
            trade_id="T007",
            strategy="vwap",
            instrument="TSLA",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=200.00,
            filled_price=200.04,
            backtest_slippage_bps=2.0,
            market_spread_bps=1.5,
            volume_at_fill=50000,
        )
        assert result["slippage_bps"] == 2.0
        assert result["ratio_real_vs_backtest"] == 1.0


# =============================================================================
# TESTS — FX and Futures instrument types
# =============================================================================

class TestInstrumentTypes:
    """Tests for different instrument types."""

    def test_fx_slippage(self, tracker):
        """FX slippage calculation (pip-based, same formula)."""
        result = tracker.record_fill(
            trade_id="FX001",
            strategy="fx_carry",
            instrument="EUR/USD",
            instrument_type="FX",
            side="BUY",
            order_type="MARKET",
            requested_price=1.0850,
            filled_price=1.0851,
        )
        # slippage ~ 0.92 bps
        assert result["slippage_bps"] > 0
        assert result["direction"] == "adverse"

    def test_futures_slippage(self, tracker):
        """Futures slippage calculation (point-based, same formula)."""
        result = tracker.record_fill(
            trade_id="FUT001",
            strategy="futures_momentum",
            instrument="ES",
            instrument_type="FUTURES",
            side="BUY",
            order_type="STOP",
            requested_price=5200.00,
            filled_price=5200.50,
        )
        # slippage = 0.50 / 5200 * 10000 ~ 0.96 bps
        assert result["slippage_bps"] > 0
        assert result["direction"] == "adverse"


# =============================================================================
# TESTS — Alerts
# =============================================================================

class TestAlerts:
    """Tests for alert triggering."""

    def test_alert_warning_2x(self, tracker_with_alert, mock_alert):
        """Alert WARNING when slippage > 2x backtest."""
        # 2x backtest = 4.0 bps adverse on a 2.0 bps assumption
        tracker_with_alert.record_fill(
            trade_id="A001",
            strategy="momentum",
            instrument="SPY",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=100.00,
            filled_price=100.05,  # 5 bps > 2x of 2 bps
            backtest_slippage_bps=2.0,
        )
        mock_alert.assert_called_once()
        call_args = mock_alert.call_args
        assert call_args[1]["level"] == "warning" or call_args[0][1] == "warning"

    def test_alert_critical_3x(self, tracker_with_alert, mock_alert):
        """Alert CRITICAL when slippage > 3x backtest."""
        # 3x backtest = 6.0 bps on a 2.0 bps assumption
        tracker_with_alert.record_fill(
            trade_id="A002",
            strategy="momentum",
            instrument="SPY",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=100.00,
            filled_price=100.10,  # 10 bps >> 3x of 2 bps
            backtest_slippage_bps=2.0,
        )
        mock_alert.assert_called_once()
        call_args = mock_alert.call_args
        assert call_args[1]["level"] == "critical" or call_args[0][1] == "critical"

    def test_no_alert_within_threshold(self, tracker_with_alert, mock_alert):
        """No alert when slippage < 2x backtest."""
        tracker_with_alert.record_fill(
            trade_id="A003",
            strategy="momentum",
            instrument="SPY",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=100.00,
            filled_price=100.01,  # 1.0 bps < 2x of 2 bps
            backtest_slippage_bps=2.0,
        )
        mock_alert.assert_not_called()

    def test_check_alerts_strategy_level(self, tracker_with_alert, mock_alert):
        """check_alerts detects strategy-level 7d average exceeding threshold."""
        # Record multiple trades averaging > 2x
        for i in range(5):
            tracker_with_alert.record_fill(
                trade_id=f"CA{i:03d}",
                strategy="bad_strat",
                instrument="SPY",
                instrument_type="EQUITY",
                side="BUY",
                order_type="MARKET",
                requested_price=100.00,
                filled_price=100.06,  # 6 bps each
                backtest_slippage_bps=2.0,
            )
        mock_alert.reset_mock()

        alerts = tracker_with_alert.check_alerts()
        assert len(alerts) >= 1
        assert alerts[0]["strategy"] == "bad_strat"
        assert alerts[0]["level"] in ("warning", "critical")


# =============================================================================
# TESTS — Summary
# =============================================================================

class TestSummary:
    """Tests for get_summary method."""

    def test_summary_by_strategy(self, tracker):
        """Summary aggregates correctly by strategy."""
        tracker.record_fill("S001", "strat_a", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)
        tracker.record_fill("S002", "strat_a", "QQQ", "EQUITY", "BUY", "MARKET", 100.0, 100.04)
        tracker.record_fill("S003", "strat_b", "IWM", "EQUITY", "SELL", "LIMIT", 100.0, 99.99)

        summary = tracker.get_summary(period="7d")

        assert "strat_a" in summary["by_strategy"]
        assert "strat_b" in summary["by_strategy"]
        # strat_a avg: (2.0 + 4.0) / 2 = 3.0 bps
        assert abs(summary["by_strategy"]["strat_a"] - 3.0) < 0.01

    def test_summary_by_instrument_type(self, tracker):
        """Summary aggregates by instrument_type."""
        tracker.record_fill("S010", "strat_a", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)
        tracker.record_fill("S011", "strat_b", "EUR/USD", "FX", "BUY", "MARKET", 1.085, 1.08505)

        summary = tracker.get_summary(period="7d")
        assert "EQUITY" in summary["by_instrument_type"]
        assert "FX" in summary["by_instrument_type"]

    def test_summary_by_order_type(self, tracker):
        """Summary aggregates by order_type."""
        tracker.record_fill("S020", "s", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)
        tracker.record_fill("S021", "s", "SPY", "EQUITY", "BUY", "LIMIT", 100.0, 100.01)

        summary = tracker.get_summary(period="7d")
        assert "MARKET" in summary["by_order_type"]
        assert "LIMIT" in summary["by_order_type"]

    def test_summary_worst_trades(self, tracker):
        """Summary returns top 5 worst trades."""
        for i in range(10):
            filled = 100.0 + (i * 0.01)
            tracker.record_fill(f"W{i:03d}", "s", "SPY", "EQUITY", "BUY", "MARKET", 100.0, filled)

        summary = tracker.get_summary(period="7d")
        assert len(summary["worst_trades"]) == 5
        # Worst should be the highest slippage
        assert summary["worst_trades"][0]["slippage_bps"] >= summary["worst_trades"][4]["slippage_bps"]

    def test_summary_total_cost(self, tracker):
        """Summary calculates total cost from adverse slippage."""
        # 2 bps adverse on $100 = $0.02
        tracker.record_fill("C001", "s", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)

        summary = tracker.get_summary(period="7d")
        assert summary["total_cost_from_slippage"] > 0

    def test_summary_with_strategy_filter(self, tracker):
        """Summary filters by strategy."""
        tracker.record_fill("F001", "alpha", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)
        tracker.record_fill("F002", "beta", "QQQ", "EQUITY", "BUY", "MARKET", 100.0, 100.04)

        summary = tracker.get_summary(period="7d", strategy="alpha")
        assert "alpha" in summary["by_strategy"]
        assert "beta" not in summary["by_strategy"]

    def test_summary_empty_data(self, tracker):
        """Summary handles empty data gracefully."""
        summary = tracker.get_summary(period="7d")
        assert summary["by_strategy"] == {}
        assert summary["by_instrument_type"] == {}
        assert summary["by_order_type"] == {}
        assert summary["ratio_real_vs_backtest"] == 0.0
        assert summary["worst_trades"] == []
        assert summary["total_cost_from_slippage"] == 0.0


# =============================================================================
# TESTS — Period filtering
# =============================================================================

class TestPeriodFiltering:
    """Tests for period-based filtering."""

    def test_7d_filter_excludes_old_trades(self, tracker, tmp_db):
        """7d filter excludes trades older than 7 days."""
        # Recent trade
        tracker.record_fill("R001", "strat", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)

        # Old trade (10 days ago)
        _insert_old_trade(tmp_db, "OLD001", "strat", days_ago=10, slippage_bps=5.0)

        summary = tracker.get_summary(period="7d")
        # Should only include the recent trade
        assert len(summary["worst_trades"]) == 1
        assert summary["worst_trades"][0]["trade_id"] == "R001"

    def test_30d_filter_includes_recent(self, tracker, tmp_db):
        """30d filter includes trades from the last 30 days."""
        tracker.record_fill("R002", "strat", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)
        _insert_old_trade(tmp_db, "MID001", "strat", days_ago=15, slippage_bps=3.0)
        _insert_old_trade(tmp_db, "OLD002", "strat", days_ago=45, slippage_bps=5.0)

        summary = tracker.get_summary(period="30d")
        trade_ids = {t["trade_id"] for t in summary["worst_trades"]}
        assert "R002" in trade_ids
        assert "MID001" in trade_ids
        assert "OLD002" not in trade_ids


# =============================================================================
# TESTS — Improvement report
# =============================================================================

class TestImprovementReport:
    """Tests for get_improvement_report method."""

    def test_improvement_report_market_vs_limit(self, tracker):
        """Report compares market vs limit order slippage."""
        # Market orders: higher slippage
        for i in range(5):
            tracker.record_fill(f"M{i}", "s", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.03)
        # Limit orders: lower slippage
        for i in range(5):
            tracker.record_fill(f"L{i}", "s", "SPY", "EQUITY", "BUY", "LIMIT", 100.0, 100.01)

        report = tracker.get_improvement_report()
        assert "MARKET" in report["by_order_type"]
        assert "LIMIT" in report["by_order_type"]
        assert report["market_vs_limit_diff_bps"] > 0

    def test_improvement_report_insufficient_data(self, tracker):
        """Report handles missing order types gracefully."""
        tracker.record_fill("X001", "s", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)

        report = tracker.get_improvement_report()
        assert len(report["recommendations"]) >= 1
        assert "Insufficient" in report["recommendations"][0]


# =============================================================================
# TESTS — DB persistence
# =============================================================================

class TestPersistence:
    """Tests for database persistence."""

    def test_data_persists_across_instances(self, tmp_db):
        """Data persists when creating a new tracker instance."""
        t1 = SlippageTracker(db_path=tmp_db, alert_callback=None)
        t1.record_fill("P001", "strat", "SPY", "EQUITY", "BUY", "MARKET", 100.0, 100.02)

        t2 = SlippageTracker(db_path=tmp_db, alert_callback=None)
        summary = t2.get_summary(period="7d")
        assert len(summary["worst_trades"]) == 1
