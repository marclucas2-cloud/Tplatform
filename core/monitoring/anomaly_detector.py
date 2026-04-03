"""Proactive anomaly detection on metrics.

Three detection methods (no ML — too little data):

1. THRESHOLD: value exceeds a fixed threshold
2. TREND: rolling average significantly higher/lower than long-term
3. ABSENCE: expected metric not emitted for N minutes

Each anomaly triggers a Telegram alert with context.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional

from core.monitoring.metrics_pipeline import MetricsCollector

logger = logging.getLogger("monitoring.anomaly")


class AlertLevel(Enum):
    INFO = "info"
    WARN = "warning"
    CRITICAL = "critical"


@dataclass
class AnomalyRule:
    metric_name: str
    rule_type: str          # "threshold", "trend", "absence"
    level: AlertLevel
    # Threshold params
    threshold_max: Optional[float] = None
    threshold_min: Optional[float] = None
    # Trend params
    short_window_hours: int = 24
    long_window_hours: int = 168   # 7 days
    trend_ratio_warn: float = 1.5
    trend_ratio_crit: float = 2.0
    # Absence params
    max_silence_minutes: int = 30
    # Cooldown (avoid spamming)
    cooldown_minutes: int = 60


@dataclass
class Anomaly:
    rule: AnomalyRule
    detected_at: datetime
    message: str
    current_value: Optional[float] = None
    threshold: Optional[float] = None


# Default rules for the trading platform
DEFAULT_RULES = [
    # --- Cycles ---
    AnomalyRule("cycle.crypto.duration_seconds", "threshold",
                AlertLevel.WARN, threshold_max=30),
    AnomalyRule("cycle.crypto.duration_seconds", "threshold",
                AlertLevel.CRITICAL, threshold_max=60),
    AnomalyRule("cycle.crypto.duration_seconds", "trend",
                AlertLevel.WARN),
    AnomalyRule("cycle.crypto.duration_seconds", "absence",
                AlertLevel.CRITICAL, max_silence_minutes=20),

    AnomalyRule("cycle.fx_carry.duration_seconds", "absence",
                AlertLevel.WARN, max_silence_minutes=1500),  # Daily cycle
    AnomalyRule("cycle.live_risk.duration_seconds", "absence",
                AlertLevel.CRITICAL, max_silence_minutes=10),

    # --- Brokers ---
    AnomalyRule("broker.binance.latency_ms", "threshold",
                AlertLevel.WARN, threshold_max=1000),
    AnomalyRule("broker.binance.latency_ms", "threshold",
                AlertLevel.CRITICAL, threshold_max=5000),
    AnomalyRule("broker.ibkr.reconnections", "threshold",
                AlertLevel.WARN, threshold_max=3),
    AnomalyRule("broker.ibkr.reconnections", "threshold",
                AlertLevel.CRITICAL, threshold_max=10),

    # --- Risk ---
    AnomalyRule("risk.dd.global_pct", "threshold",
                AlertLevel.WARN, threshold_max=3.0),
    AnomalyRule("risk.dd.global_pct", "threshold",
                AlertLevel.CRITICAL, threshold_max=5.0),

    # --- System ---
    AnomalyRule("system.disk.percent", "threshold",
                AlertLevel.WARN, threshold_max=80),
    AnomalyRule("system.disk.percent", "threshold",
                AlertLevel.CRITICAL, threshold_max=90),
    AnomalyRule("system.ram.percent", "threshold",
                AlertLevel.WARN, threshold_max=85),

    # --- Queue ---
    AnomalyRule("queue.oldest_seconds", "threshold",
                AlertLevel.WARN, threshold_max=60),
    AnomalyRule("queue.oldest_seconds", "threshold",
                AlertLevel.CRITICAL, threshold_max=300),
]


class AnomalyDetector:
    """Checks metrics against rules and detects anomalies."""

    def __init__(
        self,
        metrics: MetricsCollector,
        rules: Optional[list[AnomalyRule]] = None,
        alert_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self._metrics = metrics
        self._rules = rules or DEFAULT_RULES
        self._alert_cb = alert_callback
        self._last_alert_time: dict[str, float] = {}
        self._anomalies: list[Anomaly] = []

    def check_all(self) -> list[Anomaly]:
        """Run all anomaly checks. Returns list of detected anomalies."""
        found = []
        for rule in self._rules:
            anomaly = self._check_rule(rule)
            if anomaly:
                found.append(anomaly)
                self._anomalies.append(anomaly)
                if len(self._anomalies) > 1000:
                    self._anomalies = self._anomalies[-500:]
                self._fire_alert(anomaly)
        return found

    def get_recent_anomalies(self, hours: int = 24) -> list[Anomaly]:
        """Get anomalies from the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [a for a in self._anomalies if a.detected_at > cutoff]

    def _check_rule(self, rule: AnomalyRule) -> Optional[Anomaly]:
        """Check a single rule against current metrics."""
        if rule.rule_type == "threshold":
            return self._check_threshold(rule)
        elif rule.rule_type == "trend":
            return self._check_trend(rule)
        elif rule.rule_type == "absence":
            return self._check_absence(rule)
        return None

    def _check_threshold(self, rule: AnomalyRule) -> Optional[Anomaly]:
        """Check if latest value exceeds threshold."""
        latest = self._metrics.query_latest(rule.metric_name)
        if latest is None:
            return None

        _, value = latest
        if rule.threshold_max is not None and value > rule.threshold_max:
            return Anomaly(
                rule=rule,
                detected_at=datetime.now(),
                message=(
                    f"{rule.metric_name} = {value:.1f} > "
                    f"{rule.threshold_max} ({rule.level.value})"
                ),
                current_value=value,
                threshold=rule.threshold_max,
            )
        if rule.threshold_min is not None and value < rule.threshold_min:
            return Anomaly(
                rule=rule,
                detected_at=datetime.now(),
                message=(
                    f"{rule.metric_name} = {value:.1f} < "
                    f"{rule.threshold_min} ({rule.level.value})"
                ),
                current_value=value,
                threshold=rule.threshold_min,
            )
        return None

    def _check_trend(self, rule: AnomalyRule) -> Optional[Anomaly]:
        """Check if short-term average is significantly higher than long-term."""
        short_avg = self._metrics.query(
            rule.metric_name,
            hours=rule.short_window_hours,
            aggregation="avg",
        )
        long_avg = self._metrics.query(
            rule.metric_name,
            hours=rule.long_window_hours,
            aggregation="avg",
        )

        if long_avg <= 0 or short_avg <= 0:
            return None

        ratio = short_avg / long_avg
        if ratio >= rule.trend_ratio_crit:
            return Anomaly(
                rule=rule,
                detected_at=datetime.now(),
                message=(
                    f"{rule.metric_name} trend CRITICAL: "
                    f"short={short_avg:.2f} / long={long_avg:.2f} "
                    f"(ratio {ratio:.1f}x)"
                ),
                current_value=short_avg,
                threshold=long_avg * rule.trend_ratio_crit,
            )
        elif ratio >= rule.trend_ratio_warn:
            return Anomaly(
                rule=rule,
                detected_at=datetime.now(),
                message=(
                    f"{rule.metric_name} trend WARN: "
                    f"short={short_avg:.2f} / long={long_avg:.2f} "
                    f"(ratio {ratio:.1f}x)"
                ),
                current_value=short_avg,
                threshold=long_avg * rule.trend_ratio_warn,
            )
        return None

    def _check_absence(self, rule: AnomalyRule) -> Optional[Anomaly]:
        """Check if a metric hasn't been emitted recently."""
        latest = self._metrics.query_latest(rule.metric_name)
        if latest is None:
            return None  # Never emitted — not an absence

        ts_str, _ = latest
        try:
            last_time = datetime.fromisoformat(ts_str)
            elapsed = (datetime.now() - last_time).total_seconds() / 60
            if elapsed > rule.max_silence_minutes:
                return Anomaly(
                    rule=rule,
                    detected_at=datetime.now(),
                    message=(
                        f"{rule.metric_name} ABSENT for "
                        f"{elapsed:.0f}min (max: {rule.max_silence_minutes}min)"
                    ),
                    current_value=elapsed,
                    threshold=float(rule.max_silence_minutes),
                )
        except (ValueError, TypeError):
            pass
        return None

    def _fire_alert(self, anomaly: Anomaly) -> None:
        """Send alert if cooldown has passed."""
        key = f"{anomaly.rule.metric_name}:{anomaly.rule.rule_type}:{anomaly.rule.level.value}"
        now = time.monotonic()
        last = self._last_alert_time.get(key, 0)
        cooldown = anomaly.rule.cooldown_minutes * 60

        if now - last < cooldown:
            return

        self._last_alert_time[key] = now

        if self._alert_cb:
            try:
                self._alert_cb(anomaly.message, anomaly.rule.level.value)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

        logger.warning(f"ANOMALY: {anomaly.message}")
