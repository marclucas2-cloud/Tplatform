"""U4-01: Guard Pass Rate Tracker — track which guards kill the most signals.

Logs the pass rate of each guard over a rolling 24h window.
Identifies the "biggest killers" to diagnose why 0 trades.
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger("risk.guard_pass_rate")


@dataclass
class GuardCheck:
    """A single guard check result."""
    timestamp: datetime
    guard_name: str
    passed: bool
    strategy: str = ""
    details: str = ""


class GuardPassRateTracker:
    """Tracks pass rates for each guard in the signal funnel.

    Usage:
        tracker = GuardPassRateTracker()
        tracker.record("regime_check", passed=True, strategy="fx_carry")
        tracker.record("signal_quality", passed=False, strategy="fx_carry")

        report = tracker.get_report()
        print(report["biggest_killers"])
    """

    def __init__(self, window_hours: int = 24):
        self._window = timedelta(hours=window_hours)
        self._checks: deque[GuardCheck] = deque()

    def record(self, guard_name: str, passed: bool, strategy: str = "", details: str = ""):
        """Record a guard check result."""
        now = datetime.now()
        self._checks.append(GuardCheck(
            timestamp=now, guard_name=guard_name, passed=passed,
            strategy=strategy, details=details,
        ))
        self._prune()

    def _prune(self):
        """Remove old entries outside the window."""
        cutoff = datetime.now() - self._window
        while self._checks and self._checks[0].timestamp < cutoff:
            self._checks.popleft()

    def get_report(self) -> Dict[str, Any]:
        """Get pass rate report for all guards."""
        self._prune()

        by_guard: Dict[str, Dict[str, int]] = defaultdict(lambda: {"checked": 0, "passed": 0})

        for check in self._checks:
            by_guard[check.guard_name]["checked"] += 1
            if check.passed:
                by_guard[check.guard_name]["passed"] += 1

        guards = {}
        for name, counts in by_guard.items():
            rate = counts["passed"] / counts["checked"] * 100 if counts["checked"] > 0 else 0
            guards[name] = {
                "checked": counts["checked"],
                "passed": counts["passed"],
                "blocked": counts["checked"] - counts["passed"],
                "rate": round(rate, 1),
            }

        # Overall
        total_signals = max(
            (g["checked"] for g in guards.values()), default=0
        )
        total_passed = min(
            (g["passed"] for g in guards.values()), default=0
        ) if guards else 0

        # Biggest killers: guards with lowest pass rate and > 5 checks
        killers = sorted(
            [(name, g) for name, g in guards.items() if g["checked"] >= 5],
            key=lambda x: x[1]["rate"],
        )

        return {
            "guards": guards,
            "overall_signals": total_signals,
            "overall_pass_rate": round(
                total_passed / total_signals * 100, 1
            ) if total_signals > 0 else 0,
            "biggest_killers": [k[0] for k in killers[:3]],
            "window_hours": self._window.total_seconds() / 3600,
        }

    def format_telegram(self) -> str:
        """Format funnel report for Telegram."""
        report = self.get_report()
        guards = report["guards"]

        if not guards:
            return "Signal Funnel: no data yet"

        lines = [f"Signal Funnel ({int(report['window_hours'])}h):"]

        # Sort by funnel order (approximate)
        order = [
            "market_hours", "kill_switch", "regime_check",
            "activation_matrix", "signal_quality", "confluence",
            "cooldown", "risk_manager", "kelly_sizing",
            "min_size", "spread_check", "capital_check",
            "broker_submit", "fill",
        ]

        for name in order:
            if name in guards:
                g = guards[name]
                arrow = "→" if g["rate"] > 50 else "⬇"
                lines.append(
                    f"  {arrow} {name}: {g['passed']}/{g['checked']} ({g['rate']:.0f}%)"
                )

        # Any guards not in order
        for name, g in guards.items():
            if name not in order:
                lines.append(f"  → {name}: {g['passed']}/{g['checked']} ({g['rate']:.0f}%)")

        if report["biggest_killers"]:
            lines.append(f"  Bottlenecks: {', '.join(report['biggest_killers'])}")

        return "\n".join(lines)


# Global singleton
_tracker: GuardPassRateTracker | None = None


def get_guard_tracker() -> GuardPassRateTracker:
    global _tracker
    if _tracker is None:
        _tracker = GuardPassRateTracker()
    return _tracker
