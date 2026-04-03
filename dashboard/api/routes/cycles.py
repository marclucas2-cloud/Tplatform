"""Cycle Health Dashboard endpoint (R2-03).

GET /api/cycles -> JSON with health status of all cycles,
queue status, and system metrics.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("dashboard.cycles")

ROOT = Path(__file__).resolve().parent.parent.parent.parent


def get_cycles_health() -> dict:
    """Build the cycles health response.

    Returns dict suitable for JSON serialization.
    Called by the dashboard API routes.
    """
    result = {
        "cycles": {},
        "queue": {
            "depth": 0,
            "oldest_task_seconds": 0,
            "tasks_completed_1h": 0,
            "tasks_failed_1h": 0,
        },
        "system": {
            "cpu_percent": 0,
            "ram_percent": 0,
            "disk_percent": 0,
            "uptime_hours": 0,
        },
        "brokers": {},
        "timestamp": datetime.now().isoformat(),
    }

    # Load cycle metrics from worker state file (if available)
    try:
        from core.monitoring.metrics_pipeline import get_metrics
        metrics = get_metrics()

        cycle_names = [
            "crypto", "fx_carry", "futures", "fx_paper",
            "live_risk", "v10_portfolio", "v11_hrp", "v12_regime",
            "v11_eod",
        ]

        for name in cycle_names:
            duration = metrics.query(
                f"cycle.{name}.duration_seconds", hours=24, aggregation="avg"
            )
            errors = metrics.query(
                f"cycle.{name}.error", hours=24, aggregation="count"
            )
            total = metrics.query(
                f"cycle.{name}.duration_seconds", hours=24, aggregation="count"
            )

            # Determine health from recent errors
            recent_errors = metrics.query(
                f"cycle.{name}.error", hours=1, aggregation="count"
            )
            if recent_errors >= 3:
                health = "FAILED"
            elif recent_errors >= 1:
                health = "DEGRADED"
            elif total > 0:
                health = "HEALTHY"
            else:
                health = "UNKNOWN"

            # Trend: compare 24h avg vs 7d avg
            avg_7d = metrics.query(
                f"cycle.{name}.duration_seconds", hours=168, aggregation="avg"
            )
            trend = "STABLE"
            if avg_7d > 0 and duration > 0:
                ratio = duration / avg_7d
                if ratio > 1.5:
                    trend = "DEGRADING"
                elif ratio < 0.7:
                    trend = "IMPROVING"

            result["cycles"][name] = {
                "health": health,
                "last_duration_seconds": round(duration, 3),
                "avg_duration_seconds": round(duration, 3),
                "total_runs_24h": int(total),
                "total_failures_24h": int(errors),
                "trend": trend,
            }

        # System metrics
        result["system"]["cpu_percent"] = metrics.query(
            "system.cpu.percent", hours=1, aggregation="avg"
        )
        result["system"]["ram_percent"] = metrics.query(
            "system.ram.percent", hours=1, aggregation="avg"
        )
        result["system"]["disk_percent"] = metrics.query(
            "system.disk.percent", hours=1, aggregation="avg"
        )

    except Exception as e:
        logger.debug(f"Metrics unavailable: {e}")

    # System uptime
    try:
        import psutil
        boot_time = psutil.boot_time()
        result["system"]["uptime_hours"] = round(
            (time.time() - boot_time) / 3600, 1
        )
    except ImportError:
        pass

    return result
