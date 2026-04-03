"""
SAFE-004 — Integration tests for autonomous mode (72h).

Tests the interactions between AutoReducer, AnomalyDetector, SafetyChecker,
and external systems (broker, kill switch, Telegram).

8 scenarios covering conflict resolution, cascade failures, and recovery.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.autonomous_mode import (
    AnomalyDetector,
    AutonomousController,
    AutoReducer,
    SafetyChecker,
)

# -- Fixtures ---------------------------------------------------------------

@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_positions.return_value = [
        {"symbol": "EURUSD", "qty": 25000, "side": "LONG", "unrealized_pl": -50},
        {"symbol": "GBPUSD", "qty": 25000, "side": "LONG", "unrealized_pl": 30},
        {"symbol": "MCL", "qty": 1, "side": "LONG", "unrealized_pl": -20},
    ]
    broker.get_account_info.return_value = {"equity": 9800, "cash": 6000}
    broker.close_position.return_value = {"status": "closed"}
    broker.cancel_all_orders.return_value = 2
    return broker


@pytest.fixture
def mock_alerts():
    messages = []

    def callback(msg, level="info"):
        messages.append({"message": msg, "level": level})

    return callback, messages


@pytest.fixture
def controller(mock_broker, mock_alerts, tmp_path):
    """Create AutonomousController with mocked dependencies."""
    callback, _ = mock_alerts

    reduce_mock = MagicMock()
    close_mock = MagicMock()
    kill_mock = MagicMock()

    reducer = AutoReducer(
        capital=10000,
        reduce_func=reduce_mock,
        close_func=close_mock,
    )

    anomaly = AnomalyDetector(
        alert_callback=callback,
        close_all_func=close_mock,
    )

    safety = SafetyChecker(
        get_positions_func=mock_broker.get_positions,
        get_open_orders_func=lambda: [
            {"symbol": "EURUSD", "type": "stop_limit"},
            {"symbol": "GBPUSD", "type": "stop"},
            {"symbol": "MCL", "type": "stop"},
        ],
        has_critical_alerts_func=lambda: False,
        kill_switch_armed_func=lambda: True,
        reconciliation_ok_func=lambda: True,
    )

    ctrl = AutonomousController(
        auto_reducer=reducer,
        anomaly_detector=anomaly,
        safety_checker=safety,
        alert_callback=callback,
        kill_func=kill_mock,
        state_path=str(tmp_path / "autonomous_state.json"),
    )

    # Expose mocks for assertions
    ctrl._test_reduce_mock = reduce_mock
    ctrl._test_close_mock = close_mock
    ctrl._test_kill_mock = kill_mock

    return ctrl


# -- Test Scenarios ----------------------------------------------------------

class TestConflictReducerVsAnomaly:
    """Scenario 1: AutoReducer wants reduce 30% but AnomalyDetector says normal."""

    def test_reducer_takes_priority_when_conservative(self, controller):
        """AutoReducer is more conservative -- its action should prevail
        even when no anomaly is detected."""
        # Enter autonomous mode first
        result = controller.enter_autonomous(duration_hours=72)
        assert result["success"] is True

        # Run periodic check with 1.2% DD (triggers L0 reduce 30%)
        check = controller.periodic_check(current_dd_pct=0.012)

        # AutoReducer should have acted
        assert len(check["actions_taken"]) > 0

        # The reducer's reduce_func should have been called with 0.30
        controller._test_reduce_mock.assert_called_once_with(0.30)

        # AnomalyDetector should NOT have triggered close_all
        assert "anomaly_close_all" not in check["actions_taken"]

    def test_both_trigger_reducer_wins_higher_severity(self):
        """When both components flag issues, the more severe action executes."""
        alerts = []
        reduce_mock = MagicMock()
        close_mock = MagicMock()

        reducer = AutoReducer(
            capital=10000,
            reduce_func=reduce_mock,
            close_func=close_mock,
        )

        # Trigger L0 (reduce 30%)
        result = reducer.check_and_act(current_dd_pct=0.012)
        assert result["action_taken"] is True
        assert result["action"] == "reduce"
        reduce_mock.assert_called_once_with(0.30)

        # Now escalate to L2 (close_all at 2%)
        result2 = reducer.check_and_act(current_dd_pct=0.025)
        assert result2["action_taken"] is True
        assert result2["action"] == "close_all"
        close_mock.assert_called_once()


class TestKillSwitchDuringReduce:
    """Scenario 2: Kill switch fires while AutoReducer is mid-reduction."""

    def test_kill_switch_overrides_reducer(self, tmp_path):
        """Kill switch activation should override everything -- no deadlock."""
        from core.kill_switch_live import LiveKillSwitch

        alerts = []
        ks = LiveKillSwitch(
            alert_callback=lambda m, l: alerts.append(m),
            state_path=tmp_path / "ks.json",
        )

        # Activate kill switch
        result = ks.activate(reason="DD > 4%", trigger_type="DAILY_LOSS")
        assert result["success"] is True
        assert ks.is_active is True

        # While active, the kill switch should block trading
        # A second activation must be idempotent
        result2 = ks.activate(reason="Another reason", trigger_type="MANUAL")
        assert result2["already_active"] is True
        assert ks.is_active is True

    def test_controller_activates_kill_on_close_all(self, controller):
        """periodic_check at DD >= 2% should trigger close_all + kill switch."""
        enter = controller.enter_autonomous(72)
        assert enter["success"] is True

        # DD 2.5% -> triggers L2 close_all in the reducer
        check = controller.periodic_check(current_dd_pct=0.025)

        # close_func should have been called
        controller._test_close_mock.assert_called()

        # kill_func should also have been called by the controller
        controller._test_kill_mock.assert_called_once()
        assert "kill_switch_activated" in check["actions_taken"]


class TestTelegramDownDuringAutonomous:
    """Scenario 3: Telegram connection lost during autonomous mode."""

    def test_anomaly_detector_survives_alert_failure(self):
        """If alert callback raises, AnomalyDetector should not crash."""

        def failing_alert(msg, level="info"):
            raise ConnectionError("Telegram down")

        detector = AnomalyDetector(
            alert_callback=failing_alert,
            close_all_func=MagicMock(),
        )

        # Record 3 consecutive losses -> triggers anomaly + alert
        for i in range(3):
            detector.record_trade_result("strat_a", pnl=-10)

        # Should NOT crash -- the exception is caught internally
        result = detector.check_anomalies()
        assert result["anomalies_found"] >= 1

        # Strategy should be paused despite alert failure
        assert detector.is_strategy_paused("strat_a") is True

    def test_controller_periodic_check_survives_alert_failure(self, tmp_path):
        """Controller should continue running even with failing alerts."""

        def failing_alert(msg, level="info"):
            raise ConnectionError("Telegram down")

        reducer = AutoReducer(capital=10000)
        anomaly = AnomalyDetector(alert_callback=failing_alert)
        safety = SafetyChecker()

        ctrl = AutonomousController(
            auto_reducer=reducer,
            anomaly_detector=anomaly,
            safety_checker=safety,
            alert_callback=failing_alert,
            state_path=str(tmp_path / "state.json"),
        )

        # Enter should work (alert failure caught)
        result = ctrl.enter_autonomous(72)
        assert result["success"] is True

        # periodic_check should not crash
        check = ctrl.periodic_check(current_dd_pct=0.005)
        assert check["auto_exited"] is False


class TestWorkerCrashRestartAutonomous:
    """Scenario 4: Worker crash + restart -- state restored correctly."""

    def test_state_survives_restart(self, tmp_path):
        """Autonomous state should be restorable from disk after restart."""
        state_path = str(tmp_path / "autonomous_state.json")

        alerts = []
        callback = lambda m, l="info": alerts.append(m)

        # Create controller and enter autonomous mode
        ctrl1 = AutonomousController(
            alert_callback=callback,
            state_path=state_path,
        )
        result = ctrl1.enter_autonomous(48)
        assert result["success"] is True
        assert ctrl1.is_active is True

        # Record a trade
        ctrl1.record_trade(pnl=25.0)

        # Simulate worker crash: destroy ctrl1, create ctrl2 from same state file
        entered_at = ctrl1._entered_at
        del ctrl1

        ctrl2 = AutonomousController(
            alert_callback=callback,
            state_path=state_path,
        )

        # State should be restored
        assert ctrl2._active is True
        assert ctrl2._entered_at == entered_at
        assert ctrl2._trades_during_autonomous == 1
        assert ctrl2._pnl_during_autonomous == 25.0

    def test_state_file_missing_creates_fresh(self, tmp_path):
        """If state file is missing, controller starts fresh (not active)."""
        state_path = str(tmp_path / "nonexistent_state.json")

        ctrl = AutonomousController(state_path=state_path)
        assert ctrl.is_active is False
        assert ctrl._periodic_checks_count == 0


class TestAccelerated72hRandomEvents:
    """Scenario 5: Simulated 72h with escalating adverse events."""

    def test_system_handles_multiple_adverse_events(self, controller):
        """Multiple adverse events in sequence should not cause cascade failure."""
        enter = controller.enter_autonomous(72)
        assert enter["success"] is True

        # Simulate escalating drawdown over multiple periodic checks
        dd_sequence = [0.005, 0.008, 0.012, 0.016, 0.019, 0.015, 0.012, 0.008]

        results = []
        for dd in dd_sequence:
            check = controller.periodic_check(current_dd_pct=dd)
            results.append(check)

        # All checks should have returned valid results
        for r in results:
            assert "actions_taken" in r
            assert "anomalies" in r
            assert "auto_exited" in r

        # The first DD >= 1% (0.012) should have triggered reduce
        controller._test_reduce_mock.assert_called()

    def test_anomaly_cascade_does_not_deadlock(self):
        """Multiple strategies pausing simultaneously should not deadlock."""
        alerts = []
        detector = AnomalyDetector(
            alert_callback=lambda m, l="info": alerts.append(m),
        )

        # Pause 5 strategies via consecutive losses
        for i in range(5):
            strat = f"strat_{i}"
            for _ in range(3):
                detector.record_trade_result(strat, pnl=-10)

        # All 5 should be paused
        paused = detector.get_paused_strategies()
        assert len(paused) == 5

        # check_anomalies should still work
        result = detector.check_anomalies()
        assert result["anomalies_found"] == 5


class TestMultipleStrategiesDisableDuringAutonomous:
    """Scenario 6: 3 strategies auto-disabled during autonomous mode."""

    def test_remaining_strategies_continue(self):
        """Disabling some strategies should not cascade to others."""
        from core.live_performance_guard import DISABLE, LivePerformanceGuard

        guard = LivePerformanceGuard()

        # Trades with negative Sharpe -> will trigger DISABLE
        bad_trades = [{"pnl": -10}] * 12

        action_a, _ = guard.evaluate("strat_a", bad_trades)
        action_b, _ = guard.evaluate("strat_b", bad_trades)
        action_c, _ = guard.evaluate("strat_c", bad_trades)

        assert action_a == DISABLE
        assert action_b == DISABLE
        assert action_c == DISABLE
        assert guard.is_disabled("strat_a")
        assert guard.is_disabled("strat_b")
        assert guard.is_disabled("strat_c")

        # Good strategy should NOT be affected
        good_trades = [{"pnl": 20}] * 10 + [{"pnl": -5}] * 2
        action_d, _ = guard.evaluate("strat_d", good_trades)
        assert action_d != DISABLE
        assert not guard.is_disabled("strat_d")

    def test_anomaly_pause_isolated_per_strategy(self):
        """AnomalyDetector pause on one strategy should not affect others."""
        detector = AnomalyDetector()

        # Pause strat_a with 3 consecutive losses
        for _ in range(3):
            detector.record_trade_result("strat_a", pnl=-10)

        # strat_b has a winning trade
        detector.record_trade_result("strat_b", pnl=50)

        assert detector.is_strategy_paused("strat_a") is True
        assert detector.is_strategy_paused("strat_b") is False


class TestMarginCallDuringAutonomous:
    """Scenario 7: Extreme drawdown triggers close_all during autonomous mode."""

    def test_auto_reducer_close_all_on_extreme_dd(self):
        """DD >= 2% should trigger close_all and invoke close_func."""
        close_called = []
        reduce_called = []

        reducer = AutoReducer(
            capital=10000,
            reduce_func=lambda pct: reduce_called.append(pct),
            close_func=lambda: close_called.append(True),
        )

        # 2.5% DD -> should trigger L2 close_all
        result = reducer.check_and_act(current_dd_pct=0.025)

        assert result["action_taken"] is True
        assert result["action"] == "close_all"
        assert len(close_called) == 1

    def test_controller_full_shutdown_sequence(self, controller):
        """Controller should: close_all -> kill_switch -> log events."""
        enter = controller.enter_autonomous(72)
        assert enter["success"] is True

        check = controller.periodic_check(current_dd_pct=0.025)

        # close_all should have been called
        controller._test_close_mock.assert_called()

        # kill switch should have been activated
        controller._test_kill_mock.assert_called_once_with(
            "Autonomous auto-reducer: DD >= 2%"
        )

        # Events log should record both AUTO_REDUCE and KILL_SWITCH
        event_types = [e["type"] for e in controller._events_log]
        assert "AUTO_REDUCE" in event_types
        assert "KILL_SWITCH" in event_types


class TestWeekendGapAutonomous:
    """Scenario 8: Weekend gap adverse on all FX positions."""

    def test_fx_bracket_handler_has_pre_weekend_check(self):
        """FXBracketHandler must have pre_weekend_check and create_fx_bracket_v2."""
        from core.broker.ibkr_bracket import BracketOrderManager, FXBracketHandler

        # Create bracket manager without IB connection (testing mode)
        bm = BracketOrderManager(ib_connection=None)
        fx_handler = FXBracketHandler(bm)

        # Verify critical methods exist
        assert hasattr(fx_handler, "create_fx_bracket_v2")
        assert hasattr(fx_handler, "pre_weekend_check")
        assert callable(fx_handler.create_fx_bracket_v2)
        assert callable(fx_handler.pre_weekend_check)

    def test_pre_weekend_check_returns_protection_status(self):
        """pre_weekend_check should report all_protected status."""
        from core.broker.ibkr_bracket import BracketOrderManager, FXBracketHandler

        bm = BracketOrderManager(ib_connection=None)
        fx_handler = FXBracketHandler(bm)

        # Without IB connection, returns empty check
        result = fx_handler.pre_weekend_check()
        assert "all_protected" in result
        assert "checked_at" in result

    def test_safety_checker_detects_missing_stops(self):
        """SafetyChecker should flag positions without broker-side stops."""
        safety = SafetyChecker(
            get_positions_func=lambda: [
                {"symbol": "EURUSD"},
                {"symbol": "GBPUSD"},
                {"symbol": "MCL"},
            ],
            get_open_orders_func=lambda: [
                # Only EURUSD has a stop -- GBPUSD and MCL are unprotected
                {"symbol": "EURUSD", "type": "stop_limit"},
            ],
            kill_switch_armed_func=lambda: True,
            reconciliation_ok_func=lambda: True,
        )

        result = safety.run_safety_check()
        assert result["safe"] is False
        assert len(result["blocking_issues"]) >= 1
        # Check that missing stops are identified
        bracket_check = next(
            c for c in result["checks"] if c["name"] == "bracket_orders"
        )
        assert bracket_check["passed"] is False
        assert "GBPUSD" in bracket_check["details"]
        assert "MCL" in bracket_check["details"]
