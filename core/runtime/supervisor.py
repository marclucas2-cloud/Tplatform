"""BookSupervisor — lightweight orchestrator replacing mega-worker pattern.

Manages BookRuntime instances with independent lifecycle.
Each book can be started, stopped, blocked independently.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from core.runtime.book_runtime import BookRuntime, BookState

logger = logging.getLogger("supervisor")


class BookSupervisor:
    """Lightweight supervisor for all book runtimes.

    Responsibilities:
    - Registry of all book runtimes
    - Start/stop individual books
    - Global kill (stop all)
    - Status aggregation for dashboard
    - Health monitoring (block books on kill switch / health failure)
    """

    def __init__(self) -> None:
        self._books: dict[str, BookRuntime] = {}
        self._lock = threading.Lock()
        self._started_at: datetime | None = None

    def register(self, runtime: BookRuntime) -> None:
        with self._lock:
            self._books[runtime.book_id] = runtime
            logger.info("Registered book: %s (%s)", runtime.book_id, runtime.broker)

    def get(self, book_id: str) -> BookRuntime | None:
        return self._books.get(book_id)

    @property
    def all_books(self) -> dict[str, BookRuntime]:
        return dict(self._books)

    def start_all(self) -> dict[str, bool]:
        """Start all registered books. Returns {book_id: success}."""
        self._started_at = datetime.now(timezone.utc)
        results = {}
        for book_id, runtime in self._books.items():
            try:
                results[book_id] = runtime.start()
            except Exception as e:
                logger.error("Failed to start %s: %s", book_id, e)
                results[book_id] = False
        live = sum(1 for v in results.values() if v)
        logger.info("Supervisor started: %d/%d books alive", live, len(results))
        return results

    def start_book(self, book_id: str) -> bool:
        runtime = self._books.get(book_id)
        if runtime is None:
            logger.error("Book %s not registered", book_id)
            return False
        return runtime.start()

    def stop_book(self, book_id: str, reason: str = "operator") -> bool:
        runtime = self._books.get(book_id)
        if runtime is None:
            return False
        return runtime.stop(reason)

    def stop_all(self, reason: str = "global shutdown") -> None:
        logger.info("Stopping all books: %s", reason)
        for book_id, runtime in self._books.items():
            try:
                runtime.stop(reason)
            except Exception as e:
                logger.error("Error stopping %s: %s", book_id, e)

    def block_book(self, book_id: str, reason: str) -> bool:
        runtime = self._books.get(book_id)
        if runtime is None:
            return False
        return runtime.block(reason)

    def global_kill(self, reason: str = "emergency") -> None:
        """Emergency stop all books immediately."""
        logger.critical("GLOBAL KILL: %s", reason)
        for runtime in self._books.values():
            try:
                runtime.block(f"global_kill: {reason}")
            except Exception:
                pass

    def check_health(self) -> dict[str, dict]:
        """Check health of all books, block unhealthy ones."""
        try:
            from core.governance.kill_switches_scoped import is_killed
        except ImportError:
            return {}

        results = {}
        for book_id, runtime in self._books.items():
            if not runtime.is_alive:
                results[book_id] = {"action": "skip", "reason": "not alive"}
                continue

            killed, kill_reason = is_killed(book_id=book_id)
            if killed and runtime.state not in (BookState.BLOCKED, BookState.STOPPED):
                runtime.block(f"kill_switch: {kill_reason}")
                results[book_id] = {"action": "blocked", "reason": kill_reason}
            elif not killed and runtime.state == BookState.BLOCKED:
                if "kill_switch" in runtime._blocked_reason:
                    runtime.unblock()
                    results[book_id] = {"action": "unblocked"}
            else:
                results[book_id] = {"action": "ok"}

        return results

    def run_cycle(self, book_id: str, cycle_name: str) -> dict:
        """Run a specific cycle on a specific book."""
        runtime = self._books.get(book_id)
        if runtime is None:
            return {"status": "error", "reason": f"book {book_id} not registered"}
        return runtime.run_cycle(cycle_name)

    def get_global_status(self) -> dict[str, Any]:
        """Aggregated status for all books."""
        book_statuses = {}
        for book_id, runtime in self._books.items():
            book_statuses[book_id] = runtime.get_status()

        states = [r.state for r in self._books.values()]
        if all(s == BookState.STOPPED for s in states):
            global_state = "STOPPED"
        elif any(s == BookState.BLOCKED for s in states):
            global_state = "DEGRADED"
        elif all(s.is_tradeable for s in states if s != BookState.STOPPED):
            global_state = "HEALTHY"
        else:
            global_state = "MIXED"

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "global_state": global_state,
            "uptime_s": (
                (datetime.now(timezone.utc) - self._started_at).total_seconds()
                if self._started_at else 0
            ),
            "books": book_statuses,
            "summary": {
                "total": len(self._books),
                "alive": sum(1 for r in self._books.values() if r.is_alive),
                "blocked": sum(1 for r in self._books.values() if r.state == BookState.BLOCKED),
                "live": sum(1 for r in self._books.values() if r.state == BookState.RUNNING_LIVE),
                "paper": sum(1 for r in self._books.values() if r.state == BookState.RUNNING_PAPER),
            },
        }

    def get_compact_display(self) -> str:
        """One-line-per-book display for CLI / Telegram."""
        lines = []
        for book_id, runtime in self._books.items():
            s = runtime.state
            indicator = {
                BookState.RUNNING_LIVE: "🟢",
                BookState.RUNNING_PAPER: "🔵",
                BookState.BLOCKED: "🔴",
                BookState.DEGRADED: "🟡",
                BookState.STOPPED: "⚫",
            }.get(s, "⚪")
            cycles_ok = sum(1 for v in runtime._cycle_stats.values() if v.get("status") == "ok")
            cycles_total = len(runtime._cycle_stats)
            line = f"{indicator} {book_id}: {s.value}"
            if runtime._blocked_reason:
                line += f" ({runtime._blocked_reason})"
            elif cycles_total > 0:
                line += f" ({cycles_ok}/{cycles_total} cycles ok)"
            lines.append(line)
        return "\n".join(lines) if lines else "No books registered"
