"""Execution Quality Monitor — unified view of trading reality vs backtest.

Aggregates SlippageTracker + CostTracker + PerformanceGuard into a single
monitoring interface with clear alerts and degradation detection.

Metrics:
  - Slippage real vs expected
  - Fill rate (% orders executed)
  - Latency order → fill
  - % SL actually executed
  - Rejection rate

Usage:
    monitor = ExecutionMonitor(slippage_tracker, cost_tracker)
    report = monitor.get_report()
    alerts = monitor.check_alerts()
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class ExecutionMetrics:
    """Snapshot of execution quality."""
    timestamp: datetime
    period: str  # "1h" | "24h" | "7d"

    # Slippage
    avg_slippage_bps: float
    backtest_slippage_bps: float
    slippage_ratio: float  # actual / backtest
    worst_slippage_bps: float

    # Fill quality
    total_orders: int
    filled_orders: int
    rejected_orders: int
    fill_rate: float  # 0-1
    partial_fills: int

    # Latency
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float

    # SL execution
    sl_triggered: int
    sl_executed: int
    sl_execution_rate: float  # 0-1
    sl_avg_slippage_bps: float

    # Costs
    total_commission: float
    avg_cost_ratio: float  # commission / notional

    # Level
    level: str  # OK | WARNING | CRITICAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "period": self.period,
            "slippage": {
                "avg_bps": round(self.avg_slippage_bps, 2),
                "backtest_bps": round(self.backtest_slippage_bps, 2),
                "ratio": round(self.slippage_ratio, 2),
                "worst_bps": round(self.worst_slippage_bps, 2),
            },
            "fills": {
                "total": self.total_orders,
                "filled": self.filled_orders,
                "rejected": self.rejected_orders,
                "fill_rate": round(self.fill_rate, 4),
                "partial": self.partial_fills,
            },
            "latency": {
                "avg_ms": round(self.avg_latency_ms, 1),
                "p95_ms": round(self.p95_latency_ms, 1),
                "p99_ms": round(self.p99_latency_ms, 1),
            },
            "stop_loss": {
                "triggered": self.sl_triggered,
                "executed": self.sl_executed,
                "rate": round(self.sl_execution_rate, 4),
                "avg_slippage_bps": round(self.sl_avg_slippage_bps, 2),
            },
            "costs": {
                "total_commission": round(self.total_commission, 2),
                "avg_cost_ratio": round(self.avg_cost_ratio, 6),
            },
            "level": self.level,
        }


@dataclass
class ExecutionAlert:
    level: str  # WARNING | CRITICAL
    category: str  # slippage | fill_rate | latency | stop_loss | cost
    message: str
    value: float
    threshold: float
    timestamp: datetime


class ExecutionMonitor:
    """Unified execution quality monitoring."""

    # Thresholds
    SLIPPAGE_WARNING = 2.0  # ratio vs backtest
    SLIPPAGE_CRITICAL = 3.0
    FILL_RATE_WARNING = 0.90
    FILL_RATE_CRITICAL = 0.80
    LATENCY_WARNING_MS = 200
    LATENCY_CRITICAL_MS = 500
    SL_EXEC_WARNING = 0.95
    SL_EXEC_CRITICAL = 0.90
    COST_WARNING = 0.003  # 0.3% of notional
    COST_CRITICAL = 0.005  # 0.5%

    def __init__(
        self,
        slippage_tracker=None,
        cost_tracker=None,
        data_dir: str = "data",
    ):
        self.slippage_tracker = slippage_tracker
        self.cost_tracker = cost_tracker
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / "execution_monitor.db"
        self._init_db()

    def record_order(
        self,
        trade_id: str,
        strategy: str,
        symbol: str,
        side: str,
        order_type: str,
        requested_price: float,
        filled_price: float | None,
        status: str,  # filled | rejected | partial | cancelled
        latency_ms: float = 0.0,
        is_stop_loss: bool = False,
        commission: float = 0.0,
        notional: float = 0.0,
        quantity: float = 0.0,
    ) -> None:
        """Record an order execution event."""
        now = datetime.now(timezone.utc).isoformat()

        slippage_bps = 0.0
        if filled_price and requested_price and requested_price > 0:
            slippage_bps = abs(filled_price - requested_price) / requested_price * 10000

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """INSERT INTO execution_events
                   (timestamp, trade_id, strategy, symbol, side, order_type,
                    requested_price, filled_price, status, latency_ms,
                    slippage_bps, is_stop_loss, commission, notional, quantity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now, trade_id, strategy, symbol, side, order_type,
                    requested_price, filled_price, status, latency_ms,
                    slippage_bps, is_stop_loss, commission, notional, quantity,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_metrics(self, period: str = "24h") -> ExecutionMetrics:
        """Get execution metrics for a given period."""
        hours = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(period, 24)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                "SELECT * FROM execution_events WHERE timestamp >= ?",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return self._empty_metrics(period)

        # Parse rows
        events = []
        for row in rows:
            events.append({
                "status": row[9],
                "slippage_bps": row[11] or 0.0,
                "latency_ms": row[10] or 0.0,
                "is_stop_loss": bool(row[12]),
                "commission": row[13] or 0.0,
                "notional": row[14] or 0.0,
                "filled_price": row[8],
            })

        total = len(events)
        filled = sum(1 for e in events if e["status"] == "filled")
        rejected = sum(1 for e in events if e["status"] == "rejected")
        partial = sum(1 for e in events if e["status"] == "partial")

        # Slippage (only filled orders)
        slippages = [e["slippage_bps"] for e in events if e["status"] == "filled" and e["slippage_bps"] > 0]
        avg_slip = float(sum(slippages) / len(slippages)) if slippages else 0.0
        worst_slip = max(slippages) if slippages else 0.0
        backtest_slip = 2.0  # Default backtest slippage assumption
        slip_ratio = avg_slip / backtest_slip if backtest_slip > 0 else 0.0

        # Latency
        latencies = [e["latency_ms"] for e in events if e["latency_ms"] > 0]
        avg_lat = float(sum(latencies) / len(latencies)) if latencies else 0.0
        sorted_lat = sorted(latencies) if latencies else [0.0]
        p95_lat = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0.0
        p99_lat = sorted_lat[int(len(sorted_lat) * 0.99)] if sorted_lat else 0.0

        # SL execution
        sl_events = [e for e in events if e["is_stop_loss"]]
        sl_triggered = len(sl_events)
        sl_executed = sum(1 for e in sl_events if e["status"] == "filled")
        sl_rate = sl_executed / sl_triggered if sl_triggered > 0 else 1.0
        sl_slips = [e["slippage_bps"] for e in sl_events if e["status"] == "filled"]
        sl_avg_slip = float(sum(sl_slips) / len(sl_slips)) if sl_slips else 0.0

        # Costs
        total_comm = sum(e["commission"] for e in events)
        total_notional = sum(e["notional"] for e in events if e["notional"] > 0)
        avg_cost = total_comm / total_notional if total_notional > 0 else 0.0

        fill_rate = filled / total if total > 0 else 1.0

        # Level
        level = self._compute_level(slip_ratio, fill_rate, p95_lat, sl_rate, avg_cost)

        return ExecutionMetrics(
            timestamp=datetime.now(timezone.utc),
            period=period,
            avg_slippage_bps=avg_slip,
            backtest_slippage_bps=backtest_slip,
            slippage_ratio=slip_ratio,
            worst_slippage_bps=worst_slip,
            total_orders=total,
            filled_orders=filled,
            rejected_orders=rejected,
            fill_rate=fill_rate,
            partial_fills=partial,
            avg_latency_ms=avg_lat,
            p95_latency_ms=p95_lat,
            p99_latency_ms=p99_lat,
            sl_triggered=sl_triggered,
            sl_executed=sl_executed,
            sl_execution_rate=sl_rate,
            sl_avg_slippage_bps=sl_avg_slip,
            total_commission=total_comm,
            avg_cost_ratio=avg_cost,
            level=level,
        )

    def check_alerts(self, period: str = "24h") -> List[ExecutionAlert]:
        """Check all execution thresholds and return alerts."""
        metrics = self.get_metrics(period)
        alerts = []
        now = datetime.now(timezone.utc)

        # Slippage
        if metrics.slippage_ratio >= self.SLIPPAGE_CRITICAL:
            alerts.append(ExecutionAlert(
                level="CRITICAL", category="slippage",
                message=f"Slippage {metrics.slippage_ratio:.1f}x backtest",
                value=metrics.slippage_ratio, threshold=self.SLIPPAGE_CRITICAL,
                timestamp=now,
            ))
        elif metrics.slippage_ratio >= self.SLIPPAGE_WARNING:
            alerts.append(ExecutionAlert(
                level="WARNING", category="slippage",
                message=f"Slippage {metrics.slippage_ratio:.1f}x backtest",
                value=metrics.slippage_ratio, threshold=self.SLIPPAGE_WARNING,
                timestamp=now,
            ))

        # Fill rate
        if metrics.fill_rate < self.FILL_RATE_CRITICAL:
            alerts.append(ExecutionAlert(
                level="CRITICAL", category="fill_rate",
                message=f"Fill rate {metrics.fill_rate:.0%}",
                value=metrics.fill_rate, threshold=self.FILL_RATE_CRITICAL,
                timestamp=now,
            ))
        elif metrics.fill_rate < self.FILL_RATE_WARNING:
            alerts.append(ExecutionAlert(
                level="WARNING", category="fill_rate",
                message=f"Fill rate {metrics.fill_rate:.0%}",
                value=metrics.fill_rate, threshold=self.FILL_RATE_WARNING,
                timestamp=now,
            ))

        # Latency
        if metrics.p95_latency_ms >= self.LATENCY_CRITICAL_MS:
            alerts.append(ExecutionAlert(
                level="CRITICAL", category="latency",
                message=f"P95 latency {metrics.p95_latency_ms:.0f}ms",
                value=metrics.p95_latency_ms, threshold=self.LATENCY_CRITICAL_MS,
                timestamp=now,
            ))
        elif metrics.p95_latency_ms >= self.LATENCY_WARNING_MS:
            alerts.append(ExecutionAlert(
                level="WARNING", category="latency",
                message=f"P95 latency {metrics.p95_latency_ms:.0f}ms",
                value=metrics.p95_latency_ms, threshold=self.LATENCY_WARNING_MS,
                timestamp=now,
            ))

        # SL execution
        if metrics.sl_triggered > 0:
            if metrics.sl_execution_rate < self.SL_EXEC_CRITICAL:
                alerts.append(ExecutionAlert(
                    level="CRITICAL", category="stop_loss",
                    message=f"SL execution rate {metrics.sl_execution_rate:.0%}",
                    value=metrics.sl_execution_rate, threshold=self.SL_EXEC_CRITICAL,
                    timestamp=now,
                ))
            elif metrics.sl_execution_rate < self.SL_EXEC_WARNING:
                alerts.append(ExecutionAlert(
                    level="WARNING", category="stop_loss",
                    message=f"SL execution rate {metrics.sl_execution_rate:.0%}",
                    value=metrics.sl_execution_rate, threshold=self.SL_EXEC_WARNING,
                    timestamp=now,
                ))

        # Costs
        if metrics.avg_cost_ratio >= self.COST_CRITICAL:
            alerts.append(ExecutionAlert(
                level="CRITICAL", category="cost",
                message=f"Avg cost {metrics.avg_cost_ratio:.3%}",
                value=metrics.avg_cost_ratio, threshold=self.COST_CRITICAL,
                timestamp=now,
            ))
        elif metrics.avg_cost_ratio >= self.COST_WARNING:
            alerts.append(ExecutionAlert(
                level="WARNING", category="cost",
                message=f"Avg cost {metrics.avg_cost_ratio:.3%}",
                value=metrics.avg_cost_ratio, threshold=self.COST_WARNING,
                timestamp=now,
            ))

        return alerts

    def get_strategy_breakdown(
        self, period: str = "24h"
    ) -> Dict[str, Dict[str, Any]]:
        """Get execution metrics broken down by strategy."""
        hours = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(period, 24)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                """SELECT strategy, COUNT(*),
                          SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END),
                          AVG(CASE WHEN status='filled' THEN slippage_bps END),
                          AVG(CASE WHEN latency_ms > 0 THEN latency_ms END),
                          SUM(commission)
                   FROM execution_events
                   WHERE timestamp >= ?
                   GROUP BY strategy""",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        result = {}
        for row in rows:
            strategy = row[0]
            total = row[1]
            filled = row[2] or 0
            result[strategy] = {
                "total_orders": total,
                "filled": filled,
                "fill_rate": filled / total if total > 0 else 0.0,
                "avg_slippage_bps": round(row[3] or 0.0, 2),
                "avg_latency_ms": round(row[4] or 0.0, 1),
                "total_commission": round(row[5] or 0.0, 2),
            }

        return result

    # ─── Internal ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Initialize SQLite database."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trade_id TEXT,
                    strategy TEXT,
                    symbol TEXT,
                    side TEXT,
                    order_type TEXT,
                    requested_price REAL,
                    filled_price REAL,
                    status TEXT,
                    latency_ms REAL DEFAULT 0,
                    slippage_bps REAL DEFAULT 0,
                    is_stop_loss INTEGER DEFAULT 0,
                    commission REAL DEFAULT 0,
                    notional REAL DEFAULT 0,
                    quantity REAL DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exec_ts ON execution_events(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exec_strat ON execution_events(strategy)"
            )
            conn.commit()
        finally:
            conn.close()

    def _empty_metrics(self, period: str) -> ExecutionMetrics:
        return ExecutionMetrics(
            timestamp=datetime.now(timezone.utc),
            period=period,
            avg_slippage_bps=0.0,
            backtest_slippage_bps=2.0,
            slippage_ratio=0.0,
            worst_slippage_bps=0.0,
            total_orders=0,
            filled_orders=0,
            rejected_orders=0,
            fill_rate=1.0,
            partial_fills=0,
            avg_latency_ms=0.0,
            p95_latency_ms=0.0,
            p99_latency_ms=0.0,
            sl_triggered=0,
            sl_executed=0,
            sl_execution_rate=1.0,
            sl_avg_slippage_bps=0.0,
            total_commission=0.0,
            avg_cost_ratio=0.0,
            level="OK",
        )

    def _compute_level(
        self,
        slip_ratio: float,
        fill_rate: float,
        p95_lat: float,
        sl_rate: float,
        cost_ratio: float,
    ) -> str:
        if (
            slip_ratio >= self.SLIPPAGE_CRITICAL
            or fill_rate < self.FILL_RATE_CRITICAL
            or p95_lat >= self.LATENCY_CRITICAL_MS
            or sl_rate < self.SL_EXEC_CRITICAL
            or cost_ratio >= self.COST_CRITICAL
        ):
            return "CRITICAL"
        if (
            slip_ratio >= self.SLIPPAGE_WARNING
            or fill_rate < self.FILL_RATE_WARNING
            or p95_lat >= self.LATENCY_WARNING_MS
            or sl_rate < self.SL_EXEC_WARNING
            or cost_ratio >= self.COST_WARNING
        ):
            return "WARNING"
        return "OK"
