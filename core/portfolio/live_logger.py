"""Live Portfolio Snapshot Logger — JSONL logging every 5 minutes.

Captures full portfolio state for post-mortem analysis:
  - Positions, ERE, correlation, leverage, PnL

Usage:
    logger = LiveSnapshotLogger(portfolio_engine)
    logger.record()  # Call every 5 minutes from worker
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LiveSnapshotLogger:
    """Append portfolio snapshots to JSONL file every N minutes."""

    def __init__(
        self,
        portfolio_engine=None,
        correlation_engine=None,
        ere_calculator=None,
        execution_monitor=None,
        log_dir: str = "logs",
        max_file_mb: int = 50,
    ):
        self.portfolio_engine = portfolio_engine
        self.correlation_engine = correlation_engine
        self.ere_calculator = ere_calculator
        self.execution_monitor = execution_monitor
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_file_mb * 1024 * 1024

    def record(
        self,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record a full snapshot to JSONL.

        Args:
            extra: Additional key-value pairs to include.

        Returns:
            The snapshot dict, or None on failure.
        """
        snapshot: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Portfolio state
        if self.portfolio_engine is not None:
            try:
                state = self.portfolio_engine.get_state()
                snapshot["portfolio"] = state.to_dict()
            except Exception as e:
                snapshot["portfolio_error"] = str(e)

        # Correlation
        if self.correlation_engine is not None:
            try:
                snapshot["correlation"] = self.correlation_engine.to_dict()
            except Exception as e:
                snapshot["correlation_error"] = str(e)

        # ERE (from portfolio positions)
        if self.ere_calculator is not None and self.portfolio_engine is not None:
            try:
                state = self.portfolio_engine.get_state()
                all_pos = []
                for b in state.brokers:
                    all_pos.extend(b.positions)
                ere = self.ere_calculator.calculate(all_pos, state.total_capital)
                snapshot["ere"] = ere.to_dict()
            except Exception as e:
                snapshot["ere_error"] = str(e)

        # Execution metrics
        if self.execution_monitor is not None:
            try:
                metrics = self.execution_monitor.get_metrics("1h")
                snapshot["execution"] = metrics.to_dict()
            except Exception as e:
                snapshot["execution_error"] = str(e)

        if extra:
            snapshot.update(extra)

        # Write to JSONL (rotate if too large)
        self._write(snapshot)
        return snapshot

    def get_recent(self, n: int = 50) -> list:
        """Read the last N snapshots."""
        path = self._current_path()
        if not path.exists():
            return []

        try:
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-n:]
            return [json.loads(line) for line in recent if line.strip()]
        except Exception as e:
            logger.warning(f"Failed to read snapshots: {e}")
            return []

    # ─── Internal ────────────────────────────────────────────────────────

    def _current_path(self) -> Path:
        """Current log file path (daily rotation)."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        return self._log_dir / f"live_portfolio_{date_str}.jsonl"

    def _write(self, snapshot: Dict[str, Any]) -> None:
        path = self._current_path()

        # Rotate if file too large
        if path.exists() and path.stat().st_size > self._max_bytes:
            rotated = path.with_suffix(f".{datetime.utcnow().strftime('%H%M%S')}.jsonl")
            path.rename(rotated)
            logger.info(f"Rotated log to {rotated.name}")

        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write snapshot: {e}")
