"""Tests for Phase 1-3 robustesse modules (R2-R7)."""

import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.broker.broker_health import (
    BrokerHealth,
    BrokerHealthRegistry,
    BrokerHealthTracker,
)
from core.broker.contracts.alpaca_contracts import AlpacaContract
from core.broker.contracts.binance_contracts import BinanceContract
from core.broker.contracts.contract_runner import ContractRunner
from core.broker.contracts.ibkr_contracts import IBKRContract
from core.broker.contracts.response_snapshots import ResponseSnapshotStore
from core.execution.order_tracker import OrderTracker
from core.execution.position_state_machine import (
    IllegalPositionTransition,
    PositionInvariantViolation,
    PositionState,
    PositionStateMachine,
)
from core.monitoring.anomaly_detector import AlertLevel, AnomalyDetector, AnomalyRule
from core.monitoring.metrics_pipeline import MetricsCollector
from core.risk.partial_data_handler import PartialDataHandler
from core.worker.replay_engine import ReplayEngine
from core.worker.shadow_mode import ShadowComparator, ShadowSignalLogger


# ═══════════════════════════════════════════════════════════════════
# R2-02: Anomaly Detector
# ═══════════════════════════════════════════════════════════════════


class TestAnomalyDetectorThreshold:
    def test_threshold_max_triggered(self, tmp_path):
        mc = MetricsCollector(db_path=str(tmp_path / "m.db"))
        mc.emit("test.metric", 100.0)
        mc.flush()

        rules = [AnomalyRule("test.metric", "threshold", AlertLevel.WARN, threshold_max=50)]
        detector = AnomalyDetector(mc, rules=rules)
        anomalies = detector.check_all()
        assert len(anomalies) == 1
        assert "100" in anomalies[0].message

    def test_threshold_max_not_triggered(self, tmp_path):
        mc = MetricsCollector(db_path=str(tmp_path / "m.db"))
        mc.emit("test.metric", 30.0)
        mc.flush()

        rules = [AnomalyRule("test.metric", "threshold", AlertLevel.WARN, threshold_max=50)]
        detector = AnomalyDetector(mc, rules=rules)
        assert len(detector.check_all()) == 0

    def test_threshold_min_triggered(self, tmp_path):
        mc = MetricsCollector(db_path=str(tmp_path / "m.db"))
        mc.emit("test.metric", 5.0)
        mc.flush()

        rules = [AnomalyRule("test.metric", "threshold", AlertLevel.WARN, threshold_min=10)]
        detector = AnomalyDetector(mc, rules=rules)
        assert len(detector.check_all()) == 1

    def test_alert_callback_fired(self, tmp_path):
        mc = MetricsCollector(db_path=str(tmp_path / "m.db"))
        mc.emit("test.metric", 100.0)
        mc.flush()

        alerts = []
        rules = [AnomalyRule("test.metric", "threshold", AlertLevel.WARN, threshold_max=50)]
        detector = AnomalyDetector(
            mc, rules=rules,
            alert_callback=lambda msg, lvl: alerts.append((msg, lvl))
        )
        detector.check_all()
        assert len(alerts) == 1

    def test_cooldown_prevents_spam(self, tmp_path):
        mc = MetricsCollector(db_path=str(tmp_path / "m.db"))
        mc.emit("test.metric", 100.0)
        mc.flush()

        alerts = []
        rules = [AnomalyRule("test.metric", "threshold", AlertLevel.WARN,
                             threshold_max=50, cooldown_minutes=60)]
        detector = AnomalyDetector(
            mc, rules=rules,
            alert_callback=lambda msg, lvl: alerts.append((msg, lvl))
        )
        detector.check_all()
        detector.check_all()  # Should be cooled down
        assert len(alerts) == 1


class TestAnomalyDetectorRecent:
    def test_get_recent_anomalies(self, tmp_path):
        mc = MetricsCollector(db_path=str(tmp_path / "m.db"))
        mc.emit("test.metric", 100.0)
        mc.flush()

        rules = [AnomalyRule("test.metric", "threshold", AlertLevel.WARN, threshold_max=50)]
        detector = AnomalyDetector(mc, rules=rules)
        detector.check_all()
        recent = detector.get_recent_anomalies(hours=1)
        assert len(recent) == 1


# ═══════════════════════════════════════════════════════════════════
# R6-01: Broker Health
# ═══════════════════════════════════════════════════════════════════


class TestBrokerHealthTracker:
    def test_initial_healthy(self):
        t = BrokerHealthTracker("binance")
        assert t.health == BrokerHealth.HEALTHY
        assert t.is_tradeable
        assert t.is_data_reliable

    def test_success_stays_healthy(self):
        t = BrokerHealthTracker("binance")
        t.record_success(50.0)
        assert t.health == BrokerHealth.HEALTHY
        assert t.avg_latency_ms == 50.0

    def test_error_degrades(self):
        t = BrokerHealthTracker("binance")
        t.record_error("timeout")
        assert t.health == BrokerHealth.DEGRADED
        assert t.is_tradeable  # Still tradeable when degraded

    def test_3_errors_is_down(self):
        t = BrokerHealthTracker("binance")
        t.record_error("e1")
        t.record_error("e2")
        t.record_error("e3")
        assert t.health == BrokerHealth.DOWN
        assert not t.is_tradeable
        assert not t.is_data_reliable

    def test_success_after_errors_recovers(self):
        t = BrokerHealthTracker("binance")
        t.record_error("e1")
        t.record_error("e2")
        t.record_success(40.0)
        assert t.health == BrokerHealth.HEALTHY

    def test_sizing_multiplier(self):
        t = BrokerHealthTracker("binance")
        assert t.sizing_multiplier == 1.0
        t.record_error("e1")
        assert t.sizing_multiplier == 0.5  # DEGRADED
        t.record_error("e2")
        t.record_error("e3")
        assert t.sizing_multiplier == 0.0  # DOWN

    def test_maintenance(self):
        t = BrokerHealthTracker("ibkr")
        t.set_maintenance(datetime.now() + timedelta(hours=48))
        assert t.health == BrokerHealth.MAINTENANCE
        assert not t.is_tradeable

    def test_to_dict(self):
        t = BrokerHealthTracker("binance")
        t.record_success(50.0)
        d = t.to_dict()
        assert d["broker"] == "binance"
        assert d["health"] == "HEALTHY"
        assert d["avg_latency_ms"] == 50.0


class TestBrokerHealthRegistry:
    def test_register_and_get(self):
        reg = BrokerHealthRegistry()
        reg.register("binance")
        reg.register("ibkr")
        assert reg.get("binance") is not None
        assert len(reg.get_all()) == 2

    def test_healthy_brokers(self):
        reg = BrokerHealthRegistry()
        reg.register("binance")
        reg.register("ibkr")
        reg.get("ibkr").record_error("e1")
        reg.get("ibkr").record_error("e2")
        reg.get("ibkr").record_error("e3")
        assert reg.healthy_brokers() == ["binance"]
        assert "ibkr" in reg.down_brokers()

    def test_summary(self):
        reg = BrokerHealthRegistry()
        reg.register("binance")
        s = reg.summary()
        assert "binance" in s


# ═══════════════════════════════════════════════════════════════════
# R3: Broker Contracts
# ═══════════════════════════════════════════════════════════════════


class TestBinanceContracts:
    def test_account_balance_valid(self):
        resp = {
            "balances": [
                {"asset": "USDC", "free": "1000.0", "locked": "0"},
                {"asset": "BTC", "free": "0.1", "locked": "0"},
            ],
            "canTrade": True,
            "canWithdraw": True,
        }
        ok, msg = BinanceContract.account_balance(resp)
        assert ok

    def test_account_balance_missing_key(self):
        ok, msg = BinanceContract.account_balance({"balances": []})
        assert not ok
        assert "Missing" in msg

    def test_order_response_valid(self):
        resp = {
            "symbol": "BTCUSDC", "orderId": 123, "status": "FILLED",
            "type": "MARKET", "side": "BUY", "executedQty": "0.1",
            "cummulativeQuoteQty": "4500",
        }
        ok, msg = BinanceContract.order_response(resp)
        assert ok

    def test_exchange_info_valid(self):
        resp = {
            "symbols": [
                {"symbol": "BTCUSDC", "status": "TRADING",
                 "baseAsset": "BTC", "quoteAsset": "USDC"},
            ]
        }
        ok, msg = BinanceContract.exchange_info(resp)
        assert ok

    def test_klines_valid(self):
        resp = [[1609459200000, "29000", "29500", "28500", "29300",
                 "100", 1609545600000, "2900000", 5000, "50",
                 "1450000", "0"]]
        ok, msg = BinanceContract.klines(resp)
        assert ok


class TestIBKRContracts:
    def test_account_info_valid(self):
        ok, msg = IBKRContract.account_info({"equity": 10000, "cash": 5000})
        assert ok

    def test_account_info_missing(self):
        ok, msg = IBKRContract.account_info({"equity": 10000})
        assert not ok


class TestAlpacaContracts:
    def test_account_valid(self):
        ok, msg = AlpacaContract.account({"equity": 30000, "cash": 10000})
        assert ok

    def test_position_valid(self):
        ok, msg = AlpacaContract.position({"symbol": "AAPL", "qty": 10})
        assert ok


class TestContractRunner:
    def test_validation_pass(self):
        runner = ContractRunner()
        result = runner.validate(
            "binance", "account_balance",
            {"balances": [], "canTrade": True, "canWithdraw": True},
            BinanceContract.account_balance,
        )
        assert result.passed

    def test_validation_fail(self):
        alerts = []
        runner = ContractRunner(
            alert_callback=lambda msg, lvl: alerts.append((msg, lvl))
        )
        result = runner.validate(
            "binance", "account_balance",
            {"invalid": True},
            BinanceContract.account_balance,
        )
        assert not result.passed
        assert len(alerts) == 1

    def test_3_consecutive_failures_critical(self):
        alerts = []
        runner = ContractRunner(
            alert_callback=lambda msg, lvl: alerts.append((msg, lvl))
        )
        for _ in range(3):
            runner.validate("binance", "test", {}, lambda r: (False, "bad"))
        assert any("CRITICAL" in a[0] for a in alerts)


# ═══════════════════════════════════════════════════════════════════
# R3-03: Response Snapshots
# ═══════════════════════════════════════════════════════════════════


class TestResponseSnapshots:
    def test_save_and_get_latest(self, tmp_path):
        store = ResponseSnapshotStore(base_dir=str(tmp_path))
        store.save("binance", "account", {"equity": 10000})
        latest = store.get_latest("binance", "account")
        assert latest is not None
        assert latest["response"]["equity"] == 10000

    def test_list_snapshots(self, tmp_path):
        store = ResponseSnapshotStore(base_dir=str(tmp_path))
        store.save("binance", "account", {"equity": 10000})
        store.save("ibkr", "positions", [])
        files = store.list_snapshots()
        assert len(files) == 2


# ═══════════════════════════════════════════════════════════════════
# R4-02: Order Tracker
# ═══════════════════════════════════════════════════════════════════


class TestOrderTracker:
    def test_create_and_validate(self):
        tracker = OrderTracker()
        osm = tracker.create_order("BTCUSDC", "BUY", 0.1, broker="binance")
        assert osm.state.value == "DRAFT"
        tracker.validate(osm.order_id, risk_approved=True)
        assert osm.state.value == "VALIDATED"

    def test_full_lifecycle(self):
        tracker = OrderTracker()
        osm = tracker.create_order("ETHUSDC", "BUY", 1.0)
        tracker.validate(osm.order_id, True)
        tracker.submit(osm.order_id, "BIN-123")
        tracker.fill(osm.order_id, has_sl=True, sl_order_id="SL-1")
        assert osm.is_terminal
        assert osm.state.value == "FILLED"

    def test_reject_path(self):
        tracker = OrderTracker()
        osm = tracker.create_order("BTCUSDC", "BUY", 0.1)
        tracker.validate(osm.order_id, risk_approved=False)
        assert osm.state.value == "REJECTED"

    def test_active_orders(self):
        tracker = OrderTracker()
        osm1 = tracker.create_order("BTC", "BUY", 0.1)
        osm2 = tracker.create_order("ETH", "BUY", 1.0)
        tracker.validate(osm1.order_id, True)
        tracker.submit(osm1.order_id, "B1")
        assert len(tracker.get_active_orders()) == 1

    def test_cancel(self):
        tracker = OrderTracker()
        osm = tracker.create_order("BTC", "BUY", 0.1)
        tracker.validate(osm.order_id, True)
        tracker.submit(osm.order_id, "B1")
        tracker.cancel(osm.order_id)
        assert osm.state.value == "CANCELLED"


# ═══════════════════════════════════════════════════════════════════
# R4-03: Position State Machine
# ═══════════════════════════════════════════════════════════════════


class TestPositionStateMachine:
    def test_happy_path(self):
        psm = PositionStateMachine(
            position_id="POS-1", symbol="BTCUSDC", side="LONG",
        )
        assert psm.state == PositionState.PENDING
        psm.transition(
            PositionState.OPEN,
            has_sl=True, sl_price=44000, entry_price=45000, quantity=0.1,
        )
        assert psm.state == PositionState.OPEN
        assert psm.has_sl

    def test_open_without_sl_raises(self):
        psm = PositionStateMachine(position_id="POS-1", symbol="BTC")
        with pytest.raises(PositionInvariantViolation):
            psm.transition(PositionState.OPEN, has_sl=False)

    def test_close_records_pnl(self):
        psm = PositionStateMachine(position_id="POS-1", symbol="BTC")
        psm.transition(PositionState.OPEN, has_sl=True, entry_price=45000, quantity=0.1)
        psm.transition(PositionState.CLOSING)
        psm.transition(PositionState.CLOSED, realized_pnl=150.0)
        assert psm.realized_pnl == 150.0
        assert psm.is_terminal

    def test_closed_to_open_illegal(self):
        psm = PositionStateMachine(position_id="POS-1", symbol="BTC")
        psm.transition(PositionState.OPEN, has_sl=True)
        psm.transition(PositionState.CLOSING)
        psm.transition(PositionState.CLOSED, realized_pnl=0)
        with pytest.raises(IllegalPositionTransition):
            psm.transition(PositionState.OPEN)

    def test_emergency_close(self):
        psm = PositionStateMachine(position_id="POS-1", symbol="BTC")
        psm.transition(PositionState.OPEN, has_sl=True)
        psm.transition(PositionState.EMERGENCY)
        psm.transition(PositionState.CLOSED)
        assert psm.is_terminal

    def test_orphan_detection(self):
        psm = PositionStateMachine(
            position_id="POS-1", symbol="BTC",
            state=PositionState.ORPHAN,
        )
        # Can adopt (transition to OPEN) or close
        psm.transition(PositionState.CLOSING)

    def test_to_dict(self):
        psm = PositionStateMachine(position_id="POS-1", symbol="BTC")
        d = psm.to_dict()
        assert d["position_id"] == "POS-1"
        assert d["state"] == "PENDING"


# ═══════════════════════════════════════════════════════════════════
# R5-02: Replay Engine
# ═══════════════════════════════════════════════════════════════════


class TestReplayEngine:
    def test_load_events(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"ts": "2026-04-03T10:00:00", "cycle": "crypto", "type": "CYCLE_START", "data": {}, "snapshot": {"regime": "RISK_ON"}}\n'
            '{"ts": "2026-04-03T10:00:02", "cycle": "crypto", "type": "CYCLE_END", "data": {"duration_ms": 2000, "success": true}}\n'
        )
        engine = ReplayEngine(str(events_file))
        assert len(engine.events) == 2

    def test_filter_by_cycle(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"ts": "2026-04-03T10:00:00", "cycle": "crypto", "type": "SIGNAL", "data": {"symbol": "BTC"}}\n'
            '{"ts": "2026-04-03T10:00:01", "cycle": "fx", "type": "SIGNAL", "data": {"symbol": "EURUSD"}}\n'
        )
        engine = ReplayEngine(str(events_file))
        results = engine.replay(cycle_name="crypto")
        assert len(results) == 1
        assert results[0]["cycle"] == "crypto"

    def test_get_timeline(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"ts": "2026-04-03T10:00:00", "cycle": "crypto", "type": "SIGNAL", "data": {"symbol": "BTC"}}\n'
            '{"ts": "2026-04-03T10:00:01", "cycle": "crypto", "type": "ERROR", "data": {"error": "timeout"}}\n'
        )
        engine = ReplayEngine(str(events_file))
        timeline = engine.get_timeline()
        assert len(timeline) == 2

    def test_get_errors(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"ts": "2026-04-03T10:00:00", "cycle": "crypto", "type": "SIGNAL", "data": {}}\n'
            '{"ts": "2026-04-03T10:00:01", "cycle": "crypto", "type": "ERROR", "data": {"error": "boom"}}\n'
        )
        engine = ReplayEngine(str(events_file))
        errors = engine.get_errors()
        assert len(errors) == 1

    def test_empty_file(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        engine = ReplayEngine(str(events_file))
        assert len(engine.events) == 0

    def test_nonexistent_file(self, tmp_path):
        engine = ReplayEngine(str(tmp_path / "nope.jsonl"))
        assert len(engine.events) == 0


# ═══════════════════════════════════════════════════════════════════
# R7-01: Shadow Mode
# ═══════════════════════════════════════════════════════════════════


class TestShadowSignalLogger:
    def test_log_signal(self, tmp_path):
        logger = ShadowSignalLogger(output_dir=str(tmp_path))
        logger.log_signal("crypto", {"symbol": "BTC", "side": "BUY"})
        assert logger.signal_count == 1
        f = tmp_path / "shadow_signals.jsonl"
        assert f.exists()
        data = json.loads(f.read_text().strip())
        assert data["cycle"] == "crypto"
        assert data["signal"]["symbol"] == "BTC"


class TestShadowComparator:
    def test_no_divergences_empty(self, tmp_path):
        comp = ShadowComparator(
            live_signals_path=str(tmp_path / "live"),
            shadow_signals_path=str(tmp_path / "shadow.jsonl"),
        )
        divergences = comp.compare()
        assert len(divergences) == 0


# ═══════════════════════════════════════════════════════════════════
# R6-02: Partial Data Handler
# ═══════════════════════════════════════════════════════════════════


class TestPartialDataHandler:
    def test_healthy_broker_data(self):
        pdh = PartialDataHandler()
        bd = pdh.prepare_broker_data(
            "binance", {"equity": 10000, "positions": [], "cash": 5000},
            is_healthy=True,
        )
        assert bd.is_reliable
        assert not bd.is_frozen
        assert bd.equity == 10000

    def test_down_broker_returns_frozen(self):
        pdh = PartialDataHandler()
        # First call with data to save last known
        pdh.prepare_broker_data(
            "ibkr", {"equity": 15000, "positions": [], "cash": 5000},
            is_healthy=True,
        )
        # Second call with broker down
        bd = pdh.prepare_broker_data("ibkr", None, is_healthy=False)
        assert not bd.is_reliable
        assert bd.is_frozen
        assert bd.equity == 15000

    def test_partial_nav(self):
        from core.risk.partial_data_handler import BrokerData
        bd_list = [
            BrokerData("binance", 10000, [], 5000, is_reliable=True),
            BrokerData("ibkr", 15000, [], 5000, is_reliable=False, is_frozen=True),
        ]
        pdh = PartialDataHandler()
        nav, is_partial, excluded = pdh.calculate_partial_nav(bd_list)
        assert nav == 25000
        assert is_partial
        assert "ibkr" in excluded

    def test_regime_with_partial(self):
        pdh = PartialDataHandler()
        regimes = {
            "fx": "RISK_ON",
            "crypto": "NEUTRAL",
            "us_equity": "RISK_ON",
            "eu_equity": "RISK_ON",
            "global": "RISK_ON",
        }
        result = pdh.get_regime_with_partial(regimes, healthy_brokers=["binance", "alpaca"])
        # IBKR is down → fx and eu_equity should be UNKNOWN
        assert result["fx"] == "UNKNOWN"
        assert result["eu_equity"] == "UNKNOWN"
        assert result["crypto"] == "NEUTRAL"  # Binance is healthy
        # Global should be worst = UNKNOWN
        assert result["global"] == "UNKNOWN"

    def test_format_portfolio_status(self):
        from core.risk.partial_data_handler import BrokerData
        bd_list = [
            BrokerData("binance", 10000, [], 5000, is_reliable=True),
            BrokerData("ibkr", 15000, [], 5000, is_reliable=False,
                       is_frozen=True, frozen_at="2026-04-03T03:15"),
        ]
        pdh = PartialDataHandler()
        status = pdh.format_portfolio_status(bd_list, 25000)
        assert "binance: $10,000" in status
        assert "FROZEN" in status
        assert "PARTIAL" in status
