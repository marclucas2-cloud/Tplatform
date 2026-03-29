"""Leverage Adaptation Engine — real-time leverage adjustment.

Dynamically reduces leverage when risk conditions deteriorate:
  - High correlation → -30%
  - Drawdown > 3% → -50%
  - Stress regime → minimal leverage

Usage:
    adapter = LeverageAdapter(correlation_engine, ere_calculator)
    mult = adapter.get_multiplier(portfolio_state)
    effective_leverage = base_leverage * mult
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class LeverageDecision:
    timestamp: datetime
    base_leverage: float
    multiplier: float
    effective_leverage: float
    factors: Dict[str, float]  # Each factor's contribution
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "base_leverage": self.base_leverage,
            "multiplier": round(self.multiplier, 3),
            "effective_leverage": round(self.effective_leverage, 3),
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
            "reason": self.reason,
        }


class LeverageAdapter:
    """Adapt portfolio leverage in real-time based on risk conditions."""

    # Factor weights
    CORR_THRESHOLD_HIGH = 0.70
    CORR_PENALTY = 0.70  # -30% if correlation high

    DD_THRESHOLD_MILD = 0.02  # 2% DD
    DD_PENALTY_MILD = 0.80  # -20%

    DD_THRESHOLD_SEVERE = 0.03  # 3% DD
    DD_PENALTY_SEVERE = 0.50  # -50%

    DD_THRESHOLD_CRITICAL = 0.05  # 5% DD
    DD_PENALTY_CRITICAL = 0.20  # -80%

    ERE_THRESHOLD = 0.25  # 25% ERE
    ERE_PENALTY = 0.70  # -30%

    # Regime multipliers
    REGIME_LEVERAGE = {
        "low_vol": 1.0,
        "normal": 1.0,
        "high_vol": 0.70,
        "crisis": 0.30,
    }

    def __init__(
        self,
        correlation_engine=None,
        ere_calculator=None,
        min_multiplier: float = 0.10,
        max_multiplier: float = 1.0,
    ):
        self.correlation_engine = correlation_engine
        self.ere_calculator = ere_calculator
        self.min_multiplier = min_multiplier
        self.max_multiplier = max_multiplier

    def get_multiplier(
        self,
        base_leverage: float = 1.0,
        drawdown_pct: float = 0.0,
        correlation_score: Optional[float] = None,
        ere_pct: Optional[float] = None,
        regime: str = "normal",
    ) -> LeverageDecision:
        """Compute leverage multiplier from current conditions.

        Args:
            base_leverage: Target leverage (from LeverageManager phase).
            drawdown_pct: Current drawdown as positive fraction (0.03 = 3%).
            correlation_score: Global correlation score (0-1) from engine.
            ere_pct: ERE as fraction of capital.
            regime: Market regime string.

        Returns:
            LeverageDecision with effective leverage.
        """
        factors = {}
        reasons = []

        # 1. Correlation factor
        corr_score = correlation_score
        if corr_score is None and self.correlation_engine is not None:
            try:
                corr_score = self.correlation_engine.get_global_score()
            except Exception:
                corr_score = None

        if corr_score is not None and corr_score >= self.CORR_THRESHOLD_HIGH:
            factors["correlation"] = self.CORR_PENALTY
            reasons.append(f"corr={corr_score:.2f}>{self.CORR_THRESHOLD_HIGH}")
        else:
            factors["correlation"] = 1.0

        # 2. Drawdown factor (cascading severity)
        dd = abs(drawdown_pct)
        if dd >= self.DD_THRESHOLD_CRITICAL:
            factors["drawdown"] = self.DD_PENALTY_CRITICAL
            reasons.append(f"DD={dd:.1%} CRITICAL")
        elif dd >= self.DD_THRESHOLD_SEVERE:
            factors["drawdown"] = self.DD_PENALTY_SEVERE
            reasons.append(f"DD={dd:.1%} severe")
        elif dd >= self.DD_THRESHOLD_MILD:
            factors["drawdown"] = self.DD_PENALTY_MILD
            reasons.append(f"DD={dd:.1%} mild")
        else:
            factors["drawdown"] = 1.0

        # 3. ERE factor
        ere = ere_pct
        if ere is not None and ere >= self.ERE_THRESHOLD:
            factors["ere"] = self.ERE_PENALTY
            reasons.append(f"ERE={ere:.1%}>{self.ERE_THRESHOLD:.0%}")
        else:
            factors["ere"] = 1.0

        # 4. Regime factor
        regime_mult = self.REGIME_LEVERAGE.get(regime, 1.0)
        factors["regime"] = regime_mult
        if regime_mult < 1.0:
            reasons.append(f"regime={regime}")

        # Combined multiplier = product of all factors
        multiplier = 1.0
        for f in factors.values():
            multiplier *= f

        multiplier = max(self.min_multiplier, min(self.max_multiplier, multiplier))
        effective = base_leverage * multiplier

        reason = "; ".join(reasons) if reasons else "all clear"

        if multiplier < 0.8:
            logger.warning(
                f"Leverage reduced: {base_leverage:.1f}x → {effective:.2f}x "
                f"(mult={multiplier:.2f}, {reason})"
            )

        return LeverageDecision(
            timestamp=datetime.utcnow(),
            base_leverage=base_leverage,
            multiplier=multiplier,
            effective_leverage=effective,
            factors=factors,
            reason=reason,
        )
