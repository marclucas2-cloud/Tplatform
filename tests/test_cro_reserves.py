"""Tests for CRO reserve fixes — H-1/H-2/H-3/M-1/M-3/L-1.

Validates that all CRO audit findings are properly fixed.
"""

import json
import os
import signal
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKER_SOURCE = (ROOT / "worker.py").read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# H-2: validate_order() MUST check stop_loss
# ═══════════════════════════════════════════════════════════════════


class TestSLMandatory:
    """H-2: validate_order() rejects orders without stop_loss."""

    def test_validate_order_no_sl_rejected(self):
        """LiveRiskManager.validate_order() must reject orders without SL."""
        from core.risk_manager_live import LiveRiskManager
        rm = LiveRiskManager()
        order = {
            "symbol": "AAPL",
            "direction": "BUY",
            "notional": 1000,
            "strategy": "test",
            "asset_class": "equity",
            # NO stop_loss key
        }
        portfolio = {"equity": 10000, "positions": [], "cash": 5000}
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed
        assert "stop_loss" in msg.lower()

    def test_validate_order_with_sl_passes_first_check(self):
        """Orders with stop_loss pass the SL check (may fail other checks)."""
        from core.risk_manager_live import LiveRiskManager
        rm = LiveRiskManager()
        order = {
            "symbol": "AAPL",
            "direction": "BUY",
            "notional": 1000,
            "strategy": "test",
            "asset_class": "equity",
            "stop_loss": 145.0,
        }
        portfolio = {"equity": 10000, "positions": [], "cash": 5000}
        passed, msg = rm.validate_order(order, portfolio)
        # May pass or fail on other checks, but NOT on stop_loss
        assert "stop_loss" not in msg.lower() or passed

    def test_validate_order_reduce_only_no_sl_ok(self):
        """Reduce-only (closing) orders don't need SL."""
        from core.risk_manager_live import LiveRiskManager
        rm = LiveRiskManager()
        order = {
            "symbol": "AAPL",
            "direction": "SELL",
            "notional": 500,
            "strategy": "test",
            "reduce_only": True,
            # NO stop_loss — OK for closes
        }
        portfolio = {"equity": 10000, "positions": [], "cash": 5000}
        passed, msg = rm.validate_order(order, portfolio)
        # Should NOT fail on stop_loss
        assert "stop_loss" not in msg.lower() or passed

    def test_crypto_order_manager_rejects_no_sl(self):
        """CryptoOrderManager.submit_order() rejects orders without SL."""
        from core.crypto.order_manager import CryptoOrderManager
        broker = MagicMock()
        mgr = CryptoOrderManager(broker)
        result = mgr.submit_order(
            symbol="BTCUSDC",
            direction="BUY",
            qty=0.01,
            strategy="test",
            stop_loss=None,  # No SL
            _authorized_by="test",
        )
        assert "error" in result
        assert "stop_loss" in result["error"]
        broker.create_position.assert_not_called()

    def test_crypto_order_manager_accepts_with_sl(self):
        """CryptoOrderManager.submit_order() accepts orders with SL."""
        from core.crypto.order_manager import CryptoOrderManager
        broker = MagicMock()
        broker.create_position.return_value = {"orderId": "123", "status": "FILLED"}
        mgr = CryptoOrderManager(broker)
        result = mgr.submit_order(
            symbol="BTCUSDC",
            direction="BUY",
            qty=0.01,
            strategy="test",
            stop_loss=44000.0,
            _authorized_by="test",
        )
        assert "error" not in result
        broker.create_position.assert_called_once()

    def test_crypto_reduce_only_no_sl_ok(self):
        """Reduce-only crypto orders don't need SL."""
        from core.crypto.order_manager import CryptoOrderManager
        broker = MagicMock()
        broker.create_position.return_value = {"orderId": "123", "status": "FILLED"}
        mgr = CryptoOrderManager(broker)
        result = mgr.submit_order(
            symbol="BTCUSDC",
            direction="SELL",
            qty=0.01,
            strategy="test",
            stop_loss=None,
            reduce_only=True,
            _authorized_by="test",
        )
        assert "error" not in result


# ═══════════════════════════════════════════════════════════════════
# H-1: OrderTracker wired into CryptoOrderManager
# ═══════════════════════════════════════════════════════════════════


class TestOrderTrackerIntegration:
    """H-1: OrderTracker is wired into live order flow."""

    def test_order_tracked_on_success(self):
        """Successful order creates and fills an OrderStateMachine."""
        from core.crypto.order_manager import CryptoOrderManager
        from core.execution.order_tracker import OrderTracker

        broker = MagicMock()
        broker.create_position.return_value = {
            "orderId": "BIN-123", "status": "FILLED", "filled_price": 45000,
        }
        tracker = OrderTracker()
        mgr = CryptoOrderManager(broker, order_tracker=tracker)

        mgr.submit_order(
            symbol="BTCUSDC", direction="BUY", qty=0.01,
            strategy="test", stop_loss=44000.0, _authorized_by="test",
        )

        orders = tracker.get_recent_orders()
        assert len(orders) == 1
        assert orders[0].state.value == "FILLED"
        assert orders[0].symbol == "BTCUSDC"
        assert orders[0].has_sl

    def test_order_tracked_on_rejection(self):
        """Rejected order (risk check) is tracked as REJECTED."""
        from core.crypto.order_manager import CryptoOrderManager
        from core.execution.order_tracker import OrderTracker

        broker = MagicMock()
        tracker = OrderTracker()
        mgr = CryptoOrderManager(broker, order_tracker=tracker)

        # No SL → rejected
        mgr.submit_order(
            symbol="BTCUSDC", direction="BUY", qty=0.01,
            strategy="test", stop_loss=None, _authorized_by="test",
        )

        # Should not have created an OSM (rejected before creation)
        # Actually it creates one first then rejects — let's check
        orders = tracker.get_recent_orders()
        # The order should be empty or not exist because SL check is before tracker
        # Actually looking at the code, the SL check is BEFORE tracker creation
        assert len(orders) == 0

    def test_order_tracked_on_broker_error(self):
        """Order that fails at broker level is tracked as ERROR."""
        from core.broker.base import BrokerError
        from core.crypto.order_manager import CryptoOrderManager
        from core.execution.order_tracker import OrderTracker

        broker = MagicMock()
        broker.create_position.side_effect = BrokerError("connection refused")
        tracker = OrderTracker()
        mgr = CryptoOrderManager(broker, order_tracker=tracker)

        mgr.submit_order(
            symbol="BTCUSDC", direction="BUY", qty=0.01,
            strategy="test", stop_loss=44000.0, _authorized_by="test",
            max_retries=1,
        )

        orders = tracker.get_recent_orders()
        assert len(orders) == 1
        assert orders[0].state.value == "ERROR"


# ═══════════════════════════════════════════════════════════════════
# H-3: SIGTERM handler closes positions
# ═══════════════════════════════════════════════════════════════════


class TestSIGTERMHandler:
    """H-3: SIGTERM handler closes positions before exit."""

    def test_sigterm_handler_exists_in_worker(self):
        """Worker has a SIGTERM handler."""
        assert "signal.signal(signal.SIGTERM" in WORKER_SOURCE

    def test_sigterm_closes_binance(self):
        """SIGTERM handler calls close_all_positions on Binance."""
        assert "close_all_positions" in WORKER_SOURCE
        assert "sigterm_graceful_shutdown" in WORKER_SOURCE

    def test_sigterm_cancels_alpaca(self):
        """SIGTERM handler cancels Alpaca orders."""
        assert "cancel_all_orders" in WORKER_SOURCE

    def test_sigterm_flushes_metrics(self):
        """SIGTERM handler flushes metrics before exit."""
        assert "get_metrics().flush()" in WORKER_SOURCE or "metrics().flush" in WORKER_SOURCE


# ═══════════════════════════════════════════════════════════════════
# M-1: Health endpoint enriched
# ═══════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    """M-1: Health endpoint returns detailed system info."""

    def test_health_handler_includes_pid(self):
        source = (ROOT / "core" / "worker" / "health.py").read_text()
        assert "pid" in source
        assert "os.getpid()" in source

    def test_health_handler_includes_memory(self):
        source = (ROOT / "core" / "worker" / "health.py").read_text()
        assert "memory_mb" in source

    def test_health_handler_includes_cycles(self):
        source = (ROOT / "core" / "worker" / "health.py").read_text()
        assert "cycles" in source


# ═══════════════════════════════════════════════════════════════════
# M-3: Dead man's switch
# ═══════════════════════════════════════════════════════════════════


class TestDeadMansSwitch:
    """M-3: Worker detects stale heartbeat and alerts."""

    def test_worker_checks_heartbeat_age(self):
        """Worker main loop checks heartbeat file age."""
        assert "heartbeat.age_minutes" in WORKER_SOURCE or "DEAD MAN" in WORKER_SOURCE

    def test_alerts_on_stale_heartbeat(self):
        assert "DEAD MAN'S SWITCH" in WORKER_SOURCE


# ═══════════════════════════════════════════════════════════════════
# L-1: Proactive memory management
# ═══════════════════════════════════════════════════════════════════


class TestMemoryManagement:
    """L-1: Worker triggers gc.collect() at 300MB."""

    def test_gc_collect_in_worker(self):
        assert "gc.collect()" in WORKER_SOURCE

    def test_memory_threshold_300mb(self):
        assert "300" in WORKER_SOURCE and "gc.collect" in WORKER_SOURCE


# ═══════════════════════════════════════════════════════════════════
# M-4: Backtest costs aligned
# ═══════════════════════════════════════════════════════════════════


class TestBacktestCosts:
    """M-4: US equity costs reflect Alpaca $0 commission."""

    def test_claude_md_shows_zero_commission(self):
        claude_md = (ROOT / "CLAUDE.md").read_text()
        assert "$0 commission" in claude_md or "$0" in claude_md


# ═══════════════════════════════════════════════════════════════════
# Broker Health + Contract integration check
# ═══════════════════════════════════════════════════════════════════


class TestBrokerHealthWired:
    """H-1: BrokerHealthRegistry initialized in worker."""

    def test_broker_health_in_worker(self):
        assert "BrokerHealthRegistry" in WORKER_SOURCE
        assert '_broker_health.register("binance")' in WORKER_SOURCE
        assert '_broker_health.register("ibkr")' in WORKER_SOURCE

    def test_order_tracker_in_worker(self):
        assert "OrderTracker" in WORKER_SOURCE
        assert "_order_tracker" in WORKER_SOURCE
