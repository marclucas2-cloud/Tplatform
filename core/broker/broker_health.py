"""Broker health tracking for graceful degradation.

Each broker has a health status tracked in real-time:
  HEALTHY     - API responds, normal latency, contracts OK
  DEGRADED    - API responds but slow (>2x avg) or intermittent errors (<3)
  DOWN        - API not responding or 3+ consecutive errors
  MAINTENANCE - planned downtime (IBKR weekend, etc.)

Impact by state:
  HEALTHY:     Normal trading, data included in global regime/DD
  DEGRADED:    Reduced sizing (/2), data marked "degraded"
  DOWN:        No new trades, existing SLs active, data excluded from cross-broker
  MAINTENANCE: Like DOWN but no repeated alerts
"""

import logging
import time
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger("broker.health")


class BrokerHealth(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    MAINTENANCE = "MAINTENANCE"


class BrokerHealthTracker:
    """Tracks health of a single broker."""

    def __init__(self, broker_name: str, degraded_threshold: int = 1,
                 down_threshold: int = 3):
        self.broker_name = broker_name
        self.health = BrokerHealth.HEALTHY
        self._consecutive_errors = 0
        self._degraded_threshold = degraded_threshold
        self._down_threshold = down_threshold
        self._last_success: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[datetime] = None
        self._avg_latency_ms: float = 0
        self._latency_samples: list[float] = []
        self._maintenance_until: Optional[datetime] = None
        self._total_requests = 0
        self._total_errors = 0

    def record_success(self, latency_ms: float) -> BrokerHealth:
        """Record a successful API call."""
        self._consecutive_errors = 0
        self._last_success = datetime.now()
        self._total_requests += 1
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > 100:
            self._latency_samples.pop(0)
        self._avg_latency_ms = (
            sum(self._latency_samples) / len(self._latency_samples)
        )

        # Check if in maintenance window
        if self._maintenance_until and datetime.now() < self._maintenance_until:
            return self.health

        if self._avg_latency_ms > 0 and latency_ms > 2 * self._avg_latency_ms:
            self.health = BrokerHealth.DEGRADED
        else:
            self.health = BrokerHealth.HEALTHY
        return self.health

    def record_error(self, error: str) -> BrokerHealth:
        """Record a failed API call."""
        self._consecutive_errors += 1
        self._total_errors += 1
        self._total_requests += 1
        self._last_error = error
        self._last_error_at = datetime.now()

        if self._consecutive_errors >= self._down_threshold:
            self.health = BrokerHealth.DOWN
        elif self._consecutive_errors >= self._degraded_threshold:
            self.health = BrokerHealth.DEGRADED
        return self.health

    def set_maintenance(self, until: datetime) -> None:
        """Set planned maintenance window."""
        self._maintenance_until = until
        self.health = BrokerHealth.MAINTENANCE
        logger.info(
            f"Broker {self.broker_name} maintenance until "
            f"{until.isoformat()}"
        )

    def clear_maintenance(self) -> None:
        """Clear maintenance window."""
        self._maintenance_until = None
        if self._consecutive_errors == 0:
            self.health = BrokerHealth.HEALTHY

    @property
    def is_tradeable(self) -> bool:
        """Can we submit new trades to this broker?"""
        return self.health in (BrokerHealth.HEALTHY, BrokerHealth.DEGRADED)

    @property
    def is_data_reliable(self) -> bool:
        """Should we include this broker's data in cross-broker calculations?"""
        return self.health == BrokerHealth.HEALTHY

    @property
    def sizing_multiplier(self) -> float:
        """Position sizing multiplier based on health.
        HEALTHY=1.0, DEGRADED=0.5, DOWN/MAINTENANCE=0.0."""
        if self.health == BrokerHealth.HEALTHY:
            return 1.0
        elif self.health == BrokerHealth.DEGRADED:
            return 0.5
        return 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self._avg_latency_ms

    def to_dict(self) -> dict:
        return {
            "broker": self.broker_name,
            "health": self.health.value,
            "consecutive_errors": self._consecutive_errors,
            "avg_latency_ms": round(self._avg_latency_ms, 1),
            "last_success": (
                self._last_success.isoformat() if self._last_success else None
            ),
            "last_error": self._last_error,
            "last_error_at": (
                self._last_error_at.isoformat() if self._last_error_at else None
            ),
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "sizing_multiplier": self.sizing_multiplier,
            "is_tradeable": self.is_tradeable,
            "is_data_reliable": self.is_data_reliable,
        }


class BrokerHealthRegistry:
    """Registry of all broker health trackers."""

    def __init__(self):
        self._trackers: dict[str, BrokerHealthTracker] = {}

    def register(self, name: str, **kwargs) -> BrokerHealthTracker:
        tracker = BrokerHealthTracker(name, **kwargs)
        self._trackers[name] = tracker
        return tracker

    def get(self, name: str) -> Optional[BrokerHealthTracker]:
        return self._trackers.get(name)

    def get_all(self) -> dict[str, BrokerHealthTracker]:
        return dict(self._trackers)

    def healthy_brokers(self) -> list[str]:
        return [n for n, t in self._trackers.items() if t.is_data_reliable]

    def tradeable_brokers(self) -> list[str]:
        return [n for n, t in self._trackers.items() if t.is_tradeable]

    def down_brokers(self) -> list[str]:
        return [
            n for n, t in self._trackers.items()
            if t.health in (BrokerHealth.DOWN, BrokerHealth.MAINTENANCE)
        ]

    def summary(self) -> dict:
        return {
            name: tracker.to_dict()
            for name, tracker in self._trackers.items()
        }
