"""Dynamic Risk Budget Allocator — adapt sizing by correlation and regime.

Allocates risk budget per strategy based on:
  - Number of active correlated strategies
  - Current market regime
  - Correlation clusters

Formula: risk_budget_strat = base_risk / sqrt(n_correlated_strats)

Usage:
    allocator = RiskBudgetAllocator(correlation_engine)
    budgets = allocator.allocate(active_strategies, regime="normal")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np

logger = logging.getLogger(__name__)

# Base risk budget per strategy (% of capital)
BASE_RISK_PCT = 0.02  # 2% max loss per strategy
MAX_RISK_PCT = 0.03  # 3% absolute max
MIN_RISK_PCT = 0.005  # 0.5% minimum

# Regime multipliers
REGIME_MULTIPLIERS = {
    "low_vol": 1.2,    # Increase risk in calm markets
    "normal": 1.0,
    "high_vol": 0.7,   # Reduce in volatile markets
    "crisis": 0.4,     # Severely reduce in crisis
}


@dataclass
class StrategyBudget:
    strategy: str
    risk_budget_pct: float  # % of capital for this strategy
    risk_budget_abs: float  # $ risk budget
    base_risk: float
    correlation_adj: float  # Multiplier from correlation (<=1.0)
    regime_adj: float  # Multiplier from regime
    cluster_penalty: float  # Extra penalty if in correlated cluster
    reason: str


@dataclass
class BudgetAllocation:
    total_risk_budget_pct: float
    total_risk_budget_abs: float
    capital: float
    regime: str
    n_strategies: int
    n_clusters: int
    budgets: List[StrategyBudget]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_risk_budget_pct": round(self.total_risk_budget_pct, 4),
            "total_risk_budget_abs": round(self.total_risk_budget_abs, 2),
            "capital": self.capital,
            "regime": self.regime,
            "n_strategies": self.n_strategies,
            "n_clusters": self.n_clusters,
            "budgets": [
                {
                    "strategy": b.strategy,
                    "risk_budget_pct": round(b.risk_budget_pct, 4),
                    "risk_budget_abs": round(b.risk_budget_abs, 2),
                    "correlation_adj": round(b.correlation_adj, 3),
                    "regime_adj": round(b.regime_adj, 2),
                    "reason": b.reason,
                }
                for b in self.budgets
            ],
        }


class RiskBudgetAllocator:
    """Dynamically allocate risk budget per strategy."""

    def __init__(
        self,
        correlation_engine=None,
        base_risk: float = BASE_RISK_PCT,
        max_risk: float = MAX_RISK_PCT,
        min_risk: float = MIN_RISK_PCT,
    ):
        self.correlation_engine = correlation_engine
        self.base_risk = base_risk
        self.max_risk = max_risk
        self.min_risk = min_risk

    def allocate(
        self,
        active_strategies: List[str],
        capital: float,
        regime: str = "normal",
    ) -> BudgetAllocation:
        """Allocate risk budget to each active strategy.

        Args:
            active_strategies: List of strategy names currently trading.
            capital: Total portfolio capital ($).
            regime: Market regime (low_vol, normal, high_vol, crisis).
        """
        if not active_strategies or capital <= 0:
            return BudgetAllocation(
                total_risk_budget_pct=0.0,
                total_risk_budget_abs=0.0,
                capital=capital,
                regime=regime,
                n_strategies=0,
                n_clusters=0,
                budgets=[],
            )

        regime_mult = REGIME_MULTIPLIERS.get(regime, 1.0)

        # Get correlation clusters
        clusters = []
        strategy_cluster_map: Dict[str, int] = {}
        cluster_sizes: Dict[int, int] = {}

        if self.correlation_engine is not None:
            try:
                clusters = self.correlation_engine.detect_clusters()
                for c in clusters:
                    for s in c.strategies:
                        strategy_cluster_map[s] = c.cluster_id
                        cluster_sizes[c.cluster_id] = len(c.strategies)
            except Exception as e:
                logger.warning(f"Failed to get correlation clusters: {e}")

        budgets = []
        for strat in active_strategies:
            # Base risk
            base = self.base_risk

            # Correlation adjustment: reduce if in a cluster
            corr_adj = 1.0
            cluster_penalty = 1.0
            reason_parts = []

            if strat in strategy_cluster_map:
                cid = strategy_cluster_map[strat]
                n_in_cluster = cluster_sizes.get(cid, 1)
                # Reduce proportionally to sqrt(cluster size)
                corr_adj = 1.0 / np.sqrt(n_in_cluster)
                reason_parts.append(f"cluster#{cid} ({n_in_cluster} strats)")

                # Extra penalty if cluster is CRITICAL
                for c in clusters:
                    if c.cluster_id == cid and c.level == "CRITICAL":
                        cluster_penalty = 0.7
                        reason_parts.append("CRITICAL cluster")
                        break

            # Total budget for this strategy
            budget_pct = base * corr_adj * regime_mult * cluster_penalty
            budget_pct = max(self.min_risk, min(self.max_risk, budget_pct))

            if not reason_parts:
                reason_parts.append("uncorrelated")

            budgets.append(StrategyBudget(
                strategy=strat,
                risk_budget_pct=budget_pct,
                risk_budget_abs=budget_pct * capital,
                base_risk=base,
                correlation_adj=corr_adj,
                regime_adj=regime_mult,
                cluster_penalty=cluster_penalty,
                reason=", ".join(reason_parts),
            ))

        total_pct = sum(b.risk_budget_pct for b in budgets)
        total_abs = sum(b.risk_budget_abs for b in budgets)

        allocation = BudgetAllocation(
            total_risk_budget_pct=round(total_pct, 4),
            total_risk_budget_abs=round(total_abs, 2),
            capital=capital,
            regime=regime,
            n_strategies=len(active_strategies),
            n_clusters=len(clusters),
            budgets=budgets,
        )

        logger.info(
            f"Risk budget: {len(active_strategies)} strats, "
            f"regime={regime}, total_risk={total_pct:.1%}, "
            f"clusters={len(clusters)}"
        )

        return allocation
