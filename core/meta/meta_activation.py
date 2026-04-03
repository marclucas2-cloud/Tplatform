"""P6-04: Meta-Strategy Activation — dynamic strategy scoring and allocation.

NEED_LIVE: Requires 200+ trades total, 50+ per strategy, 90+ days.

Modes:
  PASSIVE: Scorer runs, logs recommendations, no impact on allocation
  ACTIVE: Recommendations adjust HRP weights (max 30% delta)

Guard: if meta Sharpe < HRP Sharpe for 30 days -> revert to HRP.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

MIN_TOTAL_TRADES = 200
MIN_TRADES_PER_STRAT = 50
MIN_DAYS_SCORING = 90
MAX_DELTA_VS_HRP = 0.30  # Max 30% weight change vs HRP
REVERT_WINDOW_DAYS = 30


class MetaMode(str, Enum):
    DISABLED = "DISABLED"    # Not enough data
    PASSIVE = "PASSIVE"      # Logging only
    ACTIVE = "ACTIVE"        # Adjusting allocation


@dataclass
class StrategyScore:
    """Performance score for a strategy."""
    strategy: str
    sharpe_rolling: float     # Rolling 30-day Sharpe
    win_rate_rolling: float   # Rolling 30-day win rate
    consistency: float        # How stable is the Sharpe? (lower std = more consistent)
    regime_fit: float         # How well does the strategy fit current regime?
    composite: float = 0.0   # Weighted average

    def compute_composite(self):
        self.composite = (
            self.sharpe_rolling * 0.35
            + self.win_rate_rolling * 0.25
            + self.consistency * 0.20
            + self.regime_fit * 0.20
        )

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "sharpe_rolling": round(self.sharpe_rolling, 3),
            "win_rate_rolling": round(self.win_rate_rolling, 3),
            "consistency": round(self.consistency, 3),
            "regime_fit": round(self.regime_fit, 3),
            "composite": round(self.composite, 3),
        }


@dataclass
class MetaRecommendation:
    """Meta-layer's recommendation for allocation adjustment."""
    strategy: str
    current_weight: float
    recommended_weight: float
    delta: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "current_weight": round(self.current_weight, 4),
            "recommended_weight": round(self.recommended_weight, 4),
            "delta": round(self.delta, 4),
            "delta_pct": round(self.delta / self.current_weight * 100, 1) if self.current_weight > 0 else 0,
            "reason": self.reason,
        }


@dataclass
class Prerequisites:
    """Check whether meta-activation prerequisites are met."""
    total_trades: int = 0
    trades_per_strategy: dict[str, int] = field(default_factory=dict)
    scoring_days: int = 0
    met: bool = False
    missing: list[str] = field(default_factory=list)

    def check(self):
        self.missing = []
        if self.total_trades < MIN_TOTAL_TRADES:
            self.missing.append(f"Need {MIN_TOTAL_TRADES} total trades (have {self.total_trades})")
        for strat, n in self.trades_per_strategy.items():
            if n < MIN_TRADES_PER_STRAT:
                self.missing.append(f"{strat}: need {MIN_TRADES_PER_STRAT} trades (have {n})")
        if self.scoring_days < MIN_DAYS_SCORING:
            self.missing.append(f"Need {MIN_DAYS_SCORING} days of scoring (have {self.scoring_days})")
        self.met = len(self.missing) == 0

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "min_total": MIN_TOTAL_TRADES,
            "scoring_days": self.scoring_days,
            "min_days": MIN_DAYS_SCORING,
            "met": self.met,
            "missing": self.missing,
        }


class MetaActivation:
    """Meta-strategy layer that dynamically adjusts allocation based on live scoring.

    NEED_LIVE: 200+ trades total, 50+ per strategy, 90+ days scoring.

    Usage:
        meta = MetaActivation()

        # Check prerequisites
        prereqs = meta.check_prerequisites(
            total_trades=250,
            trades_per_strategy={"fx_carry": 80, "btc_momentum": 60},
            scoring_days=95,
        )
        if prereqs.met:
            meta.activate(MetaMode.PASSIVE)

        # Score strategies
        meta.update_score("fx_carry", sharpe=1.5, win_rate=0.55,
                          consistency=0.8, regime_fit=0.9)

        # Get recommendations
        recs = meta.get_recommendations(
            current_weights={"fx_carry": 0.15, "btc_momentum": 0.10}
        )
    """

    def __init__(self):
        self._mode = MetaMode.DISABLED
        self._scores: dict[str, StrategyScore] = {}
        self._recommendation_log: list[dict] = []
        self._activated_at: datetime | None = None
        self._hrp_sharpe_30d: float | None = None
        self._meta_sharpe_30d: float | None = None

    @property
    def mode(self) -> MetaMode:
        return self._mode

    def check_prerequisites(
        self,
        total_trades: int,
        trades_per_strategy: dict[str, int],
        scoring_days: int,
    ) -> Prerequisites:
        """Check if meta-activation prerequisites are met."""
        prereqs = Prerequisites(
            total_trades=total_trades,
            trades_per_strategy=trades_per_strategy,
            scoring_days=scoring_days,
        )
        prereqs.check()
        return prereqs

    def activate(self, mode: MetaMode):
        """Activate meta-layer in PASSIVE or ACTIVE mode."""
        if mode == MetaMode.ACTIVE and self._mode != MetaMode.PASSIVE:
            logger.warning("Cannot go directly to ACTIVE — must pass through PASSIVE first")
            return

        self._mode = mode
        self._activated_at = datetime.now(UTC)
        logger.info("Meta-strategy layer activated in %s mode", mode.value)

    def update_score(
        self,
        strategy: str,
        sharpe: float,
        win_rate: float,
        consistency: float,
        regime_fit: float,
    ):
        """Update a strategy's performance score."""
        score = StrategyScore(
            strategy=strategy,
            sharpe_rolling=sharpe,
            win_rate_rolling=win_rate,
            consistency=consistency,
            regime_fit=regime_fit,
        )
        score.compute_composite()
        self._scores[strategy] = score

    def get_recommendations(
        self,
        current_weights: dict[str, float],
    ) -> list[MetaRecommendation]:
        """Get allocation adjustment recommendations.

        In PASSIVE mode: logs recommendations but doesn't apply.
        In ACTIVE mode: recommendations are actionable.
        """
        if self._mode == MetaMode.DISABLED:
            return []

        if not self._scores:
            return []

        recs = []
        # Rank strategies by composite score
        ranked = sorted(self._scores.values(), key=lambda s: -s.composite)

        # Compute score-weighted target weights
        total_score = sum(s.composite for s in ranked if s.composite > 0)
        if total_score == 0:
            return []

        target_weights = {}
        for score in ranked:
            if score.strategy in current_weights:
                target = score.composite / total_score
                target_weights[score.strategy] = target

        # Compute deltas with max cap
        for strat, target_w in target_weights.items():
            current_w = current_weights.get(strat, 0)
            delta = target_w - current_w

            # Cap delta
            delta = max(-MAX_DELTA_VS_HRP, min(MAX_DELTA_VS_HRP, delta))

            if abs(delta) > 0.01:  # Only report meaningful changes
                reason = ""
                if delta > 0:
                    score = self._scores.get(strat)
                    reason = f"High score ({score.composite:.2f}) — overweight"
                else:
                    score = self._scores.get(strat)
                    reason = f"Low score ({score.composite:.2f}) — underweight"

                rec = MetaRecommendation(
                    strategy=strat,
                    current_weight=current_w,
                    recommended_weight=current_w + delta,
                    delta=delta,
                    reason=reason,
                )
                recs.append(rec)

        # Log recommendation
        self._recommendation_log.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "mode": self._mode.value,
            "recommendations": [r.to_dict() for r in recs],
        })

        if self._mode == MetaMode.PASSIVE:
            for rec in recs:
                logger.info(
                    "META [PASSIVE]: %s would %s from %.1f%% to %.1f%%",
                    rec.strategy,
                    "increase" if rec.delta > 0 else "decrease",
                    rec.current_weight * 100,
                    rec.recommended_weight * 100,
                )

        return recs

    def apply_recommendations(
        self,
        current_weights: dict[str, float],
    ) -> dict[str, float] | None:
        """Apply meta recommendations to weights. Only in ACTIVE mode."""
        if self._mode != MetaMode.ACTIVE:
            logger.info("Meta not in ACTIVE mode — recommendations are logged only")
            return None

        recs = self.get_recommendations(current_weights)
        new_weights = dict(current_weights)

        for rec in recs:
            new_weights[rec.strategy] = rec.recommended_weight

        # Normalize
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {s: w / total for s, w in new_weights.items()}

        return new_weights

    def check_revert_condition(
        self,
        hrp_sharpe_30d: float,
        meta_sharpe_30d: float,
    ) -> bool:
        """Check if meta should revert to HRP.

        If meta Sharpe < HRP Sharpe for 30 consecutive days -> REVERT.
        """
        self._hrp_sharpe_30d = hrp_sharpe_30d
        self._meta_sharpe_30d = meta_sharpe_30d

        if meta_sharpe_30d < hrp_sharpe_30d:
            logger.warning(
                "META underperforming HRP: meta=%.2f vs hrp=%.2f",
                meta_sharpe_30d, hrp_sharpe_30d,
            )
            if self._mode == MetaMode.ACTIVE:
                self._mode = MetaMode.PASSIVE
                logger.warning("META REVERTED to PASSIVE — underperforming HRP")
                return True
        return False

    def get_status(self) -> dict:
        """Get meta-activation status."""
        return {
            "mode": self._mode.value,
            "activated_at": self._activated_at.isoformat() if self._activated_at else None,
            "strategies_scored": len(self._scores),
            "scores": {k: v.to_dict() for k, v in self._scores.items()},
            "recommendation_count": len(self._recommendation_log),
            "hrp_sharpe_30d": self._hrp_sharpe_30d,
            "meta_sharpe_30d": self._meta_sharpe_30d,
        }
