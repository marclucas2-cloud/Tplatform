"""Structured metrics pipeline with SQLite backend.

Each component emits metrics via a thread-safe collector.
Metrics are buffered and flushed to SQLite periodically.

Sources:
  - CycleRunner: duration, success/failure, health per cycle
  - Brokers: API latency, request count, errors
  - Risk: current DD, ERE, regime, Kelly mode
  - Trading: executed trades, slippage, fill rate
  - System: CPU, RAM, disk, worker uptime
  - Queue: depth, wait time, overdue tasks

Naming convention: {domain}.{component}.{metric}
  cycle.crypto.duration_seconds
  broker.binance.latency_ms
  risk.dd.global_pct
  trade.fill.slippage_bps
  system.cpu.percent
  queue.depth

Retention: 90 days (auto-purge).
Storage: ~50MB for 90 days at 1 metric/sec.
"""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monitoring.metrics")

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Metric:
    name: str                          # "cycle.crypto.duration_seconds"
    value: float                       # 2.34
    timestamp: datetime = field(default_factory=datetime.now)
    tags: Optional[dict[str, str]] = None  # {"broker": "binance", "status": "success"}


class MetricsCollector:
    """Thread-safe metrics collector with SQLite backend."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_dir = ROOT / "data" / "metrics"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "metrics.db")
        self._db_path = db_path
        self._buffer: list[Metric] = []
        self._lock = threading.Lock()
        self._flush_interval = 10  # flush every 10 seconds
        self._last_flush = time.monotonic()
        self._total_emitted = 0
        self._init_db()

    def emit(self, name: str, value: float, tags: Optional[dict] = None) -> None:
        """Emit a metric. Thread-safe, buffered."""
        metric = Metric(
            name=name,
            value=value,
            timestamp=datetime.now(),
            tags=tags,
        )
        with self._lock:
            self._buffer.append(metric)
            self._total_emitted += 1
            now = time.monotonic()
            if (
                len(self._buffer) >= 100
                or (now - self._last_flush) >= self._flush_interval
            ):
                self._flush_locked()

    def flush(self) -> None:
        """Force flush buffered metrics to SQLite."""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Write buffered metrics to SQLite. Must hold self._lock."""
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()

        try:
            conn = sqlite3.connect(self._db_path)
            conn.executemany(
                "INSERT INTO metrics (timestamp, name, value, tags) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        m.timestamp.isoformat(),
                        m.name,
                        m.value,
                        json.dumps(m.tags) if m.tags else None,
                    )
                    for m in batch
                ],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Metrics flush error: {e}")
            # Put back unflushed metrics (best effort)
            self._buffer = batch + self._buffer

    def query(
        self,
        name: str,
        hours: int = 24,
        aggregation: str = "avg",
    ) -> float:
        """Query aggregated metric value over a time window."""
        if aggregation not in ("avg", "min", "max", "sum", "count"):
            raise ValueError(f"Invalid aggregation: {aggregation}")

        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                f"SELECT {aggregation}(value) FROM metrics "
                f"WHERE name = ? AND timestamp > ?",
                (name, cutoff),
            )
            result = cursor.fetchone()[0]
            conn.close()
            return result or 0.0
        except Exception as e:
            logger.error(f"Metrics query error: {e}")
            return 0.0

    def query_series(
        self,
        name: str,
        hours: int = 24,
        limit: int = 1000,
    ) -> list[tuple[str, float]]:
        """Query raw metric values as (timestamp, value) pairs."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                "SELECT timestamp, value FROM metrics "
                "WHERE name = ? AND timestamp > ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (name, cutoff, limit),
            )
            rows = cursor.fetchall()
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"Metrics query_series error: {e}")
            return []

    def query_latest(self, name: str) -> Optional[tuple[str, float]]:
        """Get the most recent value for a metric."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                "SELECT timestamp, value FROM metrics "
                "WHERE name = ? ORDER BY timestamp DESC LIMIT 1",
                (name,),
            )
            row = cursor.fetchone()
            conn.close()
            return row
        except Exception:
            return None

    def purge_old(self, retention_days: int = 90) -> int:
        """Delete metrics older than retention_days. Returns count deleted."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                "DELETE FROM metrics WHERE timestamp < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            logger.info(f"Purged {deleted} old metrics (>{retention_days} days)")
            return deleted
        except Exception as e:
            logger.error(f"Metrics purge error: {e}")
            return 0

    @property
    def total_emitted(self) -> int:
        return self._total_emitted

    def _init_db(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    tags TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_name_ts "
                "ON metrics (name, timestamp)"
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Metrics DB init error: {e}")


# Module-level singleton
_metrics: Optional[MetricsCollector] = None
_init_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """Get or create the global MetricsCollector singleton."""
    global _metrics
    if _metrics is None:
        with _init_lock:
            if _metrics is None:
                _metrics = MetricsCollector()
    return _metrics
