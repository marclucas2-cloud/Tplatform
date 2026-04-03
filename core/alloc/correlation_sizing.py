"""P4-03: Correlation-Aware Sizing — Marginal Diversification Contribution.

Concept: MDC_i = (Sharpe_portfolio_with_i - Sharpe_portfolio_without_i) / weight_i

Strategies with high MDC (bring diversification) get more capital.
Strategies with low/negative MDC (redundant) get less.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MDCResult:
    """Marginal Diversification Contribution for a strategy."""
    strategy: str
    mdc: float                     # MDC score
    sharpe_with: float             # Portfolio Sharpe including this strat
    sharpe_without: float          # Portfolio Sharpe excluding this strat
    hrp_weight: float              # Original HRP weight
    adjusted_weight: float         # MDC-adjusted weight
    max_corr_with: str = ""        # Most correlated strategy
    max_corr_value: float = 0.0    # Correlation with most correlated

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "mdc": round(self.mdc, 4),
            "sharpe_with": round(self.sharpe_with, 3),
            "sharpe_without": round(self.sharpe_without, 3),
            "hrp_weight": round(self.hrp_weight, 4),
            "adjusted_weight": round(self.adjusted_weight, 4),
            "weight_change_pct": round(
                (self.adjusted_weight - self.hrp_weight) / self.hrp_weight * 100
                if self.hrp_weight > 0 else 0, 1
            ),
            "max_corr_with": self.max_corr_with,
            "max_corr_value": round(self.max_corr_value, 3),
        }


@dataclass
class CorrelationSizingResult:
    """Full correlation-aware sizing result."""
    strategies: dict[str, MDCResult] = field(default_factory=dict)
    portfolio_sharpe_hrp: float = 0.0
    portfolio_sharpe_mdc: float = 0.0
    improvement: float = 0.0

    def to_dict(self) -> dict:
        return {
            "portfolio_sharpe_hrp": round(self.portfolio_sharpe_hrp, 3),
            "portfolio_sharpe_mdc": round(self.portfolio_sharpe_mdc, 3),
            "improvement": round(self.improvement, 3),
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
        }


class CorrelationAwareSizer:
    """Adjusts HRP weights by Marginal Diversification Contribution.

    Usage:
        sizer = CorrelationAwareSizer()
        result = sizer.compute(pnl_matrix, hrp_weights)
        # result.strategies["fx_carry_vs"].adjusted_weight
    """

    def __init__(
        self,
        min_weight: float = 0.02,    # 2% minimum
        max_weight: float = 0.25,    # 25% maximum
        negative_mdc_weight: float = 0.02,  # Weight for MDC < 0
    ):
        self._min_weight = min_weight
        self._max_weight = max_weight
        self._negative_mdc_weight = negative_mdc_weight

    def compute(
        self,
        pnl_matrix: pd.DataFrame,
        hrp_weights: dict[str, float],
        annualization: float = 252.0,
    ) -> CorrelationSizingResult:
        """Compute MDC-adjusted weights.

        Args:
            pnl_matrix: DataFrame with columns=strategy names, rows=daily returns
            hrp_weights: {strategy: weight} from HRP allocator
            annualization: Annualization factor (252 for daily)
        """
        result = CorrelationSizingResult()

        if pnl_matrix.empty or len(pnl_matrix.columns) < 2:
            # Not enough strategies for MDC
            for strat, w in hrp_weights.items():
                result.strategies[strat] = MDCResult(
                    strategy=strat, mdc=0, sharpe_with=0, sharpe_without=0,
                    hrp_weight=w, adjusted_weight=w,
                )
            return result

        strategies = [s for s in pnl_matrix.columns if s in hrp_weights]
        if len(strategies) < 2:
            for strat, w in hrp_weights.items():
                result.strategies[strat] = MDCResult(
                    strategy=strat, mdc=0, sharpe_with=0, sharpe_without=0,
                    hrp_weight=w, adjusted_weight=w,
                )
            return result

        # Correlation matrix
        corr = pnl_matrix[strategies].corr()

        # Full portfolio Sharpe (with current HRP weights)
        full_sharpe = self._portfolio_sharpe(pnl_matrix, hrp_weights, strategies, annualization)
        result.portfolio_sharpe_hrp = full_sharpe

        # Compute MDC for each strategy
        mdcs = {}
        for strat in strategies:
            weight = hrp_weights.get(strat, 0)
            if weight <= 0:
                continue

            # Sharpe without this strategy
            without = {s: w for s, w in hrp_weights.items() if s != strat and s in strategies}
            if not without:
                mdcs[strat] = 0.0
                continue

            # Renormalize without weights
            total_w = sum(without.values())
            if total_w > 0:
                without = {s: w / total_w for s, w in without.items()}

            sharpe_without = self._portfolio_sharpe(
                pnl_matrix, without, [s for s in strategies if s != strat], annualization
            )

            mdc = (full_sharpe - sharpe_without) / weight if weight > 0 else 0
            mdcs[strat] = mdc

            # Find most correlated strategy
            corrs = corr[strat].drop(strat)
            max_corr_strat = corrs.abs().idxmax() if not corrs.empty else ""
            max_corr_val = corrs.abs().max() if not corrs.empty else 0

            result.strategies[strat] = MDCResult(
                strategy=strat,
                mdc=mdc,
                sharpe_with=full_sharpe,
                sharpe_without=sharpe_without,
                hrp_weight=hrp_weights.get(strat, 0),
                adjusted_weight=0,  # Computed below
                max_corr_with=max_corr_strat,
                max_corr_value=float(max_corr_val),
            )

        # Adjust weights by MDC
        adjusted = self._adjust_weights(hrp_weights, mdcs, strategies)

        # Update results
        for strat, adj_w in adjusted.items():
            if strat in result.strategies:
                result.strategies[strat].adjusted_weight = adj_w

        # New portfolio Sharpe
        result.portfolio_sharpe_mdc = self._portfolio_sharpe(
            pnl_matrix, adjusted, strategies, annualization
        )
        result.improvement = result.portfolio_sharpe_mdc - result.portfolio_sharpe_hrp

        return result

    def _portfolio_sharpe(
        self,
        pnl_matrix: pd.DataFrame,
        weights: dict[str, float],
        strategies: list[str],
        annualization: float,
    ) -> float:
        """Compute portfolio Sharpe from weighted strategy returns."""
        available = [s for s in strategies if s in pnl_matrix.columns and s in weights]
        if not available:
            return 0.0

        w = np.array([weights.get(s, 0) for s in available])
        total_w = w.sum()
        if total_w <= 0:
            return 0.0
        w = w / total_w

        returns = pnl_matrix[available].values
        port_returns = returns @ w

        mean = np.mean(port_returns)
        std = np.std(port_returns, ddof=1)

        if std == 0:
            return 0.0

        return float(mean / std * np.sqrt(annualization))

    def _adjust_weights(
        self,
        hrp_weights: dict[str, float],
        mdcs: dict[str, float],
        strategies: list[str],
    ) -> dict[str, float]:
        """Adjust HRP weights by MDC scores."""
        adjusted = {}

        for strat in strategies:
            hrp_w = hrp_weights.get(strat, 0)
            mdc = mdcs.get(strat, 0)

            if mdc < 0:
                # Negative MDC: strategy degrades portfolio -> minimum weight
                adj_w = self._negative_mdc_weight
            elif mdc == 0:
                adj_w = hrp_w
            else:
                # Scale by relative MDC
                adj_w = hrp_w  # Will be rescaled below

            adjusted[strat] = adj_w

        # Normalize positive MDC strategies
        positive_strats = [s for s in strategies if mdcs.get(s, 0) > 0]
        if positive_strats:
            mdc_values = {s: mdcs[s] for s in positive_strats}
            mdc_sum = sum(mdc_values.values())
            if mdc_sum > 0:
                # Remaining weight after negative MDC strategies
                neg_weight = sum(
                    adjusted[s] for s in strategies
                    if s not in positive_strats
                )
                remaining = 1.0 - neg_weight

                for s in positive_strats:
                    adjusted[s] = remaining * mdc_values[s] / mdc_sum

        # Apply min/max bounds
        for strat in adjusted:
            adjusted[strat] = max(self._min_weight, min(self._max_weight, adjusted[strat]))

        # Renormalize to sum to 1.0
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {s: w / total for s, w in adjusted.items()}

        return adjusted
