"""OrderTracker persistence + crash recovery regression tests.

Covers Phase 3 XXL plan: pre-fix the OrderTracker was in-memory only,
all in-flight orders lost on worker restart -> orphan orders on broker
with no internal record -> recovery impossible.

Post-fix: state_path enables atomic save on every transition + load on init,
classified recovery summary for boot-time reconciliation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.execution.order_state_machine import (
    OrderState,
    OrderStateMachine,
)
from core.execution.order_tracker import (
    ORDER_TRACKER_SCHEMA_VERSION,
    OrderTracker,
)


# ---------------------------------------------------------------------------
# OrderStateMachine round-trip
# ---------------------------------------------------------------------------

class TestOrderStateMachineRoundTrip:
    def test_to_dict_then_from_dict_preserves_state(self):
        osm = OrderStateMachine(
            order_id="ORD-TEST-001",
            symbol="BTCUSDT",
            side="BUY",
            total_quantity=0.1,
        )
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="BIN-12345")

        raw = osm.to_dict()
        restored = OrderStateMachine.from_dict(raw)

        assert restored.order_id == "ORD-TEST-001"
        assert restored.symbol == "BTCUSDT"
        assert restored.side == "BUY"
        assert restored.state == OrderState.SUBMITTED
        assert restored.broker_order_id == "BIN-12345"
        assert restored.total_quantity == 0.1
        assert restored.validated_at is not None
        assert restored.submitted_at is not None
        assert len(restored.history) == 2  # validated + submitted

    def test_from_dict_handles_missing_optional_fields(self):
        raw = {
            "order_id": "ORD-MIN",
            "state": "DRAFT",
            "total_quantity": 1.0,
        }
        osm = OrderStateMachine.from_dict(raw)
        assert osm.order_id == "ORD-MIN"
        assert osm.state == OrderState.DRAFT
        assert osm.symbol == ""
        assert osm.broker_order_id is None


# ---------------------------------------------------------------------------
# OrderTracker persistence
# ---------------------------------------------------------------------------

class TestOrderTrackerPersistence:
    def test_no_persistence_when_path_not_set(self, tmp_path):
        """Backward compat: no state_path = no save attempted."""
        tracker = OrderTracker()
        tracker.create_order("BTCUSDT", "BUY", 0.1, broker="binance")
        # No file written
        assert not (tmp_path / "any.json").exists()

    def test_create_order_persists_immediately(self, tmp_path):
        path = tmp_path / "tracker.json"
        tracker = OrderTracker(state_path=path)
        osm = tracker.create_order("BTCUSDT", "BUY", 0.1)

        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == ORDER_TRACKER_SCHEMA_VERSION
        assert osm.order_id in data["orders"]
        assert data["orders"][osm.order_id]["state"] == "DRAFT"

    def test_atomic_write_no_partial_state(self, tmp_path):
        path = tmp_path / "tracker.json"
        tracker = OrderTracker(state_path=path)
        for _ in range(5):
            tracker.create_order("ETHUSDT", "SELL", 0.5)
        # No leftover .tmp files
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_full_lifecycle_persisted(self, tmp_path):
        path = tmp_path / "tracker.json"
        tracker = OrderTracker(state_path=path)
        osm = tracker.create_order("BTCUSDT", "BUY", 0.1)
        assert tracker.validate(osm.order_id, risk_approved=True)
        assert tracker.submit(osm.order_id, broker_order_id="BIN-XYZ")

        data = json.loads(path.read_text(encoding="utf-8"))
        rec = data["orders"][osm.order_id]
        assert rec["state"] == "SUBMITTED"
        assert rec["broker_order_id"] == "BIN-XYZ"
        assert len(rec["history"]) == 2  # validated + submitted


# ---------------------------------------------------------------------------
# Crash recovery: THE bug we are fixing
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    def test_session_2_recovers_orders_from_session_1(self, tmp_path):
        """Worker restart: in-flight orders survive crash, recovery_summary lists them."""
        path = tmp_path / "tracker.json"

        # --- Session 1: create + submit 2 orders, then "crash" ---
        t1 = OrderTracker(state_path=path)
        o1 = t1.create_order("BTCUSDT", "BUY", 0.1, broker="binance")
        o2 = t1.create_order("ETHUSDT", "SELL", 0.5, broker="binance")
        t1.validate(o1.order_id, risk_approved=True)
        t1.submit(o1.order_id, broker_order_id="BIN-001")
        t1.validate(o2.order_id, risk_approved=True)
        t1.submit(o2.order_id, broker_order_id="BIN-002")
        # No clean shutdown — just discard t1

        # --- Session 2: new tracker reads same path -> RECOVER ---
        t2 = OrderTracker(state_path=path)
        summary = t2.recovery_summary()
        assert summary["total_recovered"] == 2
        assert set(summary["active_order_ids"]) == {o1.order_id, o2.order_id}

        # Restored orders are usable
        recovered_o1 = t2.get(o1.order_id)
        assert recovered_o1 is not None
        assert recovered_o1.state == OrderState.SUBMITTED
        assert recovered_o1.broker_order_id == "BIN-001"

    def test_recovery_corrupt_state_starts_empty(self, tmp_path):
        """Corrupt state file: log critical + start empty (caller must reconcile)."""
        path = tmp_path / "tracker.json"
        path.write_text("{{{ corrupt", encoding="utf-8")

        alerts: list[str] = []
        t = OrderTracker(state_path=path, alert_callback=alerts.append)
        assert t.recovery_summary()["total_recovered"] == 0
        # Critical alert fired
        assert any("CORRUPT" in a for a in alerts)

    def test_recovery_wrong_schema_starts_empty(self, tmp_path):
        path = tmp_path / "tracker.json"
        path.write_text(
            json.dumps({"schema_version": 999, "orders": {}}),
            encoding="utf-8",
        )
        t = OrderTracker(state_path=path)
        assert t.recovery_summary()["total_recovered"] == 0

    def test_recovery_skips_individual_corrupt_entries(self, tmp_path):
        """One bad order entry doesn't poison the whole tracker."""
        path = tmp_path / "tracker.json"
        path.write_text(json.dumps({
            "schema_version": ORDER_TRACKER_SCHEMA_VERSION,
            "orders": {
                "GOOD-1": {
                    "order_id": "GOOD-1",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "state": "SUBMITTED",
                    "total_quantity": 0.1,
                    "broker_order_id": "BIN-G1",
                },
                "BAD-1": {
                    "order_id": "BAD-1",
                    "state": "INVALID_STATE_NAME",
                },
            },
        }), encoding="utf-8")

        t = OrderTracker(state_path=path)
        assert t.recovery_summary()["total_recovered"] == 1
        assert t.get("GOOD-1") is not None
        assert t.get("BAD-1") is None

    def test_terminal_orders_persisted_but_not_listed_active(self, tmp_path):
        path = tmp_path / "tracker.json"
        t1 = OrderTracker(state_path=path)
        o = t1.create_order("BTCUSDT", "BUY", 0.1)
        t1.validate(o.order_id, risk_approved=True)
        t1.submit(o.order_id, broker_order_id="BIN-FILL")
        t1.fill(o.order_id, has_sl=True, sl_order_id="SL-001")  # terminal

        t2 = OrderTracker(state_path=path)
        assert t2.recovery_summary()["total_recovered"] == 1
        # FILLED is terminal -> not in active_order_ids
        assert t2.recovery_summary()["active_order_ids"] == []
        assert t2.get(o.order_id).state == OrderState.FILLED
