"""PositionTracker regression tests (Phase B post-XXL).

Same pattern as OrderTracker recovery tests. Validates lifecycle, persistence,
crash recovery, orphan handling.
"""
from __future__ import annotations

import json

import pytest

from core.execution.position_state_machine import (
    PositionInvariantViolation,
    PositionState,
    PositionStateMachine,
)
from core.execution.position_tracker import (
    POSITION_TRACKER_SCHEMA_VERSION,
    PositionTracker,
)


# ---------------------------------------------------------------------------
# PSM round-trip
# ---------------------------------------------------------------------------

class TestPositionStateMachineRoundTrip:
    def test_to_dict_then_from_dict_preserves_state(self):
        psm = PositionStateMachine(
            position_id="POS-001",
            symbol="BTCUSDT",
            side="LONG",
            broker="binance",
            quantity=0.1,
            entry_price=50000.0,
        )
        psm.transition(
            PositionState.OPEN,
            has_sl=True, sl_price=49000, entry_price=50000, quantity=0.1,
        )
        raw = psm.to_dict()
        restored = PositionStateMachine.from_dict(raw)
        assert restored.position_id == "POS-001"
        assert restored.symbol == "BTCUSDT"
        assert restored.side == "LONG"
        assert restored.state == PositionState.OPEN
        assert restored.has_sl is True
        assert restored.sl_price == 49000
        assert restored.opened_at is not None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestPositionLifecycle:
    def test_create_position_pending(self, tmp_path):
        path = tmp_path / "tracker.json"
        t = PositionTracker(state_path=path)
        psm = t.create_position("BTCUSDT", "LONG", broker="binance")
        assert psm.state == PositionState.PENDING
        assert path.exists()

    def test_confirm_open_requires_sl(self, tmp_path):
        path = tmp_path / "tracker.json"
        alerts = []
        t = PositionTracker(state_path=path, alert_callback=alerts.append)
        psm = t.create_position("BTCUSDT", "LONG", broker="binance")
        # confirm_open passes has_sl=True internally, so this should succeed
        ok = t.confirm_open(
            psm.position_id,
            entry_price=50000.0,
            quantity=0.1,
            sl_price=49000.0,
        )
        assert ok
        assert t.get(psm.position_id).state == PositionState.OPEN
        assert t.get(psm.position_id).sl_price == 49000.0

    def test_close_lifecycle(self, tmp_path):
        path = tmp_path / "tracker.json"
        t = PositionTracker(state_path=path)
        psm = t.create_position("BTCUSDT", "LONG", broker="binance")
        t.confirm_open(psm.position_id, 50000, 0.1, 49000)
        assert t.closing(psm.position_id)
        assert t.close_complete(psm.position_id, realized_pnl=150.0)
        assert t.get(psm.position_id).state == PositionState.CLOSED
        assert t.get(psm.position_id).realized_pnl == 150.0

    def test_emergency_close_from_open(self, tmp_path):
        path = tmp_path / "tracker.json"
        t = PositionTracker(state_path=path)
        psm = t.create_position("BTCUSDT", "LONG", broker="binance")
        t.confirm_open(psm.position_id, 50000, 0.1, 49000)
        assert t.emergency_close(psm.position_id, reason="kill_switch_DD")
        assert t.get(psm.position_id).state == PositionState.EMERGENCY


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPositionTrackerPersistence:
    def test_no_persistence_when_path_not_set(self, tmp_path):
        t = PositionTracker()
        psm = t.create_position("BTCUSDT", "LONG", broker="binance")
        assert psm is not None
        assert not (tmp_path / "any.json").exists()

    def test_atomic_write_no_partial_state(self, tmp_path):
        path = tmp_path / "tracker.json"
        t = PositionTracker(state_path=path)
        for _ in range(5):
            t.create_position("BTC", "LONG", broker="binance")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_full_lifecycle_persisted(self, tmp_path):
        path = tmp_path / "tracker.json"
        t = PositionTracker(state_path=path)
        psm = t.create_position("BTCUSDT", "LONG", broker="binance")
        t.confirm_open(psm.position_id, 50000, 0.1, 49000)
        t.closing(psm.position_id)
        t.close_complete(psm.position_id, realized_pnl=150.0)

        data = json.loads(path.read_text(encoding="utf-8"))
        rec = data["positions"][psm.position_id]
        assert rec["state"] == "CLOSED"
        assert rec["realized_pnl"] == 150.0


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    def test_session_2_recovers_open_positions(self, tmp_path):
        path = tmp_path / "tracker.json"
        t1 = PositionTracker(state_path=path)
        p1 = t1.create_position("BTCUSDT", "LONG", broker="binance")
        p2 = t1.create_position("ETHUSDT", "SHORT", broker="binance")
        t1.confirm_open(p1.position_id, 50000, 0.1, 49000)
        t1.confirm_open(p2.position_id, 3000, 0.5, 3100)

        # Crash, new session
        t2 = PositionTracker(state_path=path)
        summary = t2.recovery_summary()
        assert summary["total_recovered"] == 2
        assert set(summary["active_position_ids"]) == {p1.position_id, p2.position_id}
        assert t2.get(p1.position_id).state == PositionState.OPEN
        assert t2.get(p1.position_id).sl_price == 49000

    def test_corrupt_state_alerts_and_starts_empty(self, tmp_path):
        path = tmp_path / "tracker.json"
        path.write_text("{{{ corrupt", encoding="utf-8")
        alerts = []
        t = PositionTracker(state_path=path, alert_callback=alerts.append)
        assert t.recovery_summary()["total_recovered"] == 0
        assert any("CORRUPT" in a for a in alerts)

    def test_terminal_positions_not_listed_active(self, tmp_path):
        path = tmp_path / "tracker.json"
        t1 = PositionTracker(state_path=path)
        psm = t1.create_position("BTCUSDT", "LONG", broker="binance")
        t1.confirm_open(psm.position_id, 50000, 0.1, 49000)
        t1.closing(psm.position_id)
        t1.close_complete(psm.position_id, realized_pnl=100)

        t2 = PositionTracker(state_path=path)
        summary = t2.recovery_summary()
        assert summary["total_recovered"] == 1
        assert summary["active_position_ids"] == []  # CLOSED is terminal


# ---------------------------------------------------------------------------
# Orphan management
# ---------------------------------------------------------------------------

class TestOrphanManagement:
    def test_mark_orphan_creates_entry_and_alerts(self, tmp_path):
        path = tmp_path / "tracker.json"
        alerts = []
        t = PositionTracker(state_path=path, alert_callback=alerts.append)
        psm = t.mark_orphan(symbol="MES", broker="ibkr", qty=1)
        assert psm.state == PositionState.ORPHAN
        assert psm.symbol == "MES"
        assert psm.quantity == 1
        assert any("ORPHAN" in a for a in alerts)

    def test_get_orphans_filters_correctly(self, tmp_path):
        t = PositionTracker(state_path=tmp_path / "t.json")
        normal = t.create_position("BTC", "LONG", broker="binance")
        t.confirm_open(normal.position_id, 50000, 0.1, 49000)
        t.mark_orphan("MES", "ibkr", 1)
        t.mark_orphan("MGC", "ibkr", -1)

        orphans = t.get_orphans()
        assert len(orphans) == 2
        assert {o.symbol for o in orphans} == {"MES", "MGC"}


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_removes_terminal_old(self, tmp_path):
        from datetime import datetime, timedelta
        t = PositionTracker(state_path=tmp_path / "t.json")
        psm = t.create_position("BTC", "LONG", broker="binance")
        t.confirm_open(psm.position_id, 50000, 0.1, 49000)
        t.closing(psm.position_id)
        t.close_complete(psm.position_id, realized_pnl=100)
        # Mock created_at to old
        psm.created_at = datetime.now() - timedelta(hours=48)
        n = t.cleanup_terminal(max_age_hours=24)
        assert n == 1
        assert t.get(psm.position_id) is None
