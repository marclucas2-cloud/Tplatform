"""
Tests for LiveKillSwitch — emergency position closure for live trading.

Covers:
  - Initial state (armed, not active)
  - Daily loss trigger
  - 5-day rolling trigger
  - Monthly loss trigger
  - Per-strategy trigger (default + MC calibrated)
  - Activate -> closes all positions
  - Activate -> cancels all orders
  - Activate -> sends alert
  - Activate -> persists state
  - Deactivate -> requires authorization
  - Double activation (idempotent)
  - Status report
  - History tracking
  - Paper continues when live killed
  - Threshold calculations with $10K capital
  - Capital zero edge case
  - State persistence and reload
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.kill_switch_live import LiveKillSwitch, DEFAULT_THRESHOLDS


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_broker():
    """Mock broker with positions and orders."""
    broker = MagicMock()
    broker.get_positions.return_value = [
        {"symbol": "AAPL", "qty": 10, "side": "long",
         "avg_entry": 180.0, "market_val": 1800.0, "unrealized_pl": -50.0},
        {"symbol": "NVDA", "qty": -5, "side": "short",
         "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 30.0},
    ]
    broker.close_position.return_value = {"status": "closed"}
    broker.cancel_all_orders.return_value = 3
    broker.close_all_positions.return_value = [{"orderId": "123"}]
    return broker


@pytest.fixture
def alert_log():
    """Capture alerts as a list of (message, level) tuples."""
    log = []

    def callback(message, level):
        log.append((message, level))

    return log, callback


@pytest.fixture
def state_file(tmp_path):
    """Temporary state file path."""
    return tmp_path / "kill_switch_state.json"


@pytest.fixture
def ks(mock_broker, alert_log, state_file):
    """Standard LiveKillSwitch instance for testing."""
    _, callback = alert_log
    return LiveKillSwitch(
        broker=mock_broker,
        alert_callback=callback,
        state_path=state_file,
    )


@pytest.fixture
def ks_with_mc(mock_broker, alert_log, state_file):
    """LiveKillSwitch with MC calibration overrides."""
    _, callback = alert_log
    return LiveKillSwitch(
        broker=mock_broker,
        alert_callback=callback,
        state_path=state_file,
        mc_overrides={
            "opex_gamma": -0.01,     # 1% threshold (tighter than default 2%)
            "vwap_micro": -0.025,    # 2.5% threshold (looser than default 2%)
        },
    )


# =============================================================================
# TEST 1: Initial state
# =============================================================================

class TestInitialState:
    def test_armed_by_default(self, ks):
        """Kill switch should be armed at startup."""
        assert ks.is_armed is True

    def test_not_active_by_default(self, ks):
        """Kill switch should NOT be active at startup."""
        assert ks.is_active is False

    def test_default_thresholds_loaded(self, ks):
        """Default thresholds should be loaded."""
        assert ks.thresholds["daily_loss_pct"] == 0.015
        assert ks.thresholds["trailing_5d_loss_pct"] == 0.03
        assert ks.thresholds["monthly_loss_pct"] == 0.05
        assert ks.thresholds["strategy_loss_pct"] == 0.02


# =============================================================================
# TEST 2: Daily loss trigger
# =============================================================================

class TestDailyLossTrigger:
    def test_daily_loss_triggers(self, ks):
        """Daily loss exceeding threshold should trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-200.0,
            capital=10_000.0,
        )
        assert result["triggered"] is True
        assert result["trigger_type"] == "DAILY_LOSS"
        assert "-1.50%" in result["reason"]

    def test_daily_loss_within_threshold(self, ks):
        """Daily loss within threshold should NOT trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-100.0,  # -1.0% < -1.5% threshold
            capital=10_000.0,
        )
        assert result["triggered"] is False

    def test_daily_gain_no_trigger(self, ks):
        """Positive P&L should never trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=500.0,
            capital=10_000.0,
        )
        assert result["triggered"] is False


# =============================================================================
# TEST 3: Rolling 5-day trigger
# =============================================================================

class TestRolling5dTrigger:
    def test_rolling_5d_triggers(self, ks):
        """Rolling 5-day loss exceeding threshold should trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,  # daily OK
            capital=10_000.0,
            rolling_5d_pnl=-400.0,  # -4% > -3% threshold
        )
        assert result["triggered"] is True
        assert result["trigger_type"] == "ROLLING_5D_LOSS"

    def test_rolling_5d_within_threshold(self, ks):
        """Rolling 5-day loss within threshold should NOT trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            rolling_5d_pnl=-200.0,  # -2% < -3% threshold
        )
        assert result["triggered"] is False


# =============================================================================
# TEST 4: Monthly loss trigger
# =============================================================================

class TestMonthlyLossTrigger:
    def test_monthly_loss_triggers(self, ks):
        """Monthly loss exceeding threshold should trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            monthly_pnl=-600.0,  # -6% > -5% threshold
        )
        assert result["triggered"] is True
        assert result["trigger_type"] == "MONTHLY_LOSS"

    def test_monthly_loss_within_threshold(self, ks):
        """Monthly loss within threshold should NOT trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            monthly_pnl=-400.0,  # -4% < -5% threshold
        )
        assert result["triggered"] is False


# =============================================================================
# TEST 5: Per-strategy trigger
# =============================================================================

class TestStrategyTrigger:
    def test_strategy_loss_triggers(self, ks):
        """Per-strategy loss exceeding default threshold should trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            strategy_pnls={"orb_v2": -250.0},  # -2.5% > -2% threshold
        )
        assert result["triggered"] is True
        assert result["trigger_type"] == "STRATEGY_LOSS"
        assert "orb_v2" in result["reason"]
        assert result["details"]["mc_calibrated"] is False

    def test_strategy_loss_within_threshold(self, ks):
        """Per-strategy loss within threshold should NOT trigger."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            strategy_pnls={"orb_v2": -150.0},  # -1.5% < -2% threshold
        )
        assert result["triggered"] is False

    def test_mc_calibrated_strategy_trigger(self, ks_with_mc):
        """MC-calibrated tighter threshold should trigger sooner."""
        result = ks_with_mc.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            strategy_pnls={"opex_gamma": -120.0},  # -1.2% > -1% MC threshold
        )
        assert result["triggered"] is True
        assert result["details"]["mc_calibrated"] is True

    def test_mc_calibrated_strategy_no_trigger(self, ks_with_mc):
        """MC-calibrated looser threshold should allow more loss."""
        result = ks_with_mc.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            strategy_pnls={"vwap_micro": -200.0},  # -2% < -2.5% MC threshold
        )
        assert result["triggered"] is False


# =============================================================================
# TEST 6: Activate closes all positions
# =============================================================================

class TestActivateClosesPositions:
    def test_activate_closes_all_positions(self, ks, mock_broker):
        """Activation should call close_position for every position."""
        result = ks.activate("Test trigger", "AUTOMATIC")

        assert result["success"] is True
        assert result["positions_closed"] == 2
        assert mock_broker.close_position.call_count == 2
        # Verify _authorized_by is KILL_SWITCH
        for c in mock_broker.close_position.call_args_list:
            assert c.kwargs.get("_authorized_by") == "KILL_SWITCH"

    def test_activate_reports_pnl_at_close(self, ks):
        """Activation should sum unrealized P&L at close."""
        result = ks.activate("Test", "AUTOMATIC")
        # -50.0 + 30.0 = -20.0
        assert result["pnl_at_close"] == pytest.approx(-20.0)


# =============================================================================
# TEST 7: Activate cancels all orders
# =============================================================================

class TestActivateCancelsOrders:
    def test_activate_cancels_all_orders(self, ks, mock_broker):
        """Activation should cancel all open orders."""
        result = ks.activate("Test", "AUTOMATIC")

        assert result["orders_cancelled"] == 3
        mock_broker.cancel_all_orders.assert_called_once_with(
            _authorized_by="KILL_SWITCH"
        )


# =============================================================================
# TEST 8: Activate sends alert
# =============================================================================

class TestActivateSendsAlert:
    def test_activate_sends_critical_alert(self, ks, alert_log):
        """Activation should send a critical-level alert."""
        log, _ = alert_log
        ks.activate("Daily loss exceeded", "DAILY_LOSS")

        assert len(log) == 1
        message, level = log[0]
        assert level == "critical"
        assert "KILL SWITCH ACTIVATED" in message
        assert "Daily loss exceeded" in message


# =============================================================================
# TEST 9: Activate persists state
# =============================================================================

class TestActivatePersistsState:
    def test_state_persisted_after_activation(self, ks, state_file):
        """State should be written to disk after activation."""
        ks.activate("Persistence test", "MANUAL")

        assert state_file.exists()
        with open(state_file) as f:
            state = json.load(f)
        assert state["active"] is True
        assert state["activation_reason"] == "Persistence test"
        assert state["activation_trigger"] == "MANUAL"

    def test_state_reloaded_on_new_instance(self, mock_broker, alert_log, state_file):
        """A new instance should reload active state from disk."""
        _, callback = alert_log
        ks1 = LiveKillSwitch(
            broker=mock_broker, alert_callback=callback, state_path=state_file
        )
        ks1.activate("Reload test", "AUTOMATIC")

        # Create new instance with same state file
        ks2 = LiveKillSwitch(
            broker=mock_broker, alert_callback=callback, state_path=state_file
        )
        assert ks2.is_active is True


# =============================================================================
# TEST 10: Deactivate requires authorization
# =============================================================================

class TestDeactivateRequiresAuth:
    def test_deactivate_records_who(self, ks):
        """Deactivation should record who authorized it."""
        ks.activate("Test", "AUTOMATIC")
        result = ks.deactivate(authorized_by="Marc via Telegram")

        assert result["success"] is True
        assert result["was_active"] is True
        assert result["authorized_by"] == "Marc via Telegram"
        assert result["downtime_minutes"] >= 0

    def test_deactivate_when_not_active(self, ks):
        """Deactivating when not active should be a no-op."""
        result = ks.deactivate(authorized_by="test")
        assert result["success"] is True
        assert result["was_active"] is False
        assert result["downtime_minutes"] == 0.0

    def test_deactivate_clears_state(self, ks):
        """After deactivation, kill switch is no longer active."""
        ks.activate("Test", "AUTOMATIC")
        assert ks.is_active is True

        ks.deactivate(authorized_by="test")
        assert ks.is_active is False


# =============================================================================
# TEST 11: Double activation is idempotent
# =============================================================================

class TestIdempotentActivation:
    def test_double_activation_is_idempotent(self, ks, mock_broker):
        """Second activation should not close positions again."""
        result1 = ks.activate("First trigger", "AUTOMATIC")
        assert result1["positions_closed"] == 2

        # Reset mock call counts
        mock_broker.close_position.reset_mock()
        mock_broker.cancel_all_orders.reset_mock()

        result2 = ks.activate("Second trigger", "MANUAL")
        assert result2["already_active"] is True
        assert result2["positions_closed"] == 0
        mock_broker.close_position.assert_not_called()
        mock_broker.cancel_all_orders.assert_not_called()


# =============================================================================
# TEST 12: Status report
# =============================================================================

class TestStatusReport:
    def test_status_when_inactive(self, ks):
        """Status report when kill switch is not active."""
        status = ks.get_status()
        assert status["is_active"] is False
        assert status["is_armed"] is True
        assert status["activated_at"] is None
        assert status["total_activations"] == 0

    def test_status_when_active(self, ks):
        """Status report when kill switch is active."""
        ks.activate("Status test", "DAILY_LOSS")
        status = ks.get_status()

        assert status["is_active"] is True
        assert status["activated_at"] is not None
        assert status["activation_reason"] == "Status test"
        assert status["activation_trigger"] == "DAILY_LOSS"
        assert status["total_activations"] == 1

    def test_status_includes_thresholds(self, ks):
        """Status should include threshold configuration."""
        status = ks.get_status()
        assert "thresholds" in status
        assert status["thresholds"]["daily_loss_pct"] == 0.015


# =============================================================================
# TEST 13: History tracking
# =============================================================================

class TestHistoryTracking:
    def test_history_empty_initially(self, ks):
        """History should be empty at startup."""
        assert ks.get_history() == []

    def test_history_records_activation(self, ks):
        """History should record each activation."""
        ks.activate("Trigger 1", "DAILY_LOSS")
        history = ks.get_history()

        assert len(history) == 1
        assert history[0]["action"] == "ACTIVATE"
        assert history[0]["reason"] == "Trigger 1"
        assert history[0]["trigger_type"] == "DAILY_LOSS"

    def test_history_records_deactivation(self, ks):
        """History should record deactivation too."""
        ks.activate("Test", "AUTOMATIC")
        ks.deactivate(authorized_by="Marc")
        history = ks.get_history()

        assert len(history) == 2
        assert history[0]["action"] == "ACTIVATE"
        assert history[1]["action"] == "DEACTIVATE"
        assert history[1]["authorized_by"] == "Marc"

    def test_history_persisted(self, mock_broker, alert_log, state_file):
        """History should survive instance restart."""
        _, callback = alert_log
        ks1 = LiveKillSwitch(
            broker=mock_broker, alert_callback=callback, state_path=state_file
        )
        ks1.activate("Persist test", "AUTOMATIC")
        ks1.deactivate(authorized_by="test")

        ks2 = LiveKillSwitch(
            broker=mock_broker, alert_callback=callback, state_path=state_file
        )
        history = ks2.get_history()
        assert len(history) == 2


# =============================================================================
# TEST 14: Paper trading continues when live killed
# =============================================================================

class TestPaperContinuesWhenLiveKilled:
    def test_kill_switch_does_not_affect_separate_paper_broker(self, tmp_path):
        """Kill switch on live broker should not touch paper broker.

        The architecture uses separate broker instances for paper/live.
        This test verifies the live kill switch only calls its own broker.
        """
        live_broker = MagicMock()
        live_broker.get_positions.return_value = [
            {"symbol": "SPY", "qty": 10, "side": "long",
             "unrealized_pl": -100.0},
        ]
        live_broker.cancel_all_orders.return_value = 1

        paper_broker = MagicMock()

        ks = LiveKillSwitch(
            broker=live_broker,
            state_path=tmp_path / "ks_test_paper.json",
        )
        ks.activate("Test isolation", "AUTOMATIC")

        # Live broker should have been called
        live_broker.close_position.assert_called()
        live_broker.cancel_all_orders.assert_called()

        # Paper broker should NOT have been called
        paper_broker.close_position.assert_not_called()
        paper_broker.cancel_all_orders.assert_not_called()


# =============================================================================
# TEST 15: Threshold calculations with $10K capital
# =============================================================================

class TestThresholdsWith10KCapital:
    def test_10k_daily_threshold(self, ks):
        """With $10K capital, -1.5% daily = -$150 loss triggers."""
        # -$149 should NOT trigger
        result = ks.check_automatic_triggers(daily_pnl=-149.0, capital=10_000.0)
        assert result["triggered"] is False

        # -$151 should trigger
        result = ks.check_automatic_triggers(daily_pnl=-151.0, capital=10_000.0)
        assert result["triggered"] is True

    def test_10k_rolling_threshold(self, ks):
        """With $10K capital, -3% rolling = -$300 loss triggers."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            rolling_5d_pnl=-299.0,
        )
        assert result["triggered"] is False

        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            rolling_5d_pnl=-301.0,
        )
        assert result["triggered"] is True

    def test_10k_monthly_threshold(self, ks):
        """With $10K capital, -5% monthly = -$500 loss triggers."""
        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            monthly_pnl=-499.0,
        )
        assert result["triggered"] is False

        result = ks.check_automatic_triggers(
            daily_pnl=-50.0,
            capital=10_000.0,
            monthly_pnl=-501.0,
        )
        assert result["triggered"] is True


# =============================================================================
# TEST 16: Capital zero edge case
# =============================================================================

class TestCapitalZeroEdgeCase:
    def test_zero_capital_triggers_immediately(self, ks):
        """Zero capital should trigger immediately (division safety)."""
        result = ks.check_automatic_triggers(daily_pnl=0.0, capital=0.0)
        assert result["triggered"] is True
        assert result["trigger_type"] == "CAPITAL_ZERO"

    def test_negative_capital_triggers(self, ks):
        """Negative capital should also trigger."""
        result = ks.check_automatic_triggers(daily_pnl=-100.0, capital=-5000.0)
        assert result["triggered"] is True
        assert result["trigger_type"] == "CAPITAL_ZERO"


# =============================================================================
# TEST 17: No broker configured
# =============================================================================

class TestNoBrokerConfigured:
    def test_activate_without_broker(self, state_file):
        """Activation without broker should not crash."""
        ks = LiveKillSwitch(broker=None, state_path=state_file)
        result = ks.activate("No broker test", "MANUAL")

        assert result["success"] is True
        assert result["positions_closed"] == 0
        assert result["orders_cancelled"] == 0
        assert ks.is_active is True


# =============================================================================
# TEST 18: Trigger priority (daily > rolling > monthly > strategy)
# =============================================================================

class TestTriggerPriority:
    def test_daily_checked_first(self, ks):
        """When multiple triggers are breached, daily should fire first."""
        result = ks.check_automatic_triggers(
            daily_pnl=-200.0,
            capital=10_000.0,
            rolling_5d_pnl=-400.0,
            monthly_pnl=-600.0,
            strategy_pnls={"test": -300.0},
        )
        assert result["trigger_type"] == "DAILY_LOSS"
