"""Monte Carlo Engine for BacktesterV2.

Permutation-based Monte Carlo simulation to assess strategy robustness.
Shuffles trade order to build distributions of Sharpe, max drawdown,
and ruin probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np


@dataclass
class MCResult:
    """Monte Carlo simulation result."""

    median_sharpe: float
    p5_sharpe: float
    p95_sharpe: float
    median_max_dd: float
    p95_max_dd: float
    prob_profitable: float
    prob_ruin: float
    n_simulations: int
    distributions: Dict[str, List[float]] = field(default_factory=dict)


class MonteCarloEngine:
    """Permutation-based Monte Carlo simulation engine.

    Shuffles the order of historical trades to build distributions of
    key metrics, testing whether results are path-dependent.
    """

    def run(
        self,
        trade_log: List[Dict],
        n_simulations: int = 10_000,
        initial_capital: float = 10_000.0,
        seed: int = 42,
    ) -> MCResult:
        """Run Monte Carlo simulation by permuting trade order.

        Args:
            trade_log: List of trade dicts, each must have a 'pnl' key.
            n_simulations: Number of permutation simulations.
            initial_capital: Starting capital for equity curve.
            seed: Random seed for reproducibility.

        Returns:
            MCResult with distributions and percentiles.
        """
        if not trade_log:
            return MCResult(
                median_sharpe=0.0,
                p5_sharpe=0.0,
                p95_sharpe=0.0,
                median_max_dd=0.0,
                p95_max_dd=0.0,
                prob_profitable=0.0,
                prob_ruin=0.0,
                n_simulations=n_simulations,
                distributions={
                    "sharpes": [],
                    "max_dds": [],
                    "final_equities": [],
                },
            )

        pnls = np.array([t.get("pnl", 0.0) for t in trade_log], dtype=np.float64)
        rng = np.random.default_rng(seed)

        sharpes: List[float] = []
        max_dds: List[float] = []
        final_equities: List[float] = []

        for _ in range(n_simulations):
            # Permute trade order
            permuted = rng.permutation(pnls)

            # Build equity curve
            equity_curve = np.empty(len(permuted) + 1, dtype=np.float64)
            equity_curve[0] = initial_capital
            np.cumsum(permuted, out=equity_curve[1:])
            equity_curve[1:] += initial_capital

            final_equity = equity_curve[-1]
            final_equities.append(float(final_equity))

            # Sharpe from trade PnLs (annualized, ~252 trades/year assumption)
            if len(permuted) >= 2 and np.std(permuted) > 0:
                sharpe = float(
                    np.mean(permuted) / np.std(permuted) * math.sqrt(252)
                )
            else:
                sharpe = 0.0
            sharpes.append(sharpe)

            # Max drawdown
            peak = np.maximum.accumulate(equity_curve)
            drawdown = (equity_curve - peak) / np.where(peak > 0, peak, 1.0)
            max_dd = float(abs(drawdown.min()))
            max_dds.append(max_dd)

        sharpes_arr = np.array(sharpes)
        max_dds_arr = np.array(max_dds)
        finals_arr = np.array(final_equities)

        prob_profitable = float(np.mean(finals_arr > initial_capital))
        prob_ruin = float(np.mean(finals_arr < initial_capital * 0.5))

        return MCResult(
            median_sharpe=float(np.median(sharpes_arr)),
            p5_sharpe=float(np.percentile(sharpes_arr, 5)),
            p95_sharpe=float(np.percentile(sharpes_arr, 95)),
            median_max_dd=float(np.median(max_dds_arr)),
            p95_max_dd=float(np.percentile(max_dds_arr, 95)),
            prob_profitable=prob_profitable,
            prob_ruin=prob_ruin,
            n_simulations=n_simulations,
            distributions={
                "sharpes": sharpes,
                "max_dds": max_dds,
                "final_equities": final_equities,
            },
        )
