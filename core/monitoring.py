"""
Monitoring memoire et performance — alerte si un cycle depasse les seuils.

Utilise psutil pour mesurer la memoire RSS du process courant
et un context manager pour chronometre chaque cycle de trading.

Usage :
    monitor = PerformanceMonitor(max_memory_mb=500, max_cycle_seconds=30)

    # Check memoire ponctuel
    status = monitor.check_memory()
    if status["alert"]:
        # envoyer alerte Telegram...

    # Chronometre un cycle
    with monitor.time_cycle() as timer:
        run_trading_cycle()
    print(timer.elapsed, timer.alert)
"""

import time
import logging
import subprocess
from collections import deque

logger = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    logger.warning("psutil non installe — monitoring memoire desactive (pip install psutil)")


class CycleTimer:
    """Context manager pour mesurer le temps d'un cycle de trading."""

    def __init__(self, max_seconds: float):
        self.max_seconds = max_seconds
        self.start_time: float = 0.0
        self.elapsed: float = 0.0
        self.alert: bool = False

    def __enter__(self):
        self.start_time = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = time.monotonic() - self.start_time
        self.alert = self.elapsed > self.max_seconds
        if self.alert:
            logger.critical(
                f"CYCLE SLOW: {self.elapsed:.1f}s > {self.max_seconds}s"
            )
        else:
            logger.debug(f"Cycle OK: {self.elapsed:.1f}s")
        return False  # ne pas avaler les exceptions


class PerformanceMonitor:
    """Moniteur de performance — memoire RSS + temps de cycle.

    Args:
        max_memory_mb: seuil d'alerte memoire en Mo (default 500)
        max_cycle_seconds: seuil d'alerte temps de cycle en secondes (default 30)
    """

    def __init__(self, max_memory_mb: float = 500, max_cycle_seconds: float = 30):
        self.max_memory_mb = max_memory_mb
        self.max_cycle_seconds = max_cycle_seconds

    def check_memory(self) -> dict:
        """Verifie la consommation memoire du process courant.

        Returns:
            {"memory_mb": float, "alert": bool}
            Si psutil n'est pas installe, retourne memory_mb=0, alert=False.
        """
        if not _HAS_PSUTIL:
            return {"memory_mb": 0.0, "alert": False}

        process = psutil.Process()
        mem = process.memory_info().rss / 1024 / 1024
        alert = mem > self.max_memory_mb
        if alert:
            logger.critical(f"MEMORY ALERT: {mem:.0f}MB > {self.max_memory_mb}MB")
        return {"memory_mb": round(mem, 1), "alert": alert}

    def time_cycle(self) -> CycleTimer:
        """Context manager pour mesurer le temps d'un cycle.

        Usage:
            with monitor.time_cycle() as timer:
                do_work()
            print(timer.elapsed, timer.alert)
        """
        return CycleTimer(self.max_cycle_seconds)

    def full_check(self) -> dict:
        """Check complet : memoire + CPU (snapshot).

        Returns:
            {"memory_mb": float, "memory_alert": bool,
             "cpu_percent": float, "cpu_alert": bool}
        """
        mem = self.check_memory()
        result = {
            "memory_mb": mem["memory_mb"],
            "memory_alert": mem["alert"],
            "cpu_percent": 0.0,
            "cpu_alert": False,
        }
        if _HAS_PSUTIL:
            cpu = psutil.Process().cpu_percent(interval=0.1)
            result["cpu_percent"] = round(cpu, 1)
            # Alerte si > 90% CPU
            result["cpu_alert"] = cpu > 90.0
            if result["cpu_alert"]:
                logger.warning(f"CPU ALERT: {cpu:.1f}% > 90%")
        return result


class LatencyMonitor:
    """Continuous latency monitoring — ping target every check interval.

    Tracks P50, P95, P99 over a rolling 1-hour window.
    Alerts if P95 exceeds thresholds.
    """

    WINDOW_SIZE = 12  # 12 measurements = 1h at 5-min intervals
    THRESHOLD_WARNING_MS = 150
    THRESHOLD_CRITICAL_MS = 300

    def __init__(self, target_host: str = None):
        self.target_host = target_host
        self.history: deque = deque(maxlen=self.WINDOW_SIZE * 24)  # 24h
        self._alert_callback = None

    def set_alert_callback(self, callback):
        self._alert_callback = callback

    def measure(self) -> dict:
        """Measure latency to target and update stats.

        Returns:
            {latency_ms, mean_ms, p95_ms, p99_ms, status}
        """
        if not self.target_host:
            return {"latency_ms": 0, "status": "no_target"}

        latency_ms = self._ping(self.target_host)

        if latency_ms is not None:
            self.history.append(latency_ms)

        stats = self.get_stats()
        stats["latency_ms"] = latency_ms

        # Alert on threshold breach
        p95 = stats.get("p95_ms", 0)
        if p95 > self.THRESHOLD_CRITICAL_MS:
            stats["status"] = "CRITICAL"
            if self._alert_callback:
                self._alert_callback(
                    f"Latency CRITICAL: P95={p95:.0f}ms > {self.THRESHOLD_CRITICAL_MS}ms",
                    "critical"
                )
        elif p95 > self.THRESHOLD_WARNING_MS:
            stats["status"] = "WARNING"
            if self._alert_callback:
                self._alert_callback(
                    f"Latency WARNING: P95={p95:.0f}ms > {self.THRESHOLD_WARNING_MS}ms",
                    "warning"
                )
        else:
            stats["status"] = "OK"

        return stats

    def get_stats(self) -> dict:
        """Get latency statistics over rolling window."""
        if not self.history:
            return {"mean_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "samples": 0}

        recent = list(self.history)[-self.WINDOW_SIZE:]
        sorted_lat = sorted(recent)
        n = len(sorted_lat)

        return {
            "mean_ms": round(sum(sorted_lat) / n, 2),
            "p50_ms": round(sorted_lat[n // 2], 2),
            "p95_ms": round(sorted_lat[min(int(n * 0.95), n - 1)], 2),
            "p99_ms": round(sorted_lat[min(int(n * 0.99), n - 1)], 2),
            "samples": n,
        }

    @staticmethod
    def _ping(host: str, timeout: int = 3) -> float:
        """Ping a host and return latency in ms, or None on failure."""
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout), host],
                capture_output=True, text=True, timeout=timeout + 2,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "time=" in line:
                        return float(line.split("time=")[1].split()[0])
            return None
        except Exception:
            return None
