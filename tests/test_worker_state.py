"""Tests for WorkerState (R1-03) — thread-safe shared state."""

import threading

import pytest

from core.worker.worker_state import WorkerState


class TestWorkerStatePositions:
    def test_get_empty(self):
        ws = WorkerState()
        assert ws.get_positions() == {}

    def test_update_and_get(self):
        ws = WorkerState()
        ws.update_position("BTCUSDC", {"broker": "binance", "qty": 0.1})
        positions = ws.get_positions()
        assert "BTCUSDC" in positions
        assert positions["BTCUSDC"]["qty"] == 0.1

    def test_filter_by_broker(self):
        ws = WorkerState()
        ws.update_position("BTCUSDC", {"broker": "binance", "qty": 0.1})
        ws.update_position("AAPL", {"broker": "alpaca", "qty": 10})
        ws.update_position("EURUSD", {"broker": "ibkr", "qty": 100000})

        binance = ws.get_positions(broker="binance")
        assert len(binance) == 1
        assert "BTCUSDC" in binance

        alpaca = ws.get_positions(broker="alpaca")
        assert len(alpaca) == 1
        assert "AAPL" in alpaca

    def test_remove_position(self):
        ws = WorkerState()
        ws.update_position("BTCUSDC", {"broker": "binance"})
        ws.remove_position("BTCUSDC")
        assert ws.get_positions() == {}

    def test_remove_nonexistent(self):
        ws = WorkerState()
        ws.remove_position("NONEXISTENT")  # Should not raise

    def test_clear_all(self):
        ws = WorkerState()
        ws.update_position("A", {"broker": "binance"})
        ws.update_position("B", {"broker": "alpaca"})
        ws.clear_positions()
        assert ws.get_positions() == {}

    def test_clear_by_broker(self):
        ws = WorkerState()
        ws.update_position("A", {"broker": "binance"})
        ws.update_position("B", {"broker": "alpaca"})
        ws.clear_positions(broker="binance")
        positions = ws.get_positions()
        assert "A" not in positions
        assert "B" in positions


class TestWorkerStateRegime:
    def test_default_regime(self):
        ws = WorkerState()
        assert ws.get_regime("crypto") == "UNKNOWN"
        assert ws.get_regime("fx") == "UNKNOWN"
        assert ws.get_regime("nonexistent") == "UNKNOWN"

    def test_set_and_get(self):
        ws = WorkerState()
        ws.set_regime("crypto", "RISK_ON")
        assert ws.get_regime("crypto") == "RISK_ON"

    def test_get_all_regimes(self):
        ws = WorkerState()
        ws.set_regime("crypto", "RISK_ON")
        ws.set_regime("fx", "RISK_OFF")
        regimes = ws.get_all_regimes()
        assert regimes["crypto"] == "RISK_ON"
        assert regimes["fx"] == "RISK_OFF"
        assert regimes["global"] == "UNKNOWN"

    def test_regime_age(self):
        ws = WorkerState()
        assert ws.regime_age_seconds is None
        ws.set_regime("crypto", "RISK_ON")
        age = ws.regime_age_seconds
        assert age is not None
        assert age < 1.0


class TestWorkerStateKillSwitch:
    def test_default_not_killed(self):
        ws = WorkerState()
        assert not ws.is_killed("binance")
        assert not ws.is_killed("global")

    def test_activate_and_check(self):
        ws = WorkerState()
        ws.activate_kill("binance", reason="DD > 5%")
        assert ws.is_killed("binance")
        assert ws.get_kill_reason("binance") == "DD > 5%"

    def test_global_kill_affects_all(self):
        ws = WorkerState()
        ws.activate_kill("global", reason="market crash")
        assert ws.is_killed("binance")
        assert ws.is_killed("ibkr")
        assert ws.is_killed("alpaca")

    def test_deactivate_kill(self):
        ws = WorkerState()
        ws.activate_kill("binance", reason="test")
        ws.deactivate_kill("binance")
        assert not ws.is_killed("binance")
        assert ws.get_kill_reason("binance") == ""

    def test_get_active_kills(self):
        ws = WorkerState()
        ws.activate_kill("binance", reason="DD")
        ws.activate_kill("ibkr", reason="gateway down")
        active = ws.get_active_kills()
        assert len(active) == 2
        assert "binance" in active
        assert "ibkr" in active


class TestWorkerStateCycleMetrics:
    def test_record_and_get(self):
        ws = WorkerState()
        ws.record_cycle_metrics("crypto", {"duration": 2.3, "success": True})
        m = ws.get_cycle_metrics("crypto")
        assert m["duration"] == 2.3

    def test_get_nonexistent(self):
        ws = WorkerState()
        assert ws.get_cycle_metrics("nonexistent") is None

    def test_get_all(self):
        ws = WorkerState()
        ws.record_cycle_metrics("crypto", {"ok": True})
        ws.record_cycle_metrics("fx", {"ok": False})
        all_m = ws.get_all_cycle_metrics()
        assert len(all_m) == 2


class TestWorkerStateSnapshot:
    def test_snapshot(self):
        ws = WorkerState()
        ws.update_position("BTC", {"broker": "binance"})
        ws.set_regime("crypto", "RISK_ON")
        ws.activate_kill("binance", reason="test")

        snap = ws.snapshot()
        assert "BTC" in snap["positions"]
        assert snap["regimes"]["crypto"] == "RISK_ON"
        assert "binance" in snap["kills"]


class TestWorkerStateThreadSafety:
    def test_concurrent_position_updates(self):
        """Multiple threads updating positions concurrently."""
        ws = WorkerState()
        errors = []

        def updater(thread_id):
            try:
                for i in range(100):
                    ws.update_position(
                        f"t{thread_id}_pos{i}",
                        {"broker": "test", "qty": i},
                    )
                    ws.get_positions()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0
        assert len(ws.get_positions()) == 500

    def test_concurrent_regime_updates(self):
        ws = WorkerState()
        errors = []

        def setter(asset_class):
            try:
                for _ in range(100):
                    ws.set_regime(asset_class, "RISK_ON")
                    ws.get_regime(asset_class)
                    ws.get_all_regimes()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=setter, args=(ac,))
            for ac in ["crypto", "fx", "us_equity", "eu_equity", "global"]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0

    def test_concurrent_snapshot(self):
        """Snapshot while other threads are writing."""
        ws = WorkerState()
        errors = []

        def writer():
            try:
                for i in range(100):
                    ws.update_position(f"pos_{i}", {"broker": "test"})
                    ws.set_regime("crypto", f"state_{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    ws.snapshot()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0
