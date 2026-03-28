"""
Tests for core.alerting_live — LiveAlertManager.

Covers:
  - Prefix formatting (LIVE / PAPER)
  - INFO alerts: trade opened, trade closed, daily report
  - WARNING alerts: slippage, margin, drawdown, signal skipped, strategy paused
  - CRITICAL alerts: kill switch, broker disconnected, reconciliation,
    drawdown critical, margin critical, worker crash
  - Backup channel dispatch for CRITICAL
  - Throttling behaviour
  - Alert history and stats
  - Unresolved critical tracking
"""

import time
import pytest
from unittest.mock import MagicMock

from core.alerting_live import LiveAlertManager, INFO, WARNING, CRITICAL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def send_mock():
    """Primary send function mock — always succeeds."""
    return MagicMock(return_value=True)


@pytest.fixture
def backup_mock():
    """Backup send function mock — always succeeds."""
    return MagicMock(return_value=True)


@pytest.fixture
def mgr(send_mock, backup_mock):
    """LiveAlertManager in LIVE mode with mocked send functions."""
    return LiveAlertManager(
        mode="LIVE",
        throttle_seconds=300,
        send_func=send_mock,
        backup_send_func=backup_mock,
    )


@pytest.fixture
def mgr_paper(send_mock, backup_mock):
    """LiveAlertManager in PAPER mode."""
    return LiveAlertManager(
        mode="PAPER",
        throttle_seconds=300,
        send_func=send_mock,
        backup_send_func=backup_mock,
    )


# ---------------------------------------------------------------------------
# Prefix formatting
# ---------------------------------------------------------------------------

class TestPrefix:
    def test_live_prefix(self, mgr, send_mock):
        """All LIVE mode messages contain [LIVE]."""
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        text_sent = send_mock.call_args[0][0]
        assert "[LIVE]" in text_sent

    def test_paper_prefix(self, mgr_paper, send_mock):
        """All PAPER mode messages contain [PAPER]."""
        mgr_paper.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        text_sent = send_mock.call_args[0][0]
        assert "[PAPER]" in text_sent


# ---------------------------------------------------------------------------
# INFO alerts
# ---------------------------------------------------------------------------

class TestInfoAlerts:
    def test_trade_opened_format(self, mgr, send_mock):
        mgr.trade_opened(
            "MOM_ETF", "QQQ", "LONG", 50, 385.20,
            stop_loss=375.0, take_profit=400.0, instrument_type="ETF",
        )
        text = send_mock.call_args[0][0]
        assert "Trade Opened" in text
        assert "MOM_ETF" in text
        assert "QQQ" in text
        assert "ETF" in text
        assert "SL:" in text
        assert "TP:" in text
        assert "INFO" in text

    def test_trade_opened_no_sl_tp(self, mgr, send_mock):
        mgr.trade_opened("ORB", "AAPL", "LONG", 20, 190.0)
        text = send_mock.call_args[0][0]
        assert "SL:" not in text
        assert "TP:" not in text

    def test_trade_closed_positive_pnl(self, mgr, send_mock):
        mgr.trade_closed("MOM_ETF", "QQQ", "LONG", 250.0, 1.5, "TP hit", "2h")
        text = send_mock.call_args[0][0]
        assert "Trade Closed" in text
        assert "+250.00" in text or "+$250.00" in text
        assert "TP hit" in text
        assert "2h" in text

    def test_trade_closed_negative_pnl(self, mgr, send_mock):
        mgr.trade_closed("ORB", "TSLA", "LONG", -120.0, -0.8, "SL hit")
        text = send_mock.call_args[0][0]
        assert "-120.00" in text or "-$120.00" in text

    def test_daily_report(self, mgr, send_mock):
        mgr.daily_report(
            trades_today=12, pnl_today=350.0, pnl_mtd=1200.0,
            positions_open=3, margin_used_pct=45.2, strategies_active=8,
        )
        text = send_mock.call_args[0][0]
        assert "Daily Report" in text
        assert "12" in text
        assert "45.2%" in text
        assert "8" in text


# ---------------------------------------------------------------------------
# WARNING alerts
# ---------------------------------------------------------------------------

class TestWarningAlerts:
    def test_slippage_warning(self, mgr, send_mock):
        mgr.slippage_warning("ORB", "AAPL", 15.0, 5.0)
        text = send_mock.call_args[0][0]
        assert "Slippage" in text
        assert "WARNING" in text
        assert "3.0x" in text

    def test_margin_warning(self, mgr, send_mock):
        mgr.margin_warning(0.72, threshold=0.70)
        text = send_mock.call_args[0][0]
        assert "Margin" in text
        assert "72.0%" in text
        assert "70%" in text

    def test_drawdown_warning(self, mgr, send_mock):
        mgr.drawdown_warning(1.3, 1300.0)
        text = send_mock.call_args[0][0]
        assert "Drawdown" in text
        assert "1.30%" in text
        assert "1,300.00" in text

    def test_signal_skipped(self, mgr, send_mock):
        mgr.signal_skipped("VWAP_MD", "TSLA", "max exposure reached")
        text = send_mock.call_args[0][0]
        assert "Signal Skipped" in text
        assert "VWAP_MD" in text
        assert "max exposure reached" in text

    def test_strategy_paused(self, mgr, send_mock):
        mgr.strategy_paused("ORB_5M", "kill switch 5d rolling")
        text = send_mock.call_args[0][0]
        assert "Strategy Paused" in text
        assert "ORB_5M" in text
        assert "kill switch" in text


# ---------------------------------------------------------------------------
# CRITICAL alerts
# ---------------------------------------------------------------------------

class TestCriticalAlerts:
    def test_kill_switch(self, mgr, send_mock):
        mgr.kill_switch_activated("5d rolling loss > -2%", 4, -2500.0)
        text = send_mock.call_args[0][0]
        assert "KILL SWITCH" in text
        assert "CRITICAL" in text
        assert "ALL TRADING HALTED" in text

    def test_broker_disconnected(self, mgr, send_mock):
        mgr.broker_disconnected("Alpaca", 30)
        text = send_mock.call_args[0][0]
        assert "Broker Disconnected" in text
        assert "Alpaca" in text
        assert "30s" in text

    def test_broker_disconnected_no_duration(self, mgr, send_mock):
        mgr.broker_disconnected("Alpaca")
        text = send_mock.call_args[0][0]
        assert "just now" in text

    def test_reconciliation_mismatch(self, mgr, send_mock):
        divs = ["SPY: model=100, broker=95", "QQQ: model=50, broker=50"]
        mgr.reconciliation_mismatch(divs)
        text = send_mock.call_args[0][0]
        assert "Reconciliation" in text
        assert "SPY" in text
        assert "Manual review" in text

    def test_drawdown_critical(self, mgr, send_mock):
        mgr.drawdown_critical(2.5, 2500.0)
        text = send_mock.call_args[0][0]
        assert "CIRCUIT BREAKER" in text
        assert "2.50%" in text
        assert "ALL NEW ORDERS BLOCKED" in text

    def test_margin_critical(self, mgr, send_mock):
        mgr.margin_critical(0.88)
        text = send_mock.call_args[0][0]
        assert "MARGIN CRITICAL" in text
        assert "88.0%" in text
        assert "NEW TRADES BLOCKED" in text

    def test_worker_crash(self, mgr, send_mock):
        mgr.worker_crash("ZeroDivisionError: division by zero")
        text = send_mock.call_args[0][0]
        assert "WORKER CRASH" in text
        assert "ZeroDivisionError" in text
        assert "manual restart" in text

    def test_critical_sends_backup(self, mgr, send_mock, backup_mock):
        """CRITICAL alerts must also call the backup send function."""
        mgr.kill_switch_activated("test", 0, 0.0)
        assert backup_mock.called
        backup_text = backup_mock.call_args[0][0]
        assert "KILL SWITCH" in backup_text

    def test_info_does_not_send_backup(self, mgr, send_mock, backup_mock):
        """INFO alerts must NOT call the backup send function."""
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        assert not backup_mock.called

    def test_warning_does_not_send_backup(self, mgr, send_mock, backup_mock):
        """WARNING alerts must NOT call the backup send function."""
        mgr.drawdown_warning(1.2, 1200.0)
        assert not backup_mock.called


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

class TestThrottling:
    def test_same_type_throttled(self, send_mock, backup_mock):
        """Same alert type within throttle window should be skipped."""
        mgr = LiveAlertManager(
            mode="LIVE", throttle_seconds=300,
            send_func=send_mock, backup_send_func=backup_mock,
        )
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        assert send_mock.call_count == 1
        # Second call within 300s — should be throttled
        result = mgr.trade_opened("MOM", "QQQ", "LONG", 5, 380.0)
        assert result is False
        assert send_mock.call_count == 1

    def test_different_type_not_throttled(self, mgr, send_mock):
        """Different alert types should not be throttled by each other."""
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        mgr.trade_closed("MOM", "SPY", "LONG", 100.0, 0.5, "TP hit")
        assert send_mock.call_count == 2

    def test_same_type_after_expiry(self, send_mock, backup_mock):
        """Same alert type after throttle window expires should be allowed."""
        mgr = LiveAlertManager(
            mode="LIVE", throttle_seconds=0,  # 0 = no throttle
            send_func=send_mock, backup_send_func=backup_mock,
        )
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        mgr.trade_opened("MOM", "QQQ", "LONG", 5, 380.0)
        assert send_mock.call_count == 2


# ---------------------------------------------------------------------------
# Alert history & stats
# ---------------------------------------------------------------------------

class TestAlertHistory:
    def test_history_recorded(self, mgr, send_mock):
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        mgr.drawdown_warning(1.5, 1500.0)
        history = mgr.get_alert_history()
        assert len(history) == 2
        assert history[0]["level"] == INFO
        assert history[1]["level"] == WARNING

    def test_history_filtered_by_level(self, mgr, send_mock):
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        mgr.drawdown_warning(1.5, 1500.0)
        warnings = mgr.get_alert_history(level=WARNING)
        assert len(warnings) == 1
        assert warnings[0]["type"] == "drawdown_warning"

    def test_history_limit(self, send_mock, backup_mock):
        mgr = LiveAlertManager(
            mode="LIVE", throttle_seconds=0,
            send_func=send_mock, backup_send_func=backup_mock,
        )
        for i in range(10):
            mgr.trade_opened(f"S{i}", "SPY", "LONG", 1, 100.0)
        assert len(mgr.get_alert_history(limit=5)) == 5

    def test_alert_stats(self, mgr, send_mock):
        mgr.trade_opened("MOM", "SPY", "LONG", 10, 450.0)
        mgr.drawdown_warning(1.5, 1500.0)
        mgr.kill_switch_activated("test", 0, 0.0)
        stats = mgr.get_alert_stats()
        assert stats[INFO] == 1
        assert stats[WARNING] == 1
        assert stats[CRITICAL] == 1
        assert stats["total"] == 3
        assert stats["by_type"]["trade_opened"] == 1


# ---------------------------------------------------------------------------
# Unresolved critical tracking
# ---------------------------------------------------------------------------

class TestUnresolvedCriticals:
    def test_critical_is_unresolved(self, mgr, send_mock):
        assert mgr.has_unresolved_critical() is False
        mgr.kill_switch_activated("test", 0, 0.0)
        assert mgr.has_unresolved_critical() is True

    def test_resolve_critical(self, mgr, send_mock):
        mgr.kill_switch_activated("test", 0, 0.0)
        mgr.resolve_critical("kill_switch")
        assert mgr.has_unresolved_critical() is False

    def test_resolve_nonexistent_no_error(self, mgr):
        """Resolving a non-existent critical should not raise."""
        mgr.resolve_critical("nonexistent")
        assert mgr.has_unresolved_critical() is False

    def test_multiple_criticals(self, mgr, send_mock):
        mgr.kill_switch_activated("test", 0, 0.0)
        mgr.worker_crash("crash")
        assert mgr.has_unresolved_critical() is True
        mgr.resolve_critical("kill_switch")
        # Still has worker_crash unresolved
        assert mgr.has_unresolved_critical() is True
        mgr.resolve_critical("worker_crash")
        assert mgr.has_unresolved_critical() is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_backup_func_critical_still_works(self, send_mock):
        """CRITICAL alert without backup_send_func should not raise."""
        mgr = LiveAlertManager(
            mode="LIVE", throttle_seconds=300,
            send_func=send_mock, backup_send_func=None,
        )
        result = mgr.kill_switch_activated("test", 0, 0.0)
        assert result is True

    def test_worker_crash_long_error_truncated(self, mgr, send_mock):
        """Long error messages should be truncated."""
        long_error = "X" * 1000
        mgr.worker_crash(long_error)
        text = send_mock.call_args[0][0]
        # 500 chars of X + potential formatting, should not have full 1000
        assert "X" * 500 in text
        assert "X" * 600 not in text

    def test_reconciliation_many_divergences_capped(self, mgr, send_mock):
        """Divergence list display capped at 10."""
        divs = [f"SYM{i}: model={i}, broker={i+1}" for i in range(20)]
        mgr.reconciliation_mismatch(divs)
        text = send_mock.call_args[0][0]
        assert "SYM9" in text
        assert "SYM10" not in text
