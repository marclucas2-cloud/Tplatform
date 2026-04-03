"""Deterministic event logger for post-mortem debugging and replay.

Each cycle logs its complete input AND output.
Format: JSONL (one line per event), one file per day.
File: data/events/events_2026-04-03.jsonl

Events are:
  - append-only (never modified after write)
  - async write (doesn't block the cycle)
  - REPLAY-COMPATIBLE (see replay_engine.py)

Retention: 30 days (auto-purge).
"""

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("worker.event_logger")

ROOT = Path(__file__).resolve().parent.parent.parent


class EventLogger:
    """Thread-safe deterministic event logger with daily file rotation."""

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = Path(base_dir) if base_dir else ROOT / "data" / "events"
        self._lock = threading.Lock()
        self._current_date: Optional[date] = None
        self._file = None
        self._event_count = 0
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        cycle_name: str,
        event_type: str,
        data: dict[str, Any],
        input_snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log an event. Thread-safe, immediate flush."""
        event = {
            "ts": datetime.now().isoformat(timespec="microseconds"),
            "cycle": cycle_name,
            "type": event_type,
            "data": data,
        }
        if input_snapshot:
            event["snapshot"] = input_snapshot

        line = json.dumps(event, default=str) + "\n"

        with self._lock:
            self._ensure_file()
            try:
                self._file.write(line)
                self._file.flush()
                self._event_count += 1
            except Exception as e:
                logger.error(f"EventLogger write error: {e}")

    def log_cycle_start(
        self,
        cycle_name: str,
        snapshot: dict[str, Any],
    ) -> None:
        """Convenience: log a CYCLE_START with full input snapshot."""
        self.log(cycle_name, "CYCLE_START", {}, input_snapshot=snapshot)

    def log_cycle_end(
        self,
        cycle_name: str,
        output: dict[str, Any],
        duration_ms: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Convenience: log a CYCLE_END with output and metrics."""
        data = {
            "output": output,
            "duration_ms": round(duration_ms, 2),
            "success": success,
        }
        if error:
            data["error"] = error
        self.log(cycle_name, "CYCLE_END", data)

    def log_signal(
        self,
        cycle_name: str,
        signal: dict[str, Any],
    ) -> None:
        """Log a trading signal."""
        self.log(cycle_name, "SIGNAL", signal)

    def log_order(
        self,
        cycle_name: str,
        order: dict[str, Any],
    ) -> None:
        """Log an order submission/fill/cancel."""
        self.log(cycle_name, "ORDER", order)

    def log_error(
        self,
        cycle_name: str,
        error: str,
        context: Optional[dict] = None,
    ) -> None:
        """Log an error event."""
        self.log(cycle_name, "ERROR", {
            "error": error,
            **(context or {}),
        })

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def current_file(self) -> Optional[str]:
        if self._file and not self._file.closed:
            return self._file.name
        return None

    def purge_old(self, retention_days: int = 30) -> int:
        """Delete event files older than retention_days. Returns count deleted."""
        cutoff = date.today() - timedelta(days=retention_days)
        deleted = 0
        for f in self._base_dir.glob("events_*.jsonl"):
            try:
                file_date_str = f.stem.replace("events_", "")
                file_date = date.fromisoformat(file_date_str)
                if file_date < cutoff:
                    f.unlink()
                    deleted += 1
                    logger.info(f"Purged old events file: {f.name}")
            except (ValueError, OSError):
                continue
        return deleted

    def _ensure_file(self) -> None:
        """Ensure we're writing to today's file. Rotate if needed."""
        today = date.today()
        if self._current_date != today:
            if self._file and not self._file.closed:
                self._file.close()
            filename = f"events_{today.isoformat()}.jsonl"
            filepath = self._base_dir / filename
            self._file = open(filepath, "a", encoding="utf-8")
            self._current_date = today

    def close(self) -> None:
        """Close the current file handle."""
        with self._lock:
            if self._file and not self._file.closed:
                self._file.close()
                self._file = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# Module-level singleton
_event_logger: Optional[EventLogger] = None
_init_lock = threading.Lock()


def get_event_logger() -> EventLogger:
    """Get or create the global EventLogger singleton."""
    global _event_logger
    if _event_logger is None:
        with _init_lock:
            if _event_logger is None:
                _event_logger = EventLogger()
    return _event_logger
