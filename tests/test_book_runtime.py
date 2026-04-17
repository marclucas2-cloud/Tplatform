"""Tests for BookRuntime + BookSupervisor — Phase 4 isolation."""
import time
from unittest.mock import MagicMock

import pytest

from core.runtime.book_runtime import BookRuntime, BookState, VALID_TRANSITIONS
from core.runtime.supervisor import BookSupervisor


def _make_runtime(book_id="test_book", mode="live_allowed", cycles=None, **kw):
    if cycles is None:
        cycles = {"main": lambda: {"trades": 1}}
    return BookRuntime(book_id=book_id, broker="test", mode=mode, cycles=cycles, **kw)


class TestBookState:
    def test_is_tradeable(self):
        assert BookState.RUNNING_LIVE.is_tradeable
        assert BookState.RUNNING_PAPER.is_tradeable
        assert BookState.DEGRADED.is_tradeable
        assert not BookState.STOPPED.is_tradeable
        assert not BookState.BLOCKED.is_tradeable
        assert not BookState.PREFLIGHT_FAILED.is_tradeable

    def test_all_states_have_transitions(self):
        for state in BookState:
            assert state in VALID_TRANSITIONS, f"Missing transitions for {state}"


class TestBookRuntime:
    def test_start_live(self):
        rt = _make_runtime(mode="live_allowed")
        assert rt.start()
        assert rt.state == BookState.RUNNING_LIVE

    def test_start_paper(self):
        rt = _make_runtime(mode="paper_only")
        assert rt.start()
        assert rt.state == BookState.RUNNING_PAPER

    def test_start_disabled_refused(self):
        rt = _make_runtime(mode="disabled")
        assert not rt.start()
        assert rt.state == BookState.STOPPED

    def test_preflight_pass(self):
        rt = _make_runtime(preflight_fn=lambda: (True, "ok"))
        assert rt.start()
        assert rt.state == BookState.RUNNING_LIVE

    def test_preflight_fail(self):
        rt = _make_runtime(preflight_fn=lambda: (False, "no broker"))
        assert not rt.start()
        assert rt.state == BookState.PREFLIGHT_FAILED

    def test_preflight_exception(self):
        def boom():
            raise ConnectionError("broker down")
        rt = _make_runtime(preflight_fn=boom)
        assert not rt.start()
        assert rt.state == BookState.PREFLIGHT_FAILED

    def test_stop(self):
        rt = _make_runtime()
        rt.start()
        assert rt.stop()
        assert rt.state == BookState.STOPPED
        assert rt.uptime_seconds == 0.0

    def test_block_and_unblock(self):
        rt = _make_runtime()
        rt.start()
        assert rt.block("test kill")
        assert rt.state == BookState.BLOCKED
        assert not rt.state.is_tradeable
        assert rt.unblock()
        assert rt.state == BookState.RUNNING_LIVE

    def test_block_sends_alert(self):
        alert = MagicMock()
        rt = _make_runtime(alert_fn=alert)
        rt.start()
        rt.block("margin call")
        alert.assert_called_once()
        assert "BLOCKED" in alert.call_args[0][1]

    def test_run_cycle_ok(self):
        rt = _make_runtime(cycles={"main": lambda: {"trades": 2}})
        rt.start()
        result = rt.run_cycle("main")
        assert result["status"] == "ok"
        assert result["result"]["trades"] == 2
        assert rt._error_counts.get("main", 0) == 0

    def test_run_cycle_skipped_when_stopped(self):
        rt = _make_runtime()
        result = rt.run_cycle("main")
        assert result["status"] == "skipped"

    def test_run_cycle_error_isolation(self):
        def crash():
            raise ValueError("bad data")
        rt = _make_runtime(cycles={"crash_cycle": crash})
        rt.start()
        result = rt.run_cycle("crash_cycle")
        assert result["status"] == "error"
        assert rt.state == BookState.RUNNING_LIVE  # still alive

    def test_run_cycle_auto_block_after_5_failures(self):
        call_count = 0
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"fail #{call_count}")

        rt = _make_runtime(cycles={"bad": always_fail})
        rt.start()
        for _ in range(5):
            rt.run_cycle("bad")
        assert rt.state == BookState.BLOCKED
        assert "5x" in rt._blocked_reason

    def test_reconcile(self):
        rt = _make_runtime(reconcile_fn=lambda: {"divergences": 0})
        rt.start()
        result = rt.reconcile()
        assert result["status"] == "ok"
        assert rt.state == BookState.RUNNING_LIVE

    def test_reconcile_error_returns_to_previous_state(self):
        def bad_reconcile():
            raise IOError("broker timeout")
        rt = _make_runtime(reconcile_fn=bad_reconcile)
        rt.start()
        result = rt.reconcile()
        assert result["status"] == "error"

    def test_get_status(self):
        rt = _make_runtime()
        rt.start()
        status = rt.get_status()
        assert status["book_id"] == "test_book"
        assert status["state"] == "RUNNING_LIVE"
        assert status["mode"] == "live_allowed"

    def test_invalid_transition_rejected(self):
        rt = _make_runtime()
        assert not rt._transition(BookState.RUNNING_LIVE, "impossible from STOPPED")
        assert rt.state == BookState.STOPPED

    def test_state_history_bounded(self):
        rt = _make_runtime()
        for _ in range(120):
            rt.start()
            rt.stop()
        assert len(rt._state_history) <= 100


class TestBookSupervisor:
    def test_register_and_start_all(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("book_a"))
        sv.register(_make_runtime("book_b", mode="paper_only"))
        results = sv.start_all()
        assert results["book_a"] is True
        assert results["book_b"] is True
        assert sv.get("book_a").state == BookState.RUNNING_LIVE
        assert sv.get("book_b").state == BookState.RUNNING_PAPER

    def test_stop_single_book(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("a"))
        sv.register(_make_runtime("b"))
        sv.start_all()
        sv.stop_book("a", "test")
        assert sv.get("a").state == BookState.STOPPED
        assert sv.get("b").state == BookState.RUNNING_LIVE

    def test_global_kill(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("a"))
        sv.register(_make_runtime("b"))
        sv.start_all()
        sv.global_kill("emergency")
        assert sv.get("a").state == BookState.BLOCKED
        assert sv.get("b").state == BookState.BLOCKED

    def test_global_status(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("a"))
        sv.register(_make_runtime("b", mode="disabled"))
        sv.start_all()
        status = sv.get_global_status()
        assert status["summary"]["live"] == 1
        assert status["summary"]["total"] == 2

    def test_compact_display(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("crypto"))
        sv.register(_make_runtime("futures", mode="paper_only"))
        sv.start_all()
        display = sv.get_compact_display()
        assert "crypto" in display
        assert "futures" in display

    def test_run_cycle_on_book(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("x", cycles={"go": lambda: 42}))
        sv.start_all()
        result = sv.run_cycle("x", "go")
        assert result["status"] == "ok"

    def test_run_cycle_unknown_book(self):
        sv = BookSupervisor()
        result = sv.run_cycle("nonexistent", "main")
        assert result["status"] == "error"

    def test_isolation_one_book_crash_doesnt_affect_other(self):
        sv = BookSupervisor()
        sv.register(_make_runtime("good", cycles={"main": lambda: "ok"}))
        sv.register(_make_runtime("bad", cycles={"main": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}))
        sv.start_all()
        sv.run_cycle("bad", "main")
        assert sv.get("good").state == BookState.RUNNING_LIVE
        result = sv.run_cycle("good", "main")
        assert result["status"] == "ok"
