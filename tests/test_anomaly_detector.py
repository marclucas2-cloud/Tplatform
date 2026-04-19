"""AnomalyDetector regression tests (Phase 12 XXL).

Validates threshold / trend / absence detection + cooldown + alert wiring.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from core.monitoring.anomaly_detector import (
    AlertLevel,
    AnomalyDetector,
    AnomalyRule,
)


class _FakeMetrics:
    """Minimal MetricsCollector stub for tests."""

    def __init__(self):
        self._latest: dict[str, tuple[str, float]] = {}
        self._aggregates: dict[tuple[str, int, str], float] = {}

    def set_latest(self, name: str, value: float, ts: datetime | None = None):
        ts = ts or datetime.now()
        self._latest[name] = (ts.isoformat(), value)

    def query_latest(self, name: str) -> tuple[str, float] | None:
        return self._latest.get(name)

    def set_aggregate(self, name: str, hours: int, agg: str, value: float):
        self._aggregates[(name, hours, agg)] = value

    def query(self, name: str, hours: int, aggregation: str) -> float:
        return self._aggregates.get((name, hours, aggregation), 0.0)


# ---------------------------------------------------------------------------
# THRESHOLD detection
# ---------------------------------------------------------------------------

class TestThresholdDetection:
    def test_threshold_max_exceeded_fires(self):
        m = _FakeMetrics()
        m.set_latest("cycle.crypto.duration_seconds", 100.0)
        rule = AnomalyRule(
            "cycle.crypto.duration_seconds", "threshold",
            AlertLevel.CRITICAL, threshold_max=60,
        )
        d = AnomalyDetector(m, rules=[rule])
        out = d.check_all()
        assert len(out) == 1
        assert "100.0" in out[0].message
        assert "60" in out[0].message

    def test_threshold_max_not_exceeded(self):
        m = _FakeMetrics()
        m.set_latest("metric", 30.0)
        rule = AnomalyRule("metric", "threshold", AlertLevel.WARN, threshold_max=60)
        d = AnomalyDetector(m, rules=[rule])
        assert d.check_all() == []

    def test_threshold_min_undershot_fires(self):
        m = _FakeMetrics()
        m.set_latest("battery", 5.0)
        rule = AnomalyRule("battery", "threshold", AlertLevel.WARN, threshold_min=10)
        d = AnomalyDetector(m, rules=[rule])
        out = d.check_all()
        assert len(out) == 1

    def test_no_metric_no_anomaly(self):
        m = _FakeMetrics()
        rule = AnomalyRule("never_emitted", "threshold", AlertLevel.WARN, threshold_max=10)
        d = AnomalyDetector(m, rules=[rule])
        assert d.check_all() == []


# ---------------------------------------------------------------------------
# TREND detection
# ---------------------------------------------------------------------------

class TestTrendDetection:
    def test_trend_critical_when_short_2x_long(self):
        m = _FakeMetrics()
        m.set_aggregate("cycle.x.duration", 24, "avg", 100.0)
        m.set_aggregate("cycle.x.duration", 168, "avg", 50.0)
        rule = AnomalyRule(
            "cycle.x.duration", "trend", AlertLevel.CRITICAL,
            trend_ratio_warn=1.5, trend_ratio_crit=2.0,
        )
        d = AnomalyDetector(m, rules=[rule])
        out = d.check_all()
        assert len(out) == 1
        assert "CRITICAL" in out[0].message

    def test_trend_warn_when_short_1_5x_long(self):
        m = _FakeMetrics()
        m.set_aggregate("cycle.x.duration", 24, "avg", 75.0)
        m.set_aggregate("cycle.x.duration", 168, "avg", 50.0)
        rule = AnomalyRule(
            "cycle.x.duration", "trend", AlertLevel.WARN,
            trend_ratio_warn=1.5, trend_ratio_crit=2.0,
        )
        d = AnomalyDetector(m, rules=[rule])
        out = d.check_all()
        assert len(out) == 1
        assert "WARN" in out[0].message

    def test_trend_no_alert_when_below_threshold(self):
        m = _FakeMetrics()
        m.set_aggregate("metric", 24, "avg", 60.0)
        m.set_aggregate("metric", 168, "avg", 50.0)
        rule = AnomalyRule("metric", "trend", AlertLevel.WARN, trend_ratio_warn=1.5)
        d = AnomalyDetector(m, rules=[rule])
        assert d.check_all() == []


# ---------------------------------------------------------------------------
# ABSENCE detection
# ---------------------------------------------------------------------------

class TestAbsenceDetection:
    def test_absence_fires_when_silent_too_long(self):
        m = _FakeMetrics()
        # Last seen 60 minutes ago
        m.set_latest("cycle.crypto.duration", 5.0, ts=datetime.now() - timedelta(minutes=60))
        rule = AnomalyRule(
            "cycle.crypto.duration", "absence", AlertLevel.CRITICAL,
            max_silence_minutes=30,
        )
        d = AnomalyDetector(m, rules=[rule])
        out = d.check_all()
        assert len(out) == 1
        assert "ABSENT" in out[0].message

    def test_absence_no_alert_when_recent(self):
        m = _FakeMetrics()
        m.set_latest("metric", 5.0, ts=datetime.now() - timedelta(minutes=5))
        rule = AnomalyRule("metric", "absence", AlertLevel.WARN, max_silence_minutes=30)
        d = AnomalyDetector(m, rules=[rule])
        assert d.check_all() == []

    def test_absence_no_alert_when_never_emitted(self):
        m = _FakeMetrics()
        rule = AnomalyRule("never", "absence", AlertLevel.WARN, max_silence_minutes=10)
        d = AnomalyDetector(m, rules=[rule])
        assert d.check_all() == []  # No metric = not an "absence"


# ---------------------------------------------------------------------------
# Alerting + cooldown
# ---------------------------------------------------------------------------

class TestAlertingCooldown:
    def test_alert_callback_invoked_on_anomaly(self):
        m = _FakeMetrics()
        m.set_latest("metric", 100.0)
        alerts = []
        rule = AnomalyRule("metric", "threshold", AlertLevel.WARN, threshold_max=10)
        d = AnomalyDetector(m, rules=[rule],
                            alert_callback=lambda msg, lvl: alerts.append((msg, lvl)))
        d.check_all()
        assert len(alerts) == 1
        assert alerts[0][1] == "warning"

    def test_cooldown_suppresses_repeated_alerts(self):
        m = _FakeMetrics()
        m.set_latest("metric", 100.0)
        alerts = []
        rule = AnomalyRule(
            "metric", "threshold", AlertLevel.WARN,
            threshold_max=10, cooldown_minutes=60,
        )
        d = AnomalyDetector(m, rules=[rule],
                            alert_callback=lambda msg, lvl: alerts.append((msg, lvl)))
        d.check_all()
        d.check_all()  # within cooldown
        assert len(alerts) == 1  # only first fired

    def test_alert_callback_failure_does_not_break_detection(self):
        m = _FakeMetrics()
        m.set_latest("metric", 100.0)

        def broken_cb(msg, lvl):
            raise RuntimeError("alert system down")

        rule = AnomalyRule("metric", "threshold", AlertLevel.WARN, threshold_max=10)
        d = AnomalyDetector(m, rules=[rule], alert_callback=broken_cb)
        # Should not raise
        out = d.check_all()
        assert len(out) == 1

    def test_recent_anomalies_filtered_by_window(self):
        m = _FakeMetrics()
        m.set_latest("metric", 100.0)
        rule = AnomalyRule("metric", "threshold", AlertLevel.WARN, threshold_max=10)
        d = AnomalyDetector(m, rules=[rule])
        d.check_all()
        recent = d.get_recent_anomalies(hours=24)
        assert len(recent) == 1
