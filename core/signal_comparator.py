"""
SignalComparator -- Compares live vs paper execution results for the same signal.

Part of HARDEN-003: signal-once dual routing.
When a signal is generated once and routed to both live and paper pipelines,
this module logs and analyzes divergences:
  - Signal accepted by paper but rejected by live (or vice versa)
  - Different fill prices
  - Different sizing
  - Timing differences

All comparisons are persisted in a JSONL file for post-trade analysis.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Default log directory
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _ROOT / "logs" / "signal_sync"


class SignalComparator:
    """Compares live vs paper execution results for the same signal.

    Logs divergences for analysis:
      - Signal accepted by paper but rejected by live (or vice versa)
      - Different fill prices
      - Different sizing

    Usage:
        comparator = SignalComparator()
        result = comparator.compare(
            signal_id="SIG_20260327_fx_eurusd_trend_a1b2",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD", "direction": "BUY", "qty": 25000},
            live_result={"passed_risk": True, "order_result": {...}},
            paper_results=[{"passed_risk": True, "order_result": {...}}],
        )
    """

    def __init__(self, log_dir: str | None = None):
        """Initialize the comparator.

        Args:
            log_dir: directory for the JSONL comparison log.
                     Defaults to logs/signal_sync/.
        """
        self._log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / "comparisons.jsonl"

        # In-memory stats
        self._total_signals = 0
        self._matched = 0
        self._diverged = 0
        self._comparisons: List[dict] = []

    def compare(
        self,
        signal_id: str,
        strategy: str,
        signal: dict,
        live_result: dict | None,
        paper_results: List[dict],
    ) -> dict:
        """Compare live vs paper execution results for a single signal.

        Args:
            signal_id: unique signal identifier (from _generate_signal_id)
            strategy: strategy name
            signal: the original signal dict
            live_result: execution result from live pipeline (None if paper-only)
            paper_results: list of execution results from paper pipelines

        Returns:
            {
                signal_id: str,
                strategy: str,
                divergences: list[str],
                match: bool,
                timestamp: str,
            }
        """
        divergences = []
        self._total_signals += 1

        # Check risk validation divergence
        if live_result is not None:
            live_passed = live_result.get("passed_risk", False)

            for i, paper_result in enumerate(paper_results):
                paper_passed = paper_result.get("passed_risk", False)
                paper_mode = paper_result.get("mode", f"paper_{i}")

                # Risk acceptance divergence
                if live_passed and not paper_passed:
                    divergences.append(
                        f"risk_divergence: live ACCEPTED but {paper_mode} REJECTED"
                    )
                elif not live_passed and paper_passed:
                    divergences.append(
                        f"risk_divergence: live REJECTED but {paper_mode} ACCEPTED"
                    )

                # If both passed, compare order results
                if live_passed and paper_passed:
                    live_order = live_result.get("order_result") or {}
                    paper_order = paper_result.get("order_result") or {}

                    # Sizing divergence
                    live_qty = live_order.get("qty")
                    paper_qty = paper_order.get("qty")
                    if live_qty is not None and paper_qty is not None:
                        if live_qty != paper_qty:
                            divergences.append(
                                f"sizing_divergence: live qty={live_qty} "
                                f"vs {paper_mode} qty={paper_qty}"
                            )

                    # Fill price divergence
                    live_price = live_order.get("filled_price")
                    paper_price = paper_order.get("filled_price")
                    if live_price is not None and paper_price is not None:
                        if abs(float(live_price) - float(paper_price)) > 0.01:
                            divergences.append(
                                f"price_divergence: live price={live_price} "
                                f"vs {paper_mode} price={paper_price}"
                            )

                    # Error divergence
                    live_error = live_result.get("error")
                    paper_error = paper_result.get("error")
                    if live_error and not paper_error:
                        divergences.append(
                            f"error_divergence: live error='{live_error}' "
                            f"but {paper_mode} succeeded"
                        )
                    elif paper_error and not live_error:
                        divergences.append(
                            f"error_divergence: {paper_mode} error='{paper_error}' "
                            f"but live succeeded"
                        )

        is_match = len(divergences) == 0
        if is_match:
            self._matched += 1
        else:
            self._diverged += 1

        comparison = {
            "signal_id": signal_id,
            "strategy": strategy,
            "signal": signal,
            "live_result": live_result,
            "paper_results": paper_results,
            "divergences": divergences,
            "match": is_match,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        self._comparisons.append(comparison)
        self._persist(comparison)

        if divergences:
            logger.warning(
                f"Signal {signal_id} DIVERGED: {divergences}"
            )
        else:
            logger.debug(f"Signal {signal_id} matched across all pipelines")

        return {
            "signal_id": signal_id,
            "strategy": strategy,
            "divergences": divergences,
            "match": is_match,
            "timestamp": comparison["timestamp"],
        }

    def get_sync_stats(self) -> dict:
        """Return synchronization statistics.

        Returns:
            {
                total_signals: int,
                matched: int,
                diverged: int,
                match_rate: float,  # 0.0 to 1.0
            }
        """
        match_rate = (
            self._matched / self._total_signals
            if self._total_signals > 0
            else 0.0
        )

        return {
            "total_signals": self._total_signals,
            "matched": self._matched,
            "diverged": self._diverged,
            "match_rate": round(match_rate, 4),
        }

    def get_divergences(self, limit: int = 50) -> List[dict]:
        """Return recent divergences for review.

        Args:
            limit: max number of divergences to return

        Returns:
            List of comparison dicts where match is False.
        """
        diverged = [c for c in self._comparisons if not c["match"]]
        return diverged[-limit:]

    def _persist(self, comparison: dict):
        """Append a comparison record to the JSONL log file."""
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(comparison, default=str) + "\n")
        except Exception as exc:
            logger.error(f"Failed to persist comparison: {exc}")
