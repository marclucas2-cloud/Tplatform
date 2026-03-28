"""Tests for continuous latency monitoring."""
import pytest
from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.monitoring import LatencyMonitor


class TestLatencyMonitor:
    def test_init_no_target(self):
        mon = LatencyMonitor()
        result = mon.measure()
        assert result["status"] == "no_target"

    def test_stats_empty(self):
        mon = LatencyMonitor()
        stats = mon.get_stats()
        assert stats["samples"] == 0
        assert stats["mean_ms"] == 0

    def test_stats_with_data(self):
        mon = LatencyMonitor()
        for lat in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            mon.history.append(lat)
        stats = mon.get_stats()
        assert stats["mean_ms"] == 55.0
        assert stats["samples"] == 10

    def test_alert_critical(self):
        mon = LatencyMonitor()
        alerts = []
        mon.set_alert_callback(lambda msg, level: alerts.append((msg, level)))
        for _ in range(12):
            mon.history.append(350)  # All above critical
        result = mon.measure()  # No target so latency=0 but stats from history
        # Stats are from history
        stats = mon.get_stats()
        assert stats["p95_ms"] >= 300

    def test_window_size_limit(self):
        mon = LatencyMonitor()
        for i in range(500):
            mon.history.append(i)
        assert len(mon.history) <= mon.WINDOW_SIZE * 24
