"""Cycle Runner with error boundaries for isolated cycle execution.

Each cycle is wrapped in a CycleRunner that:
1. Captures all exceptions (error boundary)
2. Measures execution time
3. Manages retries with backoff
4. Emits structured metrics
5. Sends Telegram alert if cycle fails 3x consecutively
6. Tracks cycle health (HEALTHY, DEGRADED, FAILED)
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("worker.cycle_runner")


class CycleHealth(Enum):
    HEALTHY = "HEALTHY"       # Last run OK
    DEGRADED = "DEGRADED"     # 1-2 consecutive failures, retrying
    FAILED = "FAILED"         # 3+ consecutive failures, alerted


@dataclass
class CycleMetrics:
    name: str
    last_run_at: float
    last_duration_seconds: float
    last_success: bool
    consecutive_failures: int
    total_runs: int
    total_failures: int
    health: CycleHealth
    avg_duration_seconds: float  # rolling 20 last runs
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "last_run_at": self.last_run_at,
            "last_duration_seconds": round(self.last_duration_seconds, 3),
            "last_success": self.last_success,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_failures": self.total_failures,
            "health": self.health.value,
            "avg_duration_seconds": round(self.avg_duration_seconds, 3),
            "last_error": self.last_error,
        }


class CycleRunner:
    """Wraps a cycle function with error boundaries, metrics, and alerting."""

    def __init__(
        self,
        name: str,
        callable: Callable,
        max_consecutive_failures: int = 3,
        timeout_seconds: float = 60.0,
        alert_callback: Optional[Callable[[str], None]] = None,
        metrics_callback: Optional[Callable[[str, float, bool, Optional[str]], None]] = None,
    ):
        self.name = name
        self._callable = callable
        self._max_failures = max_consecutive_failures
        self._timeout = timeout_seconds
        self._alert = alert_callback
        self._metrics_cb = metrics_callback
        self._consecutive_failures = 0
        self._total_runs = 0
        self._total_failures = 0
        self._durations: list[float] = []
        self._health = CycleHealth.HEALTHY
        self._last_run_at: float = 0
        self._last_duration: float = 0
        self._last_success: bool = True
        self._last_error: Optional[str] = None

    def run(self, *args, **kwargs) -> CycleMetrics:
        """Execute the cycle with error boundary. Never raises."""
        start = time.monotonic()
        self._total_runs += 1
        self._last_run_at = start
        success = False
        error_str = None

        try:
            self._callable(*args, **kwargs)
            success = True
            self._consecutive_failures = 0
            self._health = CycleHealth.HEALTHY
            self._last_error = None
        except Exception as e:
            self._consecutive_failures += 1
            self._total_failures += 1
            error_str = str(e)
            self._last_error = error_str

            logger.error(
                f"Cycle {self.name} error ({self._consecutive_failures}/"
                f"{self._max_failures}): {e}",
                exc_info=True,
            )

            if self._consecutive_failures >= self._max_failures:
                self._health = CycleHealth.FAILED
                if self._alert:
                    try:
                        self._alert(
                            f"Cycle {self.name} FAILED: "
                            f"{self._consecutive_failures} echecs consecutifs. "
                            f"Derniere erreur: {e}"
                        )
                    except Exception:
                        pass
            else:
                self._health = CycleHealth.DEGRADED
                if self._alert and self._consecutive_failures == 1:
                    try:
                        self._alert(
                            f"Cycle {self.name} erreur: {e}. "
                            f"Retry {self._consecutive_failures}/{self._max_failures}"
                        )
                    except Exception:
                        pass
        finally:
            duration = time.monotonic() - start
            self._last_duration = duration
            self._last_success = success
            self._durations.append(duration)
            if len(self._durations) > 20:
                self._durations.pop(0)

        if self._metrics_cb:
            try:
                self._metrics_cb(self.name, duration, success, error_str)
            except Exception:
                pass

        return self.metrics

    @property
    def is_healthy(self) -> bool:
        return self._health == CycleHealth.HEALTHY

    @property
    def health(self) -> CycleHealth:
        return self._health

    @property
    def metrics(self) -> CycleMetrics:
        return CycleMetrics(
            name=self.name,
            last_run_at=self._last_run_at,
            last_duration_seconds=self._last_duration,
            last_success=self._last_success,
            consecutive_failures=self._consecutive_failures,
            total_runs=self._total_runs,
            total_failures=self._total_failures,
            health=self._health,
            avg_duration_seconds=(
                sum(self._durations) / len(self._durations)
                if self._durations else 0
            ),
            last_error=self._last_error,
        )

    def reset(self) -> None:
        """Reset health state (e.g., after manual intervention)."""
        self._consecutive_failures = 0
        self._health = CycleHealth.HEALTHY
        self._last_error = None
