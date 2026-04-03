"""Tests for MetricsCollector (R2-01)."""

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

from core.monitoring.metrics_pipeline import MetricsCollector


@pytest.fixture
def mc(tmp_path):
    db_path = str(tmp_path / "test_metrics.db")
    collector = MetricsCollector(db_path=db_path)
    yield collector


class TestMetricsEmit:
    def test_emit_and_flush(self, mc):
        mc.emit("test.metric", 42.0)
        mc.flush()
        assert mc.total_emitted == 1

    def test_emit_with_tags(self, mc):
        mc.emit("cycle.crypto.duration", 2.3, tags={"broker": "binance"})
        mc.flush()
        result = mc.query("cycle.crypto.duration", hours=1)
        assert result == 2.3

    def test_emit_multiple(self, mc):
        for i in range(10):
            mc.emit("test.counter", float(i))
        mc.flush()
        assert mc.total_emitted == 10


class TestMetricsQuery:
    def test_query_avg(self, mc):
        mc.emit("test.metric", 10.0)
        mc.emit("test.metric", 20.0)
        mc.emit("test.metric", 30.0)
        mc.flush()
        avg = mc.query("test.metric", hours=1, aggregation="avg")
        assert avg == 20.0

    def test_query_sum(self, mc):
        mc.emit("test.metric", 10.0)
        mc.emit("test.metric", 20.0)
        mc.flush()
        total = mc.query("test.metric", hours=1, aggregation="sum")
        assert total == 30.0

    def test_query_min(self, mc):
        mc.emit("test.metric", 10.0)
        mc.emit("test.metric", 5.0)
        mc.emit("test.metric", 20.0)
        mc.flush()
        minimum = mc.query("test.metric", hours=1, aggregation="min")
        assert minimum == 5.0

    def test_query_max(self, mc):
        mc.emit("test.metric", 10.0)
        mc.emit("test.metric", 5.0)
        mc.emit("test.metric", 20.0)
        mc.flush()
        maximum = mc.query("test.metric", hours=1, aggregation="max")
        assert maximum == 20.0

    def test_query_count(self, mc):
        for i in range(5):
            mc.emit("test.metric", float(i))
        mc.flush()
        count = mc.query("test.metric", hours=1, aggregation="count")
        assert count == 5

    def test_query_nonexistent(self, mc):
        result = mc.query("nonexistent.metric", hours=1)
        assert result == 0.0

    def test_query_invalid_aggregation(self, mc):
        with pytest.raises(ValueError, match="Invalid aggregation"):
            mc.query("test", aggregation="median")


class TestMetricsQuerySeries:
    def test_query_series(self, mc):
        for i in range(5):
            mc.emit("series.test", float(i))
        mc.flush()
        series = mc.query_series("series.test", hours=1)
        assert len(series) == 5

    def test_query_series_limit(self, mc):
        for i in range(20):
            mc.emit("series.test", float(i))
        mc.flush()
        series = mc.query_series("series.test", hours=1, limit=5)
        assert len(series) == 5


class TestMetricsLatest:
    def test_query_latest(self, mc):
        mc.emit("test.metric", 1.0)
        mc.emit("test.metric", 2.0)
        mc.emit("test.metric", 3.0)
        mc.flush()
        latest = mc.query_latest("test.metric")
        assert latest is not None
        assert latest[1] == 3.0

    def test_query_latest_nonexistent(self, mc):
        result = mc.query_latest("nonexistent")
        assert result is None


class TestMetricsAutoFlush:
    def test_auto_flush_on_buffer_full(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mc = MetricsCollector(db_path=db_path)
        # Emit 100 metrics to trigger auto-flush
        for i in range(100):
            mc.emit("auto.flush.test", float(i))
        # Should have auto-flushed
        result = mc.query("auto.flush.test", hours=1, aggregation="count")
        assert result == 100


class TestMetricsPurge:
    def test_purge_old(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mc = MetricsCollector(db_path=db_path)
        # Insert a metric with old timestamp directly
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO metrics (timestamp, name, value) "
            "VALUES ('2020-01-01T00:00:00', 'old.metric', 1.0)"
        )
        conn.commit()
        conn.close()

        mc.emit("new.metric", 2.0)
        mc.flush()

        deleted = mc.purge_old(retention_days=30)
        assert deleted == 1

        # New metric should still exist
        result = mc.query("new.metric", hours=1)
        assert result == 2.0

        # Old metric should be gone
        result = mc.query("old.metric", hours=876000)  # 100 years
        assert result == 0.0


class TestMetricsDBInit:
    def test_creates_db_and_index(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mc = MetricsCollector(db_path=db_path)
        assert Path(db_path).exists()

        conn = sqlite3.connect(db_path)
        # Check table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'"
        )
        assert cursor.fetchone() is not None

        # Check index exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_metrics_name_ts'"
        )
        assert cursor.fetchone() is not None
        conn.close()


class TestMetricsThreadSafety:
    def test_concurrent_emit(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mc = MetricsCollector(db_path=db_path)
        errors = []

        def emitter(thread_id):
            try:
                for i in range(50):
                    mc.emit(f"thread.{thread_id}.metric", float(i))
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=emitter, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        mc.flush()

        assert len(errors) == 0
        assert mc.total_emitted == 250

    def test_concurrent_emit_and_query(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mc = MetricsCollector(db_path=db_path)
        errors = []

        def emitter():
            try:
                for i in range(100):
                    mc.emit("concurrent.test", float(i))
                    if i % 10 == 0:
                        mc.flush()
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    mc.query("concurrent.test", hours=1)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=emitter),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(errors) == 0


class TestMetricsNaming:
    """Verify metric naming convention works correctly."""

    def test_hierarchical_names(self, mc):
        mc.emit("cycle.crypto.duration_seconds", 2.3)
        mc.emit("broker.binance.latency_ms", 45)
        mc.emit("risk.dd.global_pct", 1.2)
        mc.emit("system.cpu.percent", 23)
        mc.emit("queue.depth", 3)
        mc.flush()

        assert mc.query("cycle.crypto.duration_seconds", hours=1) == 2.3
        assert mc.query("broker.binance.latency_ms", hours=1) == 45
        assert mc.query("risk.dd.global_pct", hours=1) == 1.2
        assert mc.query("system.cpu.percent", hours=1) == 23
        assert mc.query("queue.depth", hours=1) == 3
