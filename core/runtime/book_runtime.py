"""BookRuntime — interface + state machine for per-book isolation.

Each book (binance_crypto, ibkr_futures, ibkr_fx, ibkr_eu, alpaca_us)
gets its own BookRuntime instance with independent lifecycle.

State machine:
    STOPPED → STARTING → PREFLIGHT → RUNNING_PAPER / RUNNING_LIVE
    Any state → BLOCKED / DEGRADED (from health/kill checks)
    Any state → STOPPING → STOPPED
"""
from __future__ import annotations

import enum
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("book_runtime")


class BookState(enum.Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    PREFLIGHT = "PREFLIGHT"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    RUNNING_PAPER = "RUNNING_PAPER"
    RUNNING_LIVE = "RUNNING_LIVE"
    BLOCKED = "BLOCKED"
    DEGRADED = "DEGRADED"
    RECONCILING = "RECONCILING"
    STOPPING = "STOPPING"

    @property
    def is_tradeable(self) -> bool:
        return self in (BookState.RUNNING_LIVE, BookState.RUNNING_PAPER, BookState.DEGRADED)


VALID_TRANSITIONS: dict[BookState, set[BookState]] = {
    BookState.STOPPED: {BookState.STARTING},
    BookState.STARTING: {BookState.PREFLIGHT, BookState.BLOCKED, BookState.STOPPED},
    BookState.PREFLIGHT: {
        BookState.RUNNING_PAPER, BookState.RUNNING_LIVE,
        BookState.PREFLIGHT_FAILED, BookState.BLOCKED,
    },
    BookState.PREFLIGHT_FAILED: {BookState.STOPPED, BookState.STARTING},
    BookState.RUNNING_PAPER: {
        BookState.STOPPING, BookState.BLOCKED, BookState.DEGRADED, BookState.RECONCILING,
    },
    BookState.RUNNING_LIVE: {
        BookState.STOPPING, BookState.BLOCKED, BookState.DEGRADED, BookState.RECONCILING,
    },
    BookState.BLOCKED: {
        BookState.STOPPING, BookState.STARTING, BookState.STOPPED,
        BookState.RUNNING_LIVE, BookState.RUNNING_PAPER,
    },
    BookState.DEGRADED: {
        BookState.RUNNING_PAPER, BookState.RUNNING_LIVE,
        BookState.BLOCKED, BookState.STOPPING,
    },
    BookState.RECONCILING: {
        BookState.RUNNING_PAPER, BookState.RUNNING_LIVE,
        BookState.BLOCKED, BookState.STOPPING,
    },
    BookState.STOPPING: {BookState.STOPPED},
}


class BookRuntime:
    """Lifecycle wrapper for a single book.

    Wraps existing cycle functions with:
    - Independent state machine
    - Start/stop control
    - Health checks per cycle
    - Error isolation (one book crash doesn't affect others)
    """

    def __init__(
        self,
        book_id: str,
        broker: str,
        mode: str,
        cycles: dict[str, Callable],
        preflight_fn: Callable[[], tuple[bool, str]] | None = None,
        reconcile_fn: Callable[[], dict] | None = None,
        alert_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self.book_id = book_id
        self.broker = broker
        self.mode = mode  # disabled / paper_only / live_allowed
        self._cycles = cycles
        self._preflight_fn = preflight_fn
        self._reconcile_fn = reconcile_fn
        self._alert_fn = alert_fn

        self._state = BookState.STOPPED
        self._state_lock = threading.Lock()
        self._state_history: list[tuple[datetime, BookState, str]] = []
        self._cycle_stats: dict[str, dict[str, Any]] = {}
        self._last_cycle_ts: dict[str, float] = {}
        self._error_counts: dict[str, int] = {}
        self._blocked_reason: str = ""
        self._started_at: datetime | None = None

    @property
    def state(self) -> BookState:
        return self._state

    @property
    def is_alive(self) -> bool:
        return self._state not in (BookState.STOPPED, BookState.PREFLIGHT_FAILED)

    @property
    def uptime_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return (datetime.now(timezone.utc) - self._started_at).total_seconds()

    def _transition(self, new_state: BookState, reason: str = "") -> bool:
        with self._state_lock:
            if new_state not in VALID_TRANSITIONS.get(self._state, set()):
                logger.warning(
                    "[%s] Invalid transition %s → %s (reason: %s)",
                    self.book_id, self._state.value, new_state.value, reason,
                )
                return False
            old = self._state
            self._state = new_state
            now = datetime.now(timezone.utc)
            self._state_history.append((now, new_state, reason))
            if len(self._state_history) > 100:
                self._state_history = self._state_history[-50:]
            logger.info(
                "[%s] %s → %s%s",
                self.book_id, old.value, new_state.value,
                f" ({reason})" if reason else "",
            )
            return True

    def start(self) -> bool:
        if self.mode == "disabled":
            logger.info("[%s] Book disabled, not starting", self.book_id)
            return False

        if not self._transition(BookState.STARTING, "start requested"):
            return False

        self._started_at = datetime.now(timezone.utc)

        if not self._transition(BookState.PREFLIGHT, "running preflight"):
            return False

        if self._preflight_fn:
            try:
                ok, detail = self._preflight_fn()
                if not ok:
                    self._transition(BookState.PREFLIGHT_FAILED, detail)
                    if self._alert_fn:
                        self._alert_fn("WARN", f"[{self.book_id}] Preflight failed: {detail}")
                    return False
            except Exception as e:
                self._transition(BookState.PREFLIGHT_FAILED, str(e))
                return False

        target = BookState.RUNNING_LIVE if self.mode == "live_allowed" else BookState.RUNNING_PAPER
        self._transition(target, "preflight passed")
        return True

    def stop(self, reason: str = "operator") -> bool:
        if self._state == BookState.STOPPED:
            return True
        self._transition(BookState.STOPPING, reason)
        self._transition(BookState.STOPPED, "clean shutdown")
        self._started_at = None
        return True

    def block(self, reason: str) -> bool:
        self._blocked_reason = reason
        if self._alert_fn:
            self._alert_fn("CRITICAL", f"[{self.book_id}] BLOCKED: {reason}")
        return self._transition(BookState.BLOCKED, reason)

    def unblock(self) -> bool:
        if self._state != BookState.BLOCKED:
            return False
        target = BookState.RUNNING_LIVE if self.mode == "live_allowed" else BookState.RUNNING_PAPER
        self._blocked_reason = ""
        return self._transition(target, "unblocked")

    def run_cycle(self, cycle_name: str) -> dict:
        """Execute a named cycle with error isolation."""
        if not self._state.is_tradeable:
            return {"status": "skipped", "reason": f"book state {self._state.value}"}

        fn = self._cycles.get(cycle_name)
        if fn is None:
            return {"status": "error", "reason": f"unknown cycle {cycle_name}"}

        t0 = time.time()
        try:
            result = fn()
            elapsed = time.time() - t0
            self._last_cycle_ts[cycle_name] = time.time()
            self._error_counts[cycle_name] = 0
            self._cycle_stats[cycle_name] = {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(elapsed, 2),
                "status": "ok",
            }
            return {"status": "ok", "duration_s": round(elapsed, 2), "result": result}
        except Exception as e:
            elapsed = time.time() - t0
            self._error_counts[cycle_name] = self._error_counts.get(cycle_name, 0) + 1
            self._cycle_stats[cycle_name] = {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(elapsed, 2),
                "status": "error",
                "error": str(e),
            }
            consec = self._error_counts[cycle_name]
            if consec >= 5:
                self.block(f"cycle {cycle_name} failed {consec}x consecutively")
            elif consec >= 3 and self._alert_fn:
                self._alert_fn("WARN", f"[{self.book_id}] {cycle_name} failed {consec}x: {e}")
            logger.error("[%s] Cycle %s error: %s", self.book_id, cycle_name, e)
            return {"status": "error", "duration_s": round(elapsed, 2), "error": str(e)}

    def reconcile(self) -> dict:
        """Run reconciliation for this book."""
        if self._reconcile_fn is None:
            return {"status": "no_reconcile_fn"}
        old_state = self._state
        self._transition(BookState.RECONCILING, "scheduled reconciliation")
        try:
            result = self._reconcile_fn()
            self._transition(
                BookState.RUNNING_LIVE if self.mode == "live_allowed" else BookState.RUNNING_PAPER,
                "reconciliation complete",
            )
            return {"status": "ok", "result": result}
        except Exception as e:
            logger.error("[%s] Reconciliation error: %s", self.book_id, e)
            if old_state in VALID_TRANSITIONS.get(BookState.RECONCILING, set()):
                self._transition(old_state, f"reconciliation failed: {e}")
            return {"status": "error", "error": str(e)}

    def get_status(self) -> dict:
        """Compact status for operator dashboard."""
        return {
            "book_id": self.book_id,
            "broker": self.broker,
            "mode": self.mode,
            "state": self._state.value,
            "blocked_reason": self._blocked_reason,
            "uptime_s": round(self.uptime_seconds),
            "cycles": dict(self._cycle_stats),
            "error_counts": dict(self._error_counts),
            "last_transitions": [
                {"ts": ts.isoformat(), "state": s.value, "reason": r}
                for ts, s, r in self._state_history[-5:]
            ],
        }
