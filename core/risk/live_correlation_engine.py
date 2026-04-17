"""Live Correlation Engine — real-time PnL correlation between strategies.

Measures ACTUAL (not backtest) correlation from live trade PnL.
Detects correlated clusters and triggers warnings/criticals.

Usage:
    engine = LiveCorrelationEngine()
    engine.record_pnl("fx_eurusd_trend", 45.0, datetime.now())
    engine.record_pnl("fx_gbpusd_trend", 38.0, datetime.now())
    matrix = engine.get_correlation_matrix()
    clusters = engine.detect_clusters()
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Alert thresholds
CORR_WARNING = 0.70
CORR_CRITICAL = 0.85


@dataclass
class CorrelationAlert:
    level: str  # WARNING | CRITICAL
    strategies: Tuple[str, str]
    correlation: float
    timestamp: datetime
    cluster_id: int | None = None


@dataclass
class ClusterInfo:
    cluster_id: int
    strategies: List[str]
    avg_correlation: float
    max_correlation: float
    level: str  # OK | WARNING | CRITICAL


@dataclass
class CorrelationSnapshot:
    timestamp: datetime
    n_strategies: int
    global_score: float  # 0 (uncorrelated) → 1 (fully correlated)
    n_clusters: int
    alerts: List[CorrelationAlert]
    clusters: List[ClusterInfo]


class LiveCorrelationEngine:
    """Rolling PnL correlation between live strategies."""

    def __init__(
        self,
        window_short: int = 20,
        window_long: int = 50,
        data_dir: str = "data",
        warning_threshold: float = CORR_WARNING,
        critical_threshold: float = CORR_CRITICAL,
    ):
        self.window_short = window_short
        self.window_long = window_long
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Strategy PnL history: {strategy_name: [(timestamp, pnl), ...]}
        self._pnl_history: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)

        # Cache for correlation matrix
        self._cache_matrix: np.ndarray | None = None
        self._cache_strategies: List[str] | None = None
        self._cache_time: float = 0
        self._cache_ttl: float = 60.0  # Recompute max every 60s

        self._load_state()

    # ─── Public API ──────────────────────────────────────────────────────

    def record_pnl(
        self, strategy: str, pnl: float, timestamp: datetime | None = None
    ) -> None:
        """Record a trade PnL for a strategy."""
        ts = timestamp or datetime.now(timezone.utc)
        self._pnl_history[strategy].append((ts, pnl))

        # Keep only last window_long * 3 entries (buffer)
        max_keep = self.window_long * 3
        if len(self._pnl_history[strategy]) > max_keep:
            self._pnl_history[strategy] = self._pnl_history[strategy][-max_keep:]

        self._invalidate_cache()
        self._save_state()

    def get_strategies(self) -> List[str]:
        """Return list of strategies with enough PnL data."""
        min_trades = max(5, self.window_short // 2)
        return sorted(
            s for s, h in self._pnl_history.items() if len(h) >= min_trades
        )

    def get_correlation_matrix(
        self, window: int | None = None
    ) -> Dict[str, Any]:
        """Compute correlation matrix from recent PnL.

        Returns:
            {
                "strategies": [...],
                "matrix": [[...], ...],
                "window": int,
                "n_trades_per_strategy": {str: int}
            }
        """
        w = window or self.window_short
        strategies = self.get_strategies()

        if len(strategies) < 2:
            return {
                "strategies": strategies,
                "matrix": [[1.0]] if strategies else [],
                "window": w,
                "n_trades_per_strategy": {
                    s: len(self._pnl_history.get(s, [])) for s in strategies
                },
            }

        # Check cache
        now = time.time()
        if (
            self._cache_matrix is not None
            and self._cache_strategies == strategies
            and (now - self._cache_time) < self._cache_ttl
        ):
            return {
                "strategies": strategies,
                "matrix": self._cache_matrix.tolist(),
                "window": w,
                "n_trades_per_strategy": {
                    s: len(self._pnl_history[s]) for s in strategies
                },
            }

        matrix = self._compute_matrix(strategies, w)
        self._cache_matrix = matrix
        self._cache_strategies = strategies
        self._cache_time = now

        return {
            "strategies": strategies,
            "matrix": matrix.tolist(),
            "window": w,
            "n_trades_per_strategy": {
                s: len(self._pnl_history[s]) for s in strategies
            },
        }

    def detect_clusters(
        self, threshold: float | None = None
    ) -> List[ClusterInfo]:
        """Detect clusters of correlated strategies using simple threshold.

        Strategies with pairwise correlation > threshold are grouped.
        """
        thresh = threshold or self.warning_threshold
        strategies = self.get_strategies()

        if len(strategies) < 2:
            return []

        matrix = self._compute_matrix(strategies, self.window_short)

        # Union-find clustering
        parent = list(range(len(strategies)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(strategies)):
            for j in range(i + 1, len(strategies)):
                if matrix[i, j] >= thresh:
                    union(i, j)

        # Group by cluster root
        clusters_map: Dict[int, List[int]] = defaultdict(list)
        for i in range(len(strategies)):
            clusters_map[find(i)].append(i)

        # Only report clusters with 2+ members
        clusters = []
        for cid, (root, members) in enumerate(clusters_map.items()):
            if len(members) < 2:
                continue

            strat_names = [strategies[m] for m in members]
            corrs = []
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    corrs.append(matrix[members[i], members[j]])

            avg_corr = float(np.mean(corrs)) if corrs else 0.0
            max_corr = float(np.max(corrs)) if corrs else 0.0

            level = "OK"
            if max_corr >= self.critical_threshold:
                level = "CRITICAL"
            elif max_corr >= self.warning_threshold:
                level = "WARNING"

            clusters.append(ClusterInfo(
                cluster_id=cid,
                strategies=strat_names,
                avg_correlation=round(avg_corr, 3),
                max_correlation=round(max_corr, 3),
                level=level,
            ))

        return clusters

    def get_global_score(self) -> float:
        """Compute global correlation score (0 → uncorrelated, 1 → fully).

        Average of upper-triangle absolute correlations.
        """
        strategies = self.get_strategies()
        if len(strategies) < 2:
            return 0.0

        matrix = self._compute_matrix(strategies, self.window_short)
        n = len(strategies)

        upper_tri = []
        for i in range(n):
            for j in range(i + 1, n):
                upper_tri.append(abs(matrix[i, j]))

        return round(float(np.mean(upper_tri)), 3) if upper_tri else 0.0

    def check_alerts(self) -> List[CorrelationAlert]:
        """Check all pairwise correlations and return alerts."""
        strategies = self.get_strategies()
        if len(strategies) < 2:
            return []

        matrix = self._compute_matrix(strategies, self.window_short)
        alerts = []
        now = datetime.now(timezone.utc)

        for i in range(len(strategies)):
            for j in range(i + 1, len(strategies)):
                corr = matrix[i, j]
                if corr >= self.critical_threshold:
                    alerts.append(CorrelationAlert(
                        level="CRITICAL",
                        strategies=(strategies[i], strategies[j]),
                        correlation=round(corr, 3),
                        timestamp=now,
                    ))
                elif corr >= self.warning_threshold:
                    alerts.append(CorrelationAlert(
                        level="WARNING",
                        strategies=(strategies[i], strategies[j]),
                        correlation=round(corr, 3),
                        timestamp=now,
                    ))

        return alerts

    def get_snapshot(self) -> CorrelationSnapshot:
        """Full correlation state snapshot."""
        strategies = self.get_strategies()
        alerts = self.check_alerts()
        clusters = self.detect_clusters()

        return CorrelationSnapshot(
            timestamp=datetime.now(timezone.utc),
            n_strategies=len(strategies),
            global_score=self.get_global_score(),
            n_clusters=len(clusters),
            alerts=alerts,
            clusters=clusters,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize snapshot to dict for API/logging."""
        snap = self.get_snapshot()
        return {
            "timestamp": snap.timestamp.isoformat(),
            "n_strategies": snap.n_strategies,
            "global_score": snap.global_score,
            "n_clusters": snap.n_clusters,
            "alerts": [
                {
                    "level": a.level,
                    "strategies": list(a.strategies),
                    "correlation": a.correlation,
                }
                for a in snap.alerts
            ],
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "strategies": c.strategies,
                    "avg_correlation": c.avg_correlation,
                    "max_correlation": c.max_correlation,
                    "level": c.level,
                }
                for c in snap.clusters
            ],
        }

    # ─── Internal ────────────────────────────────────────────────────────

    def _compute_matrix(self, strategies: List[str], window: int) -> np.ndarray:
        """Pearson correlation matrix from last `window` PnL values per strategy."""
        n = len(strategies)
        matrix = np.eye(n)

        # Build PnL vectors (last `window` trades)
        vectors = {}
        for s in strategies:
            pnls = [p for _, p in self._pnl_history[s][-window:]]
            vectors[s] = np.array(pnls, dtype=float)

        for i in range(n):
            for j in range(i + 1, n):
                vi = vectors[strategies[i]]
                vj = vectors[strategies[j]]

                # Align to same length (min of both)
                min_len = min(len(vi), len(vj))
                if min_len < 5:
                    matrix[i, j] = matrix[j, i] = 0.0
                    continue

                vi_aligned = vi[-min_len:]
                vj_aligned = vj[-min_len:]

                # Pearson correlation
                std_i = np.std(vi_aligned)
                std_j = np.std(vj_aligned)
                if std_i < 1e-10 or std_j < 1e-10:
                    corr = 0.0
                else:
                    corr = float(np.corrcoef(vi_aligned, vj_aligned)[0, 1])
                    if np.isnan(corr):
                        corr = 0.0

                matrix[i, j] = matrix[j, i] = corr

        return matrix

    def _invalidate_cache(self) -> None:
        self._cache_matrix = None

    def _save_state(self) -> None:
        """Persist PnL history to JSON."""
        path = self._data_dir / "live_correlation_state.json"
        data = {}
        for strat, entries in self._pnl_history.items():
            data[strat] = [
                {"ts": ts.isoformat(), "pnl": pnl} for ts, pnl in entries
            ]
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save correlation state: {e}")

    def _load_state(self) -> None:
        """Load PnL history from JSON."""
        path = self._data_dir / "live_correlation_state.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for strat, entries in data.items():
                for e in entries:
                    ts = datetime.fromisoformat(e["ts"])
                    self._pnl_history[strat].append((ts, e["pnl"]))
        except Exception as e:
            logger.warning(f"Failed to load correlation state: {e}")
