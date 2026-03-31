"""
Hierarchical Risk Parity (HRP) Allocator.

Allocates capital inversely to variance within correlation clusters,
using hierarchical clustering (Ward linkage) and recursive bisection.

Key advantage over Markowitz: no covariance matrix inversion needed,
which makes the method numerically stable even with limited data
(20-60 days of strategy PnLs is sufficient).

Algorithm (Lopez de Prado, 2016):
  1. Build log-return matrix from strategy PnL series.
  2. Compute correlation and covariance matrices.
  3. Hierarchical clustering using Ward linkage on the distance matrix.
  4. Quasi-diagonalize the covariance matrix by reordering leaves.
  5. Recursive bisection: split clusters and allocate inversely to variance.
  6. Apply constraints (min/max weight) and renormalize.

Usage:
    from core.alloc.hrp_allocator import HRPAllocator
    hrp = HRPAllocator(min_weight=0.02, max_weight=0.25)
    weights = hrp.compute_weights(strategy_pnls)
    # weights = {"strat_a": 0.15, "strat_b": 0.10, ...}
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, leaves_list, linkage

logger = logging.getLogger(__name__)


class HRPAllocator:
    """
    HRP: allocate inversely to variance within correlation clusters.
    Advantage over Markowitz: no covariance matrix inversion needed (stable with limited data).
    """

    def __init__(
        self,
        min_weight: float = 0.02,
        max_weight: float = 0.25,
        rebalance_hours: int = 4,
    ):
        """
        Args:
            min_weight: Minimum weight per strategy (default 2%).
            max_weight: Maximum weight per strategy (default 25%).
            rebalance_hours: Minimum hours between rebalances.
        """
        if min_weight < 0 or min_weight >= max_weight:
            raise ValueError(
                f"Invalid weight bounds: min_weight={min_weight}, max_weight={max_weight}"
            )
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.rebalance_hours = rebalance_hours

        self._last_rebalance: Optional[datetime] = None
        self._last_weights: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Step 1: Build PnL / return matrix
    # ------------------------------------------------------------------

    def build_pnl_matrix(
        self,
        strategy_pnls: Dict[str, pd.Series],
        lookback_days: int = 20,
    ) -> pd.DataFrame:
        """Build log-return matrix from strategy PnL series.

        Each strategy PnL series is expected to have a DatetimeIndex and
        values representing cumulative or daily PnL. We compute daily
        log-returns (percentage change).

        Args:
            strategy_pnls: {strategy_name: pd.Series} with DatetimeIndex.
            lookback_days: Number of trailing calendar days to use.

        Returns:
            DataFrame with index=dates, columns=strategy_names, values=daily_returns.
            NaN rows (from alignment) are forward-filled then dropped.
        """
        if not strategy_pnls:
            return pd.DataFrame()

        # Align all series into a single DataFrame
        df = pd.DataFrame(strategy_pnls)

        # Trim to lookback window
        if lookback_days and len(df) > lookback_days:
            df = df.iloc[-lookback_days:]

        # Compute returns: use pct_change for cumulative PnL,
        # or diff for daily PnL. We assume daily PnL (diff).
        # If all values are positive and growing, use pct_change.
        # Safest: use simple difference (daily PnL already).
        returns = df.diff()

        # Drop first row (NaN from diff) and any fully-NaN columns
        returns = returns.iloc[1:]
        returns = returns.dropna(axis=1, how="all")

        # Fill remaining NaN with 0 (strategy not trading that day)
        returns = returns.fillna(0.0)

        logger.debug(
            "PnL matrix: %d days x %d strategies (lookback=%d)",
            len(returns), len(returns.columns), lookback_days,
        )
        return returns

    # ------------------------------------------------------------------
    # Step 2: Hierarchical clustering
    # ------------------------------------------------------------------

    def cluster_strategies(self, corr_matrix: pd.DataFrame) -> dict:
        """Hierarchical clustering using scipy Ward linkage.

        Converts the correlation matrix to a distance matrix:
            d(i, j) = sqrt(0.5 * (1 - corr(i, j)))
        Then applies Ward agglomerative clustering.

        Args:
            corr_matrix: Square correlation DataFrame (strategies x strategies).

        Returns:
            {
                "dendrogram": linkage_matrix (np.ndarray),
                "clusters": {cluster_id: [strategy_names]},
                "n_clusters": int,
            }
        """
        n = len(corr_matrix)
        if n < 2:
            names = list(corr_matrix.columns)
            return {
                "dendrogram": np.array([]),
                "clusters": {0: names} if names else {},
                "n_clusters": 1 if names else 0,
            }

        # Distance matrix from correlation: d = sqrt(0.5 * (1 - rho))
        dist_matrix = np.sqrt(0.5 * (1.0 - corr_matrix.values))
        np.fill_diagonal(dist_matrix, 0.0)

        # Convert to condensed form for scipy
        condensed = _to_condensed(dist_matrix)

        # Ward linkage
        link = linkage(condensed, method="ward")

        # Cut the dendrogram to get a reasonable number of clusters
        # Use max_d = 0.7 * max distance in linkage
        max_d = 0.7 * link[-1, 2] if len(link) > 0 else 1.0
        labels = fcluster(link, t=max_d, criterion="distance")

        # Build cluster map
        names = list(corr_matrix.columns)
        clusters: Dict[int, List[str]] = {}
        for name, label in zip(names, labels):
            clusters.setdefault(int(label), []).append(name)

        logger.debug(
            "Clustering: %d strategies -> %d clusters (max_d=%.3f)",
            n, len(clusters), max_d,
        )

        return {
            "dendrogram": link,
            "clusters": clusters,
            "n_clusters": len(clusters),
        }

    # ------------------------------------------------------------------
    # Step 3: Quasi-diagonalization
    # ------------------------------------------------------------------

    def quasi_diagonalize(
        self,
        corr_matrix: pd.DataFrame,
        link: np.ndarray,
    ) -> pd.DataFrame:
        """Reorder correlation matrix to isolate blocks of risk.

        Uses the leaf ordering from the dendrogram to rearrange rows/columns
        so that correlated strategies are adjacent.

        Args:
            corr_matrix: Square correlation DataFrame.
            link: Linkage matrix from scipy.

        Returns:
            Reordered correlation DataFrame.
        """
        if link.size == 0:
            return corr_matrix

        sorted_idx = list(leaves_list(link).astype(int))
        names = [corr_matrix.columns[i] for i in sorted_idx]

        return corr_matrix.loc[names, names]

    # ------------------------------------------------------------------
    # Step 4: Recursive bisection
    # ------------------------------------------------------------------

    def recursive_bisection(
        self,
        cov_matrix: pd.DataFrame,
        sorted_indices: list,
    ) -> pd.Series:
        """Allocate weights inversely to cluster variance.

        Recursively splits the sorted list of strategies in half,
        computing the variance of each half, and allocating more weight
        to the less volatile half.

        Args:
            cov_matrix: Covariance DataFrame (strategies x strategies).
            sorted_indices: List of strategy names in quasi-diagonal order.

        Returns:
            Series with strategy names as index, weights as values (sum ~= 1.0).
        """
        weights = pd.Series(1.0, index=sorted_indices)

        cluster_items = [sorted_indices]

        while cluster_items:
            # Split each cluster in half
            next_items = []
            for items in cluster_items:
                if len(items) <= 1:
                    continue

                mid = len(items) // 2
                left = items[:mid]
                right = items[mid:]

                # Cluster variance = w' * Cov * w (equal-weight within cluster)
                var_left = _cluster_variance(cov_matrix, left)
                var_right = _cluster_variance(cov_matrix, right)

                # Allocate inversely to variance
                total_var = var_left + var_right
                if total_var <= 0:
                    alpha = 0.5
                else:
                    alpha = 1.0 - var_left / total_var

                # Scale weights
                weights[left] *= alpha
                weights[right] *= (1.0 - alpha)

                # Continue splitting if clusters are large enough
                if len(left) > 1:
                    next_items.append(left)
                if len(right) > 1:
                    next_items.append(right)

            cluster_items = next_items

        # Normalize to sum = 1
        total = weights.sum()
        if total > 0:
            weights = weights / total

        return weights

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def compute_weights(
        self,
        strategy_pnls: Dict[str, pd.Series],
        lookback_days: int = 20,
    ) -> Dict[str, float]:
        """Full HRP pipeline: pnl_matrix -> correlation -> cluster -> quasi_diag -> bisect.

        Args:
            strategy_pnls: {strategy_name: pd.Series} of daily PnL with DatetimeIndex.
            lookback_days: Number of trailing days to use.

        Returns:
            {strategy_name: weight} where weights sum to 1.0 (before constraints).
        """
        # Build return matrix
        returns = self.build_pnl_matrix(strategy_pnls, lookback_days)

        if returns.empty or len(returns.columns) < 2:
            # Fallback: equal weight
            names = list(strategy_pnls.keys())
            if not names:
                return {}
            w = 1.0 / len(names)
            logger.warning(
                "HRP: insufficient data (%d cols), falling back to equal weight",
                len(returns.columns) if not returns.empty else 0,
            )
            return {name: w for name in names}

        # Correlation and covariance
        corr_matrix = returns.corr()
        cov_matrix = returns.cov()

        # Handle any NaN in correlation (fill with 0 = no correlation)
        corr_matrix = corr_matrix.fillna(0.0)
        cov_matrix = cov_matrix.fillna(0.0)

        # Clustering
        cluster_info = self.cluster_strategies(corr_matrix)
        link = cluster_info["dendrogram"]

        # Quasi-diagonalize
        if link.size > 0:
            sorted_corr = self.quasi_diagonalize(corr_matrix, link)
            sorted_names = list(sorted_corr.columns)
        else:
            sorted_names = list(corr_matrix.columns)

        # Recursive bisection
        raw_weights = self.recursive_bisection(cov_matrix, sorted_names)

        # Apply constraints
        weights_dict = {name: float(raw_weights[name]) for name in sorted_names}
        constrained = self.apply_constraints(weights_dict)

        logger.info(
            "HRP weights computed: %d strategies, lookback=%d days, "
            "clusters=%d, max_weight=%.3f, min_weight=%.3f",
            len(constrained), lookback_days,
            cluster_info["n_clusters"],
            max(constrained.values()) if constrained else 0,
            min(constrained.values()) if constrained else 0,
        )

        return constrained

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    def apply_constraints(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Clamp weights to [min_weight, max_weight], then renormalize to sum=1.0.

        Iterative: clamp, renormalize, repeat until stable (max 20 iterations).

        Args:
            weights: {strategy_name: weight}

        Returns:
            {strategy_name: constrained_weight} summing to 1.0.
        """
        if not weights:
            return {}

        result = dict(weights)

        for _ in range(20):
            # Clamp
            clamped = False
            for name in result:
                if result[name] < self.min_weight:
                    result[name] = self.min_weight
                    clamped = True
                elif result[name] > self.max_weight:
                    result[name] = self.max_weight
                    clamped = True

            # Renormalize
            total = sum(result.values())
            if total <= 0:
                n = len(result)
                return {name: 1.0 / n for name in result}

            result = {name: w / total for name, w in result.items()}

            if not clamped:
                break

        return result

    # ------------------------------------------------------------------
    # Rebalancing logic
    # ------------------------------------------------------------------

    def should_rebalance(
        self,
        current_weights: Dict[str, float],
        new_weights: Dict[str, float],
        threshold: float = 0.05,
    ) -> bool:
        """Only rebalance if max weight change > threshold (avoid churning).

        Args:
            current_weights: Current portfolio weights.
            new_weights: Proposed new weights.
            threshold: Maximum allowed weight delta before triggering rebalance.

        Returns:
            True if rebalance is warranted.
        """
        if not current_weights:
            return True

        all_keys = set(current_weights.keys()) | set(new_weights.keys())
        max_delta = 0.0

        for key in all_keys:
            old_w = current_weights.get(key, 0.0)
            new_w = new_weights.get(key, 0.0)
            max_delta = max(max_delta, abs(new_w - old_w))

        should = max_delta > threshold

        if should:
            logger.info(
                "Rebalance triggered: max_delta=%.4f > threshold=%.4f",
                max_delta, threshold,
            )
        else:
            logger.debug(
                "No rebalance needed: max_delta=%.4f <= threshold=%.4f",
                max_delta, threshold,
            )

        return should

    def get_turnover_cost(
        self,
        old_weights: Dict[str, float],
        new_weights: Dict[str, float],
        total_capital: float,
        cost_bps: float = 5.0,
    ) -> float:
        """Estimate rebalancing cost in dollars.

        Turnover = sum of |new_weight - old_weight| / 2 (one-way).
        Cost = turnover * capital * cost_bps / 10000.

        Args:
            old_weights: Current weights.
            new_weights: Target weights.
            total_capital: Portfolio value in dollars.
            cost_bps: Cost in basis points (default 5 bps = 0.05%).

        Returns:
            Estimated cost in dollars.
        """
        all_keys = set(old_weights.keys()) | set(new_weights.keys())

        turnover = 0.0
        for key in all_keys:
            old_w = old_weights.get(key, 0.0)
            new_w = new_weights.get(key, 0.0)
            turnover += abs(new_w - old_w)

        # One-way turnover (buy side only)
        one_way_turnover = turnover / 2.0

        cost = one_way_turnover * total_capital * cost_bps / 10_000.0

        logger.debug(
            "Turnover cost: turnover=%.4f, capital=$%.0f, "
            "cost_bps=%.1f -> cost=$%.2f",
            one_way_turnover, total_capital, cost_bps, cost,
        )

        return round(cost, 2)


# ======================================================================
# Private helper functions
# ======================================================================

def _to_condensed(dist_matrix: np.ndarray) -> np.ndarray:
    """Convert a square distance matrix to scipy condensed form.

    Extracts upper-triangle elements in row-major order.
    """
    n = dist_matrix.shape[0]
    condensed = []
    for i in range(n):
        for j in range(i + 1, n):
            condensed.append(dist_matrix[i, j])
    return np.array(condensed, dtype=float)


def _cluster_variance(cov_matrix: pd.DataFrame, items: list) -> float:
    """Compute the variance of an equal-weight portfolio over the given items.

    var = w' * Cov * w, where w = [1/n, 1/n, ...].
    """
    if not items:
        return 0.0

    sub_cov = cov_matrix.loc[items, items].values
    n = len(items)
    w = np.ones(n) / n

    var = float(w @ sub_cov @ w)
    return max(var, 0.0)
