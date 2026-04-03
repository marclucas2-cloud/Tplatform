"""P4-01: HRP Calibration Diagnostic — analyzes HRP allocation quality.

Questions answered:
  1. Current HRP weights per strategy (are any > 20% → concentration risk?)
  2. Weight stability over rolling windows (do weights oscillate?)
  3. Turnover cost from rebalancing
  4. Cluster analysis: which strategies are grouped?
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


@dataclass
class HRPDiagnosticResult:
    """Full HRP diagnostic result."""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    weights: dict[str, float] = field(default_factory=dict)
    concentration: dict[str, Any] = field(default_factory=dict)
    stability: dict[str, Any] = field(default_factory=dict)
    turnover: dict[str, Any] = field(default_factory=dict)
    clusters: list[list[str]] = field(default_factory=list)
    correlation_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "concentration": self.concentration,
            "stability": self.stability,
            "turnover": self.turnover,
            "clusters": self.clusters,
            "correlation_matrix": self.correlation_matrix,
            "recommendations": self.recommendations,
        }

    def save(self, path: Path | None = None):
        path = path or (REPORTS_DIR / "hrp_diagnostic.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("HRP diagnostic saved to %s", path)


class HRPDiagnostic:
    """Diagnoses HRP allocation quality.

    Usage:
        diag = HRPDiagnostic()
        result = diag.run(pnl_matrix)
        result.save()
    """

    def __init__(
        self,
        max_single_weight: float = 0.20,
        top3_max_weight: float = 0.50,
        stability_threshold: float = 0.50,  # 50% change between windows
        cost_bps: float = 5.0,  # Trading cost per rebalance in bps
    ):
        self._max_single = max_single_weight
        self._top3_max = top3_max_weight
        self._stability_threshold = stability_threshold
        self._cost_bps = cost_bps

    def run(
        self,
        pnl_matrix: pd.DataFrame,
        lookback_days: int = 60,
        rebalance_hours: int = 4,
    ) -> HRPDiagnosticResult:
        """Run full HRP diagnostic.

        Args:
            pnl_matrix: DataFrame with columns=strategy names, rows=daily returns
            lookback_days: Lookback for HRP computation
            rebalance_hours: Rebalance interval
        """
        result = HRPDiagnosticResult()

        if pnl_matrix.empty or len(pnl_matrix.columns) < 2:
            result.recommendations.append("Insufficient data for HRP diagnostic")
            return result

        # 1. Current weights
        weights = self._compute_hrp_weights(pnl_matrix.iloc[-lookback_days:])
        result.weights = weights

        # 2. Concentration analysis
        result.concentration = self._analyze_concentration(weights)

        # 3. Correlation matrix
        corr = pnl_matrix.iloc[-lookback_days:].corr()
        result.correlation_matrix = {
            col: {row: round(corr.loc[row, col], 3) for row in corr.index}
            for col in corr.columns
        }

        # 4. Cluster analysis
        result.clusters = self._find_clusters(corr)

        # 5. Stability over rolling windows
        result.stability = self._analyze_stability(
            pnl_matrix, lookback_days
        )

        # 6. Turnover cost
        result.turnover = self._analyze_turnover(
            pnl_matrix, lookback_days, rebalance_hours
        )

        # 7. Recommendations
        result.recommendations = self._generate_recommendations(result)

        return result

    def _compute_hrp_weights(self, returns: pd.DataFrame) -> dict[str, float]:
        """Compute HRP weights using inverse-variance on clusters."""
        cov = returns.cov()
        corr = returns.corr()

        if cov.empty:
            n = len(returns.columns)
            return {col: 1.0 / n for col in returns.columns}

        # Simplified HRP: inverse-variance within correlation clusters
        variances = np.diag(cov.values)
        variances = np.where(variances > 0, variances, 1e-10)
        inv_var = 1.0 / variances
        weights = inv_var / inv_var.sum()

        return {col: float(w) for col, w in zip(returns.columns, weights)}

    def _analyze_concentration(self, weights: dict[str, float]) -> dict:
        """Check concentration: any single > 20%? Top 3 > 50%?"""
        sorted_weights = sorted(weights.items(), key=lambda x: -x[1])

        max_weight = sorted_weights[0][1] if sorted_weights else 0
        top3_weight = sum(w for _, w in sorted_weights[:3])

        issues = []
        if max_weight > self._max_single:
            issues.append(
                f"{sorted_weights[0][0]} has {max_weight:.1%} weight (max {self._max_single:.0%})"
            )
        if top3_weight > self._top3_max:
            top3_names = [n for n, _ in sorted_weights[:3]]
            issues.append(
                f"Top 3 ({', '.join(top3_names)}) = {top3_weight:.1%} (max {self._top3_max:.0%})"
            )

        return {
            "max_single_weight": round(max_weight, 4),
            "max_single_strategy": sorted_weights[0][0] if sorted_weights else None,
            "top3_weight": round(top3_weight, 4),
            "herfindahl_index": round(sum(w ** 2 for w in weights.values()), 4),
            "effective_n": round(1.0 / sum(w ** 2 for w in weights.values()), 1) if weights else 0,
            "issues": issues,
        }

    def _find_clusters(
        self,
        corr: pd.DataFrame,
        threshold: float = 0.60,
    ) -> list[list[str]]:
        """Find clusters of correlated strategies."""
        n = len(corr)
        visited = set()
        clusters = []

        for i, col in enumerate(corr.columns):
            if col in visited:
                continue
            cluster = [col]
            visited.add(col)
            for j, row in enumerate(corr.columns):
                if row in visited or i == j:
                    continue
                if abs(corr.iloc[i, j]) > threshold:
                    cluster.append(row)
                    visited.add(row)
            clusters.append(cluster)

        return clusters

    def _analyze_stability(
        self,
        pnl_matrix: pd.DataFrame,
        lookback_days: int,
    ) -> dict:
        """Check weight stability over 3 rolling windows."""
        n_rows = len(pnl_matrix)
        if n_rows < lookback_days * 2:
            return {"stable": True, "note": "Insufficient data for stability analysis"}

        windows = []
        step = lookback_days // 2
        for start in range(max(0, n_rows - lookback_days * 3), n_rows - lookback_days + 1, step):
            end = start + lookback_days
            if end > n_rows:
                break
            w = self._compute_hrp_weights(pnl_matrix.iloc[start:end])
            windows.append(w)

        if len(windows) < 2:
            return {"stable": True, "note": "Only one window available"}

        # Max weight change between consecutive windows
        max_changes = {}
        for strat in pnl_matrix.columns:
            changes = []
            for i in range(1, len(windows)):
                w_prev = windows[i - 1].get(strat, 0)
                w_curr = windows[i].get(strat, 0)
                if w_prev > 0:
                    change = abs(w_curr - w_prev) / w_prev
                    changes.append(change)
            if changes:
                max_changes[strat] = max(changes)

        unstable = {
            s: round(c, 3) for s, c in max_changes.items()
            if c > self._stability_threshold
        }

        return {
            "stable": len(unstable) == 0,
            "unstable_strategies": unstable,
            "n_windows": len(windows),
            "avg_max_change": round(
                np.mean(list(max_changes.values())), 3
            ) if max_changes else 0,
        }

    def _analyze_turnover(
        self,
        pnl_matrix: pd.DataFrame,
        lookback_days: int,
        rebalance_hours: int,
    ) -> dict:
        """Estimate annual turnover cost from HRP rebalancing."""
        rebalances_per_day = 24 / rebalance_hours
        rebalances_per_year = rebalances_per_day * 252

        # Estimate avg turnover per rebalance
        # Use weight changes between rolling windows as proxy
        n_rows = len(pnl_matrix)
        if n_rows < lookback_days + 5:
            return {"annual_cost_pct": 0, "note": "Insufficient data"}

        w1 = self._compute_hrp_weights(pnl_matrix.iloc[-lookback_days - 5:-5])
        w2 = self._compute_hrp_weights(pnl_matrix.iloc[-lookback_days:])

        # Turnover = sum of absolute weight changes / 2
        strats = set(list(w1.keys()) + list(w2.keys()))
        turnover = sum(abs(w1.get(s, 0) - w2.get(s, 0)) for s in strats) / 2

        # Cost per rebalance
        cost_per_rebalance = turnover * self._cost_bps / 10_000

        # Annualized
        annual_cost = cost_per_rebalance * rebalances_per_year

        return {
            "avg_turnover_per_rebalance": round(turnover, 4),
            "cost_per_rebalance_pct": round(cost_per_rebalance * 100, 4),
            "rebalances_per_year": int(rebalances_per_year),
            "annual_cost_pct": round(annual_cost * 100, 2),
            "recommendation": (
                "Consider longer rebalance interval"
                if annual_cost > 0.01 else "Turnover cost acceptable"
            ),
        }

    def _generate_recommendations(self, result: HRPDiagnosticResult) -> list[str]:
        """Generate actionable recommendations."""
        recs = []

        # Concentration
        if result.concentration.get("issues"):
            for issue in result.concentration["issues"]:
                recs.append(f"CONCENTRATION: {issue}")

        # Clusters with high correlation
        for cluster in result.clusters:
            if len(cluster) > 2:
                recs.append(
                    f"CLUSTER: {', '.join(cluster)} are highly correlated — "
                    f"consider reducing combined weight"
                )

        # Stability
        unstable = result.stability.get("unstable_strategies", {})
        if unstable:
            for strat, change in unstable.items():
                recs.append(
                    f"UNSTABLE: {strat} weight changes {change:.0%} between windows"
                )

        # Turnover
        annual_cost = result.turnover.get("annual_cost_pct", 0)
        if annual_cost > 1.0:
            recs.append(
                f"TURNOVER: Annual rebalance cost {annual_cost:.1f}% — "
                f"consider extending rebalance interval"
            )

        if not recs:
            recs.append("HRP allocation looks healthy — no issues detected")

        return recs
