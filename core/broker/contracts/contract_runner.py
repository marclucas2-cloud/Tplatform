"""Contract test runner — validates live API responses against contracts.

Runs hourly as a BACKGROUND priority task in the worker.
Only makes READ-ONLY API calls (no orders, no borrows).

Tolerance:
  - 1 violation: WARN (could be a network glitch)
  - 3 consecutive violations: CRITICAL (API probably changed)
  - On CRITICAL: reduce sizing on that broker by 50%
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger("broker.contracts")


@dataclass
class ContractResult:
    broker: str
    endpoint: str
    passed: bool
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    latency_ms: float = 0.0


@dataclass
class ContractViolation:
    broker: str
    endpoint: str
    message: str
    consecutive_count: int
    timestamp: datetime = field(default_factory=datetime.now)


class ContractRunner:
    """Runs contract validations against live broker APIs."""

    def __init__(
        self,
        alert_callback: Optional[Callable[[str, str], None]] = None,
        metrics_callback: Optional[Callable[[str, float, dict], None]] = None,
    ):
        self._alert_cb = alert_callback
        self._metrics_cb = metrics_callback
        self._consecutive_failures: dict[str, int] = {}
        self._results: list[ContractResult] = []
        self._violations: list[ContractViolation] = []

    def validate(
        self,
        broker: str,
        endpoint: str,
        response: object,
        contract_fn: Callable,
    ) -> ContractResult:
        """Validate a response against its contract.

        Args:
            broker: Broker name ("binance", "ibkr", "alpaca")
            endpoint: API endpoint name ("account_balance", "positions")
            response: The actual API response
            contract_fn: Contract validation function

        Returns:
            ContractResult with pass/fail status
        """
        key = f"{broker}.{endpoint}"
        start = time.monotonic()

        try:
            passed, message = contract_fn(response)
        except Exception as e:
            passed = False
            message = f"Contract validation error: {e}"

        latency_ms = (time.monotonic() - start) * 1000

        result = ContractResult(
            broker=broker,
            endpoint=endpoint,
            passed=passed,
            message=message,
            latency_ms=latency_ms,
        )
        self._results.append(result)
        if len(self._results) > 500:
            self._results = self._results[-250:]

        if passed:
            self._consecutive_failures[key] = 0
            if self._metrics_cb:
                self._metrics_cb(
                    f"broker.{broker}.contract_ok", 1.0,
                    {"endpoint": endpoint},
                )
        else:
            count = self._consecutive_failures.get(key, 0) + 1
            self._consecutive_failures[key] = count

            violation = ContractViolation(
                broker=broker,
                endpoint=endpoint,
                message=message,
                consecutive_count=count,
            )
            self._violations.append(violation)

            if self._metrics_cb:
                self._metrics_cb(
                    f"broker.{broker}.contract_violation", 1.0,
                    {"endpoint": endpoint, "message": message},
                )

            if count >= 3:
                level = "critical"
                alert_msg = (
                    f"BROKER CONTRACT CRITICAL: {broker}.{endpoint} "
                    f"{count} consecutive violations. "
                    f"API may have changed. Last: {message}"
                )
            else:
                level = "warning"
                alert_msg = (
                    f"Broker contract violation: {broker}.{endpoint} — "
                    f"{message} ({count}/3)"
                )

            logger.warning(alert_msg)
            if self._alert_cb:
                try:
                    self._alert_cb(alert_msg, level)
                except Exception:
                    pass

        return result

    def get_violations(self, broker: Optional[str] = None) -> list[ContractViolation]:
        if broker:
            return [v for v in self._violations if v.broker == broker]
        return list(self._violations)

    def get_results(self, n: int = 20) -> list[ContractResult]:
        return list(self._results[-n:])

    def is_contract_healthy(self, broker: str) -> bool:
        """True if no active violations for this broker."""
        return all(
            self._consecutive_failures.get(f"{broker}.{ep}", 0) < 3
            for ep in self._get_endpoints(broker)
        )

    def _get_endpoints(self, broker: str) -> list[str]:
        """Get tracked endpoints for a broker."""
        return [
            key.split(".", 1)[1]
            for key in self._consecutive_failures
            if key.startswith(f"{broker}.")
        ]
