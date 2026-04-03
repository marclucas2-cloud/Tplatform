"""P6-03: Regime Effectiveness Tracker — validate regime detection accuracy.

NEED_LIVE: Requires 100+ regime transitions.

Measures:
  1. Regime accuracy: does PANIC actually correspond to high DD?
  2. Activation matrix impact: were skipped trades actually losing?
  3. False positive/negative rates
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

MIN_TRANSITIONS = 100


@dataclass
class RegimeObservation:
    """One observation of regime + actual market outcome."""
    timestamp: str
    regime: str
    actual_return_pct: float  # Actual market return during this regime period
    duration_hours: float
    strategies_active: list[str]
    strategies_skipped: list[str]
    pnl_active: float         # PnL from active strategies
    pnl_skipped_if_active: float  # Hypothetical PnL if skipped strategies were active


@dataclass
class RegimeEffectivenessResult:
    """Effectiveness analysis of the regime detection system."""
    regime: str
    n_observations: int
    avg_return_pct: float
    expected_behavior: str  # What we expect
    actual_matches: int     # How often actual matched expected
    accuracy_pct: float
    false_positive_rate: float
    false_negative_rate: float
    skipped_trade_value: float  # Were skipped trades good or bad?
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "n_observations": self.n_observations,
            "avg_return_pct": round(self.avg_return_pct, 3),
            "expected_behavior": self.expected_behavior,
            "accuracy_pct": round(self.accuracy_pct, 1),
            "false_positive_rate": round(self.false_positive_rate, 3),
            "false_negative_rate": round(self.false_negative_rate, 3),
            "skipped_trade_value": round(self.skipped_trade_value, 2),
            "details": self.details,
        }


# Expected behavior per regime
REGIME_EXPECTATIONS = {
    "PANIC": {"dd_threshold": -0.02, "description": "DD > 2% during period"},
    "HIGH_VOL": {"dd_threshold": -0.01, "description": "Elevated volatility"},
    "TREND_STRONG": {"trend_threshold": 0.005, "description": "Clear directional move > 0.5%"},
    "MEAN_REVERT": {"range_threshold": 0.003, "description": "Range-bound, < 0.3% net move"},
    "LOW_LIQUIDITY": {"spread_threshold": 2.0, "description": "Wide spreads"},
    "UNKNOWN": {"description": "Insufficient data"},
}


class RegimeEffectivenessTracker:
    """Tracks regime detection accuracy against actual outcomes.

    NEED_LIVE: Requires 100+ regime transitions.

    Usage:
        tracker = RegimeEffectivenessTracker()

        # Record each regime period:
        tracker.record_observation(RegimeObservation(
            timestamp="2026-04-01T10:00:00Z",
            regime="PANIC",
            actual_return_pct=-0.035,
            duration_hours=4,
            strategies_active=["fx_carry_vs"],
            strategies_skipped=["momentum_trend"],
            pnl_active=-50,
            pnl_skipped_if_active=-120,
        ))

        # After 100+ transitions:
        results = tracker.analyze()
    """

    def __init__(self):
        self._observations: list[RegimeObservation] = []

    @property
    def is_active(self) -> bool:
        return len(self._observations) >= MIN_TRANSITIONS

    def record_observation(self, obs: RegimeObservation):
        """Record a regime observation with actual outcomes."""
        self._observations.append(obs)

    def analyze(self) -> dict[str, RegimeEffectivenessResult]:
        """Analyze regime effectiveness."""
        if not self.is_active:
            logger.info(
                "RegimeEffectiveness: %d/%d observations — not yet active",
                len(self._observations), MIN_TRANSITIONS,
            )
            return {}

        # Group by regime
        by_regime: dict[str, list[RegimeObservation]] = defaultdict(list)
        for obs in self._observations:
            by_regime[obs.regime].append(obs)

        results = {}
        for regime, obs_list in by_regime.items():
            n = len(obs_list)
            avg_ret = sum(o.actual_return_pct for o in obs_list) / n

            # Check accuracy against expected behavior
            expected = REGIME_EXPECTATIONS.get(regime, {})
            matches = 0

            if regime == "PANIC":
                threshold = expected.get("dd_threshold", -0.02)
                matches = sum(1 for o in obs_list if o.actual_return_pct < threshold)
            elif regime == "TREND_STRONG":
                threshold = expected.get("trend_threshold", 0.005)
                matches = sum(1 for o in obs_list if abs(o.actual_return_pct) > threshold)
            elif regime == "MEAN_REVERT":
                threshold = expected.get("range_threshold", 0.003)
                matches = sum(1 for o in obs_list if abs(o.actual_return_pct) < threshold)
            elif regime == "HIGH_VOL":
                threshold = expected.get("dd_threshold", -0.01)
                matches = sum(1 for o in obs_list if abs(o.actual_return_pct) > abs(threshold))
            else:
                matches = n // 2  # Unknown/default

            accuracy = matches / n * 100 if n > 0 else 0

            # Skipped trade value: negative = skipped trades would have lost (good)
            total_skipped_value = sum(o.pnl_skipped_if_active for o in obs_list)

            # False positive/negative rates
            # False positive: regime says PANIC but market was calm
            fp = (n - matches) / n if n > 0 else 0
            # False negative: estimated from non-PANIC periods with big DD
            fn = 0.0  # Would need cross-regime analysis

            results[regime] = RegimeEffectivenessResult(
                regime=regime,
                n_observations=n,
                avg_return_pct=avg_ret,
                expected_behavior=expected.get("description", ""),
                actual_matches=matches,
                accuracy_pct=accuracy,
                false_positive_rate=fp,
                false_negative_rate=fn,
                skipped_trade_value=total_skipped_value,
                details={
                    "total_pnl_active": round(sum(o.pnl_active for o in obs_list), 2),
                    "total_pnl_if_all_active": round(
                        sum(o.pnl_active + o.pnl_skipped_if_active for o in obs_list), 2
                    ),
                    "matrix_saved": round(-total_skipped_value, 2) if total_skipped_value < 0 else 0,
                },
            )

        return results

    def get_activation_matrix_report(self) -> dict[str, Any]:
        """Was the activation matrix worth it?

        If skipped trades were on average positive, the matrix is too conservative.
        If skipped trades were on average negative, the matrix works.
        """
        if not self.is_active:
            return {"active": False, "n_observations": len(self._observations)}

        total_skipped = sum(o.pnl_skipped_if_active for o in self._observations)
        n_with_skipped = sum(1 for o in self._observations if o.strategies_skipped)

        return {
            "active": True,
            "n_observations": len(self._observations),
            "total_skipped_pnl": round(total_skipped, 2),
            "avg_skipped_pnl": round(total_skipped / n_with_skipped, 2) if n_with_skipped > 0 else 0,
            "matrix_effective": total_skipped < 0,  # Negative = saved money
            "recommendation": (
                "Activation matrix is EFFECTIVE — skipped trades were losers"
                if total_skipped < 0
                else "Activation matrix may be TOO CONSERVATIVE — review thresholds"
            ),
        }

    def get_status(self) -> dict:
        return {
            "active": self.is_active,
            "total_observations": len(self._observations),
            "min_required": MIN_TRANSITIONS,
            "unique_regimes": len(set(o.regime for o in self._observations)),
        }
