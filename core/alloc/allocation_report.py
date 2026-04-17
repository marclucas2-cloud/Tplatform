"""Allocation Report — generates reports for dashboard and Telegram.

Provides cluster visualization data, Kelly mode status,
correlation heatmap, and formatted alerts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class AllocationReport:
    """Generates allocation reports from HRP and Kelly state."""

    def generate_cluster_report(self, hrp_allocator) -> dict:
        """Generate cluster info from HRP allocator.

        Returns:
            {clusters: [{id, strategies, total_weight, avg_correlation}], n_clusters}
        """
        if not hasattr(hrp_allocator, '_last_clusters') or hrp_allocator._last_clusters is None:
            return {"clusters": [], "n_clusters": 0}

        clusters_data = hrp_allocator._last_clusters
        weights = getattr(hrp_allocator, '_last_weights', {})
        corr = getattr(hrp_allocator, '_last_corr', None)

        result_clusters = []
        for cluster_id, strategies in clusters_data.items():
            total_w = sum(weights.get(s, 0) for s in strategies)
            avg_corr = 0.0
            if corr is not None and len(strategies) > 1:
                pairs_corr = []
                for i, s1 in enumerate(strategies):
                    for s2 in strategies[i + 1:]:
                        if s1 in corr.index and s2 in corr.index:
                            pairs_corr.append(abs(corr.loc[s1, s2]))
                if pairs_corr:
                    avg_corr = float(np.mean(pairs_corr))

            result_clusters.append({
                "id": cluster_id,
                "strategies": list(strategies),
                "total_weight": round(total_w, 4),
                "avg_correlation": round(avg_corr, 4),
            })

        return {"clusters": result_clusters, "n_clusters": len(result_clusters)}

    def generate_kelly_report(self, kelly_manager) -> dict:
        """Generate Kelly mode status report."""
        stats = kelly_manager.get_equity_stats()
        return {
            "mode": stats.get("mode", "UNKNOWN"),
            "fraction": stats.get("fraction", 0.125),
            "equity": stats.get("current", 0),
            "sma20": stats.get("sma20", 0),
            "std20": stats.get("std20", 0),
            "peak": stats.get("peak", 0),
            "drawdown_pct": stats.get("drawdown_pct", 0),
            "next_threshold": self._compute_next_threshold(stats),
        }

    def generate_correlation_heatmap_data(self, corr_matrix: pd.DataFrame) -> dict:
        """Format correlation matrix for frontend heatmap.

        Returns:
            {labels, values (2D list), warnings (high-corr pairs)}
        """
        labels = list(corr_matrix.columns)
        values = corr_matrix.values.tolist()

        warnings = []
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                c = abs(corr_matrix.iloc[i, j])
                if c > 0.6:
                    warnings.append(
                        f"{labels[i]} / {labels[j]}: corr={c:.2f} (DANGER)"
                    )

        return {
            "labels": labels,
            "values": [[round(v, 4) for v in row] for row in values],
            "warnings": warnings,
        }

    def format_telegram_alert(
        self, old_mode: str, new_mode: str, reason: str
    ) -> str:
        """Format Telegram message for allocation mode change."""
        mode_icons = {
            "AGGRESSIVE": "AGRESSIF",
            "NOMINAL": "NOMINAL",
            "DEFENSIVE": "DEFENSIF",
            "STOPPED": "ARRET TOTAL",
        }
        mode_sizes = {
            "AGGRESSIVE": "1/4 Kelly (taille max)",
            "NOMINAL": "1/8 Kelly (taille standard)",
            "DEFENSIVE": "1/32 Kelly (tailles /4)",
            "STOPPED": "0 Kelly (arret complet)",
        }
        old_label = mode_icons.get(old_mode, old_mode)
        new_label = mode_icons.get(new_mode, new_mode)
        size_info = mode_sizes.get(new_mode, "")

        return (
            f"ALLOCATION CHANGE: {old_label} -> {new_label}\n"
            f"Raison: {reason}\n"
            f"Sizing: {size_info}"
        )

    def generate_full_report(
        self, hrp, kelly, strategies: list
    ) -> dict:
        """Combined report from HRP + Kelly + strategy list."""
        cluster_report = self.generate_cluster_report(hrp)
        kelly_report = self.generate_kelly_report(kelly)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "n_strategies": len(strategies),
            "allocation": {
                "method": "HRP",
                "clusters": cluster_report,
            },
            "kelly": kelly_report,
            "strategies": strategies,
        }

    def _compute_next_threshold(self, stats: dict) -> dict:
        """Compute distance to next mode transition."""
        current = stats.get("current", 0)
        sma = stats.get("sma20", 0)
        std = stats.get("std20", 1)
        mode = stats.get("mode", "NOMINAL")

        if std == 0:
            return {"direction": "UNKNOWN", "distance": 0}

        upper = sma + 0.5 * std
        lower = sma - 0.5 * std

        if mode == "DEFENSIVE":
            return {
                "direction": "UP to NOMINAL",
                "distance": round(lower - current, 2),
                "threshold": round(lower, 2),
            }
        elif mode == "NOMINAL":
            dist_up = upper - current
            dist_down = current - lower
            if dist_up < dist_down:
                return {
                    "direction": "UP to AGGRESSIVE",
                    "distance": round(dist_up, 2),
                    "threshold": round(upper, 2),
                }
            return {
                "direction": "DOWN to DEFENSIVE",
                "distance": round(dist_down, 2),
                "threshold": round(lower, 2),
            }
        elif mode == "AGGRESSIVE":
            return {
                "direction": "DOWN to NOMINAL",
                "distance": round(current - upper, 2),
                "threshold": round(upper, 2),
            }
        return {"direction": "UNKNOWN", "distance": 0}
