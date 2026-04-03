"""U4-02: Adaptive Guards — thermostat, not wall.

Guards adjust based on utilization rate:
  utilization < 20%: permissivity 1.3 (guards 30% more permissive)
  utilization 20-40%: permissivity 1.15
  utilization 40-60%: permissivity 1.0 (nominal)
  utilization 60-80%: permissivity 0.9
  utilization > 80%: permissivity 0.8 (guards 20% stricter)

Guards that NEVER adjust (safety absolue):
  - Kill switch, SL obligatoire, daily loss limit, emergency close
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict

logger = logging.getLogger("risk.adaptive_guards")


# Permissivity by utilization band
PERMISSIVITY_TABLE = [
    (0.20, 1.30),   # < 20% utilization → 30% more permissive
    (0.40, 1.15),   # 20-40% → 15% more permissive
    (0.60, 1.00),   # 40-60% → nominal
    (0.80, 0.90),   # 60-80% → 10% stricter
    (1.01, 0.80),   # > 80% → 20% stricter
]

# Guards that never adjust
FIXED_GUARDS = {
    "kill_switch",
    "sl_mandatory",
    "daily_loss_limit",
    "hourly_loss_limit",
    "emergency_close",
    "margin_block",
    "weekly_loss_limit",
}


@dataclass
class AdjustedThreshold:
    """A threshold adjusted by permissivity."""
    guard_name: str
    nominal: float
    adjusted: float
    permissivity: float
    utilization_pct: float

    def to_dict(self) -> dict:
        return {
            "guard": self.guard_name,
            "nominal": self.nominal,
            "adjusted": round(self.adjusted, 4),
            "permissivity": self.permissivity,
            "utilization_pct": round(self.utilization_pct, 1),
        }


class AdaptiveGuards:
    """Adjusts risk thresholds based on capital utilization.

    Usage:
        guards = AdaptiveGuards()
        adjusted = guards.adjust("cash_min_pct", 0.10, utilization_pct=15.0)
        # adjusted.adjusted = 0.077 (10% / 1.3)
    """

    def get_permissivity(self, utilization_pct: float) -> float:
        """Get permissivity multiplier for current utilization."""
        util = utilization_pct / 100.0
        for threshold, perm in PERMISSIVITY_TABLE:
            if util < threshold:
                return perm
        return PERMISSIVITY_TABLE[-1][1]

    def adjust(
        self,
        guard_name: str,
        nominal_value: float,
        utilization_pct: float,
    ) -> AdjustedThreshold:
        """Adjust a threshold based on utilization.

        For "higher is more restrictive" thresholds (cash_min, cash_reserve):
            adjusted = nominal / permissivity (lower when permissive)

        For "lower is more restrictive" thresholds (max_position, gross_max):
            adjusted = nominal * permissivity (higher when permissive)

        For integer thresholds (max_positions):
            adjusted = int(nominal * permissivity)
        """
        if guard_name in FIXED_GUARDS:
            return AdjustedThreshold(
                guard_name=guard_name,
                nominal=nominal_value,
                adjusted=nominal_value,
                permissivity=1.0,
                utilization_pct=utilization_pct,
            )

        perm = self.get_permissivity(utilization_pct)

        # Guards where LOWER value = more restrictive (relax by dividing)
        lower_is_stricter = {
            "cash_min_pct", "cash_reserve_pct", "min_cash_pct",
            "signal_quality_threshold",
        }

        # Guards where HIGHER value = more restrictive (relax by multiplying)
        higher_is_stricter = {
            "max_position_pct", "max_strategy_pct", "max_long_pct",
            "max_short_pct", "max_gross_pct", "max_positions",
            "max_fx_notional_pct", "max_fx_margin_pct",
        }

        if guard_name in lower_is_stricter:
            adjusted = nominal_value / perm
        elif guard_name in higher_is_stricter:
            adjusted = nominal_value * perm
            # Cap for safety
            if "pct" in guard_name:
                adjusted = min(adjusted, 0.95)  # Never above 95%
            if guard_name == "max_positions":
                adjusted = int(adjusted)
        else:
            # Unknown guard → don't adjust
            adjusted = nominal_value

        result = AdjustedThreshold(
            guard_name=guard_name,
            nominal=nominal_value,
            adjusted=adjusted,
            permissivity=perm,
            utilization_pct=utilization_pct,
        )

        if perm != 1.0:
            logger.info(
                "GUARD_ADJUST|%s|nominal=%.4f|adjusted=%.4f|perm=%.2f|util=%.0f%%",
                guard_name, nominal_value, adjusted, perm, utilization_pct,
            )

        return result

    def adjust_all(
        self,
        thresholds: Dict[str, float],
        utilization_pct: float,
    ) -> Dict[str, AdjustedThreshold]:
        """Adjust all thresholds at once."""
        return {
            name: self.adjust(name, value, utilization_pct)
            for name, value in thresholds.items()
        }
