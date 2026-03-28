"""Tests for live performance guard — auto-disable underperforming strategies."""
import pytest
from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.live_performance_guard import LivePerformanceGuard, CONTINUE, DISABLE, ALERT


@pytest.fixture
def guard():
    return LivePerformanceGuard()


class TestNotEnoughTrades:
    def test_below_minimum(self, guard):
        trades = [{"pnl": 10}] * 5
        action, reason = guard.evaluate("test_strat", trades)
        assert action == CONTINUE
        assert "too early" in reason


class TestSharpeDisable:
    def test_negative_sharpe_disables(self, guard):
        trades = [{"pnl": -10}] * 8 + [{"pnl": 5}] * 2 + [{"pnl": -15}] * 2
        action, reason = guard.evaluate("bad_strat", trades)
        assert action == DISABLE
        assert "Sharpe" in reason
        assert guard.is_disabled("bad_strat")

    def test_positive_sharpe_continues(self, guard):
        trades = [{"pnl": 20}] * 7 + [{"pnl": -5}] * 3 + [{"pnl": 15}] * 2
        action, reason = guard.evaluate("good_strat", trades)
        assert action == CONTINUE


class TestWinRateDisable:
    def test_low_win_rate_disables(self, guard):
        trades = [{"pnl": -10}] * 8 + [{"pnl": 100}] * 2  # 20% WR
        # Sharpe might be positive due to one big win, but WR < 30%
        action, reason = guard.evaluate("low_wr", trades)
        # Either Sharpe or WR will trigger
        assert action == DISABLE


class TestSlippageDisable:
    def test_high_slippage_disables(self, guard):
        trades = [{"pnl": 10, "slippage_bps": 30}] * 10  # 30 bps avg
        action, reason = guard.evaluate("slip_strat", trades, backtest_slippage_bps=5)
        assert action == DISABLE
        assert "Slippage" in reason


class TestConsecutiveLosses:
    def test_5_consecutive_losses_alerts(self, guard):
        trades = [{"pnl": 20}] * 5 + [{"pnl": -5}] * 5 + [{"pnl": 10}] * 2
        action, reason = guard.evaluate("losing_strat", trades)
        assert action == ALERT
        assert "consecutive" in reason


class TestReactivation:
    def test_reactivate_disabled(self, guard):
        trades = [{"pnl": -10}] * 10 + [{"pnl": -5}] * 2
        guard.evaluate("bad", trades)
        assert guard.is_disabled("bad")
        guard.reactivate("bad", "Marc")
        assert not guard.is_disabled("bad")


class TestAlertCallback:
    def test_disable_fires_alert(self):
        alerts = []
        guard = LivePerformanceGuard(alert_callback=lambda m, l: alerts.append((m, l)))
        trades = [{"pnl": -10}] * 12
        guard.evaluate("dying_strat", trades)
        assert len(alerts) >= 1
        assert alerts[0][1] == "critical"
