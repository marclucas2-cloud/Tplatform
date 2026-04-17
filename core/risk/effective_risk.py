"""Effective Risk Exposure (ERE) — true capital at risk.

ERE = sum of max losses per position, weighted by correlation.
Unlike notional exposure, ERE accounts for:
  - Stop-loss placement (actual max loss per position)
  - Correlation between positions (correlated losses compound)

Usage:
    ere = EffectiveRiskExposure(correlation_engine)
    result = ere.calculate(positions, capital=30000)
    print(result.ere_pct)  # e.g. 18.5%
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np

logger = logging.getLogger(__name__)

# Thresholds
ERE_REDUCE = 0.25  # 25% → reduce exposure
ERE_CRITICAL = 0.35  # 35% → partial kill switch


@dataclass
class PositionRisk:
    """Risk profile for a single position."""
    symbol: str
    strategy: str
    direction: str  # LONG | SHORT
    quantity: float
    entry_price: float
    stop_loss: float
    current_price: float
    max_loss: float  # $ amount at risk to SL
    max_loss_pct: float  # % of capital


@dataclass
class EREResult:
    """Full ERE calculation result."""
    timestamp: datetime
    capital: float
    ere_absolute: float  # $ at risk
    ere_pct: float  # % of capital
    naive_risk: float  # Sum of individual max losses (no correlation adj)
    naive_risk_pct: float
    correlation_penalty: float  # Multiplier from correlation (>= 1.0)
    worst_case_cluster_loss: float  # $ worst cluster scenario
    position_risks: List[PositionRisk]
    level: str  # OK | WARNING | CRITICAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "capital": self.capital,
            "ere_absolute": round(self.ere_absolute, 2),
            "ere_pct": round(self.ere_pct, 4),
            "naive_risk": round(self.naive_risk, 2),
            "naive_risk_pct": round(self.naive_risk_pct, 4),
            "correlation_penalty": round(self.correlation_penalty, 3),
            "worst_case_cluster_loss": round(self.worst_case_cluster_loss, 2),
            "level": self.level,
            "n_positions": len(self.position_risks),
            "positions": [
                {
                    "symbol": p.symbol,
                    "strategy": p.strategy,
                    "direction": p.direction,
                    "max_loss": round(p.max_loss, 2),
                    "max_loss_pct": round(p.max_loss_pct, 4),
                }
                for p in self.position_risks
            ],
        }


class EffectiveRiskExposure:
    """Calculate true portfolio risk from live positions + correlation."""

    def __init__(
        self,
        correlation_engine=None,
        reduce_threshold: float = ERE_REDUCE,
        critical_threshold: float = ERE_CRITICAL,
    ):
        self.correlation_engine = correlation_engine
        self.reduce_threshold = reduce_threshold
        self.critical_threshold = critical_threshold

    def calculate(
        self,
        positions: List[Dict[str, Any]],
        capital: float,
    ) -> EREResult:
        """Calculate ERE from live positions.

        Args:
            positions: List of position dicts with keys:
                symbol, strategy, direction (LONG|SHORT), quantity,
                entry_price, stop_loss, current_price
            capital: Total portfolio capital ($)

        Returns:
            EREResult with risk metrics.
        """
        if not positions or capital <= 0:
            return EREResult(
                timestamp=datetime.now(timezone.utc),
                capital=capital,
                ere_absolute=0.0,
                ere_pct=0.0,
                naive_risk=0.0,
                naive_risk_pct=0.0,
                correlation_penalty=1.0,
                worst_case_cluster_loss=0.0,
                position_risks=[],
                level="OK",
            )

        # 1. Compute max loss per position (distance to SL)
        pos_risks = []
        for pos in positions:
            risk = self._position_max_loss(pos, capital)
            if risk is not None:
                pos_risks.append(risk)

        if not pos_risks:
            return EREResult(
                timestamp=datetime.now(timezone.utc),
                capital=capital,
                ere_absolute=0.0,
                ere_pct=0.0,
                naive_risk=0.0,
                naive_risk_pct=0.0,
                correlation_penalty=1.0,
                worst_case_cluster_loss=0.0,
                position_risks=[],
                level="OK",
            )

        # 2. Naive risk = sum of individual max losses
        naive_risk = sum(p.max_loss for p in pos_risks)
        naive_risk_pct = naive_risk / capital if capital > 0 else 0.0

        # 3. Correlation-adjusted risk
        corr_penalty = self._compute_correlation_penalty(pos_risks)

        # ERE = naive_risk * sqrt(corr_penalty) — penalizes correlated positions
        ere_absolute = naive_risk * np.sqrt(corr_penalty)
        ere_pct = ere_absolute / capital if capital > 0 else 0.0

        # 4. Worst-case cluster loss
        worst_cluster = self._worst_case_cluster(pos_risks)

        # 5. Level
        level = "OK"
        if ere_pct >= self.critical_threshold:
            level = "CRITICAL"
        elif ere_pct >= self.reduce_threshold:
            level = "WARNING"

        result = EREResult(
            timestamp=datetime.now(timezone.utc),
            capital=capital,
            ere_absolute=round(ere_absolute, 2),
            ere_pct=round(ere_pct, 4),
            naive_risk=round(naive_risk, 2),
            naive_risk_pct=round(naive_risk_pct, 4),
            correlation_penalty=round(corr_penalty, 3),
            worst_case_cluster_loss=round(worst_cluster, 2),
            position_risks=pos_risks,
            level=level,
        )

        if level != "OK":
            logger.warning(
                f"ERE {level}: {ere_pct:.1%} of capital "
                f"(${ere_absolute:.0f} / ${capital:.0f}), "
                f"corr_penalty={corr_penalty:.2f}"
            )

        return result

    def should_reduce(self, ere_result: EREResult) -> bool:
        """Check if exposure should be reduced."""
        return ere_result.ere_pct >= self.reduce_threshold

    def should_kill(self, ere_result: EREResult) -> bool:
        """Check if partial kill switch should trigger."""
        return ere_result.ere_pct >= self.critical_threshold

    # ─── Internal ────────────────────────────────────────────────────────

    def _position_max_loss(
        self, pos: Dict[str, Any], capital: float
    ) -> PositionRisk | None:
        """Compute max loss for a single position (to SL)."""
        symbol = pos.get("symbol", "UNKNOWN")
        strategy = pos.get("strategy", "unknown")
        direction = pos.get("direction", pos.get("side", "LONG")).upper()
        qty = abs(float(pos.get("quantity", pos.get("qty", 0))))
        entry = float(pos.get("entry_price", pos.get("avg_entry", 0)))
        sl = float(pos.get("stop_loss", 0))
        current = float(pos.get("current_price", pos.get("market_price", entry)))

        if qty <= 0 or entry <= 0:
            return None

        # If no SL, assume 5% loss (worst default)
        if sl <= 0:
            sl = entry * 0.95 if direction == "LONG" else entry * 1.05

        # Max loss = distance from entry to SL × quantity
        if direction == "LONG":
            loss_per_unit = max(0, entry - sl)
        else:
            loss_per_unit = max(0, sl - entry)

        max_loss = loss_per_unit * qty
        max_loss_pct = max_loss / capital if capital > 0 else 0.0

        return PositionRisk(
            symbol=symbol,
            strategy=strategy,
            direction=direction,
            quantity=qty,
            entry_price=entry,
            stop_loss=sl,
            current_price=current,
            max_loss=max_loss,
            max_loss_pct=max_loss_pct,
        )

    def _compute_correlation_penalty(
        self, pos_risks: List[PositionRisk]
    ) -> float:
        """Compute correlation penalty from live correlation engine.

        Returns a multiplier >= 1.0 that increases with correlation.
        1.0 = uncorrelated, N = fully correlated (N positions).
        """
        if len(pos_risks) < 2:
            return 1.0

        if self.correlation_engine is None:
            # Without engine, assume moderate correlation (1.3x)
            return 1.3

        strategies = list(set(p.strategy for p in pos_risks))
        if len(strategies) < 2:
            return 1.0

        try:
            result = self.correlation_engine.get_correlation_matrix()
            matrix_strats = result.get("strategies", [])
            matrix = np.array(result.get("matrix", []))

            if len(matrix_strats) < 2 or matrix.ndim != 2:
                return 1.3

            # Build sub-matrix for active strategies
            active_indices = []
            for s in strategies:
                if s in matrix_strats:
                    active_indices.append(matrix_strats.index(s))

            if len(active_indices) < 2:
                return 1.3

            n = len(active_indices)
            sub_matrix = np.zeros((n, n))
            for i in range(n):
                for j in range(n):
                    sub_matrix[i, j] = matrix[active_indices[i], active_indices[j]]

            # Penalty = average eigenvalue ratio (captures portfolio variance inflation)
            # For perfectly correlated: penalty = n
            # For uncorrelated: penalty = 1
            eigenvalues = np.linalg.eigvalsh(sub_matrix)
            eigenvalues = np.maximum(eigenvalues, 0)  # Ensure non-negative

            if eigenvalues.sum() > 0:
                # Concentration ratio: largest eigenvalue / sum
                concentration = eigenvalues.max() / eigenvalues.sum()
                # Scale: concentration=1/n → penalty=1, concentration=1 → penalty=n
                penalty = 1.0 + (concentration - 1.0 / n) * n / (1.0 - 1.0 / n) * (n - 1)
                return max(1.0, min(float(penalty), float(n)))
            else:
                return 1.3

        except Exception as e:
            logger.warning(f"Correlation penalty computation failed: {e}")
            return 1.3

    def _worst_case_cluster(self, pos_risks: List[PositionRisk]) -> float:
        """Estimate worst-case loss if a correlated cluster hits SL simultaneously."""
        if not self.correlation_engine or len(pos_risks) < 2:
            return sum(p.max_loss for p in pos_risks)

        clusters = self.correlation_engine.detect_clusters()
        if not clusters:
            return max((p.max_loss for p in pos_risks), default=0.0)

        # For each cluster, sum max losses of positions in that cluster
        worst = 0.0
        for cluster in clusters:
            cluster_loss = sum(
                p.max_loss
                for p in pos_risks
                if p.strategy in cluster.strategies
            )
            worst = max(worst, cluster_loss)

        return worst
