"""U3-01: Allocation Gap Tracker — monitors target vs actual allocation.

Statuses:
  ALIGNED:    |gap| < 5%  — all good
  DRIFTING:   |gap| 5-15% — normal intraday
  MISALIGNED: |gap| > 15% for > 4h — problem
  BLOCKED:    actual = 0% for > 24h — strategy blocked
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger("alloc.gap")


class GapStatus:
    ALIGNED = "ALIGNED"
    DRIFTING = "DRIFTING"
    MISALIGNED = "MISALIGNED"
    BLOCKED = "BLOCKED"


@dataclass
class StrategyGap:
    strategy: str
    target_weight: float
    actual_weight: float
    gap: float
    gap_abs: float
    status: str
    duration_hours: float = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "target_pct": round(self.target_weight * 100, 1),
            "actual_pct": round(self.actual_weight * 100, 1),
            "gap_pct": round(self.gap * 100, 1),
            "status": self.status,
            "duration_hours": round(self.duration_hours, 1),
            "reason": self.reason,
        }


class AllocationGapTracker:
    """Tracks target vs actual allocation for each strategy.

    Usage:
        tracker = AllocationGapTracker()
        gaps = tracker.check(
            target_weights={"fx_carry": 0.25, "btc_momentum": 0.15},
            actual_weights={"fx_carry": 0.0, "btc_momentum": 0.08},
        )
    """

    def __init__(self):
        self._misaligned_since: Dict[str, datetime] = {}
        self._blocked_since: Dict[str, datetime] = {}

    def check(
        self,
        target_weights: Dict[str, float],
        actual_weights: Dict[str, float],
    ) -> List[StrategyGap]:
        now = datetime.now()
        gaps = []

        for strat, target in target_weights.items():
            actual = actual_weights.get(strat, 0)
            gap = target - actual
            gap_abs = abs(gap)

            # Determine status
            if actual == 0 and target > 0.02:
                status = GapStatus.BLOCKED
                if strat not in self._blocked_since:
                    self._blocked_since[strat] = now
                duration = (now - self._blocked_since[strat]).total_seconds() / 3600
            elif gap_abs > 0.15:
                status = GapStatus.MISALIGNED
                if strat not in self._misaligned_since:
                    self._misaligned_since[strat] = now
                duration = (now - self._misaligned_since[strat]).total_seconds() / 3600
                # Clear blocked tracker if no longer blocked
                self._blocked_since.pop(strat, None)
            elif gap_abs > 0.05:
                status = GapStatus.DRIFTING
                duration = 0
                self._misaligned_since.pop(strat, None)
                self._blocked_since.pop(strat, None)
            else:
                status = GapStatus.ALIGNED
                duration = 0
                self._misaligned_since.pop(strat, None)
                self._blocked_since.pop(strat, None)

            gaps.append(StrategyGap(
                strategy=strat,
                target_weight=target,
                actual_weight=actual,
                gap=gap,
                gap_abs=gap_abs,
                status=status,
                duration_hours=duration,
            ))

        return gaps

    def get_blocked(self, gaps: List[StrategyGap], min_hours: float = 24) -> List[StrategyGap]:
        return [g for g in gaps if g.status == GapStatus.BLOCKED and g.duration_hours >= min_hours]

    def get_misaligned(self, gaps: List[StrategyGap], min_hours: float = 4) -> List[StrategyGap]:
        return [g for g in gaps if g.status == GapStatus.MISALIGNED and g.duration_hours >= min_hours]
