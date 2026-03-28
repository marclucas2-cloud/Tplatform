"""
ROC-006 — Real-Time Correlation Monitor.

Calcule la correlation inter-positions en temps reel et ajuste le sizing
si la concentration dans un cluster correle depasse le seuil.

Toutes les heures :
  1. Matrice de correlation sur les 7 derniers jours (rolling)
  2. Identification des clusters (groupes avec rho > threshold)
  3. Si un cluster concentre > max_cluster_pct du capital → alerte + reduction

Ne modifie aucun fichier existant. Tout est en memoire.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class RealTimeCorrelationMonitor:
    """Moniteur de correlation en temps reel pour les positions actives.

    Detecte les clusters de positions correlees et propose des overrides
    de sizing pour limiter la concentration du risque.
    """

    def __init__(
        self,
        cluster_threshold: float = 0.6,
        max_cluster_pct: float = 40.0,
        reduction_factor: float = 0.7,
    ):
        """
        Args:
            cluster_threshold: seuil de correlation pour former un cluster
            max_cluster_pct: % max du capital dans un seul cluster
            reduction_factor: multiplicateur de sizing si cluster trop concentre
        """
        if not 0 < cluster_threshold <= 1.0:
            raise ValueError("cluster_threshold doit etre entre 0 et 1")
        if not 0 < max_cluster_pct <= 100:
            raise ValueError("max_cluster_pct doit etre entre 0 et 100")
        if not 0 < reduction_factor <= 1.0:
            raise ValueError("reduction_factor doit etre entre 0 et 1")

        self.cluster_threshold = cluster_threshold
        self.max_cluster_pct = max_cluster_pct
        self.reduction_factor = reduction_factor

        # Etat interne
        self._corr_matrix: Optional[np.ndarray] = None
        self._symbols: List[str] = []
        self._clusters: List[List[str]] = []
        self._sizing_overrides: Dict[str, float] = {}
        self._alerts: List[dict] = []
        self._last_update: Optional[datetime] = None

        logger.info(
            f"RealTimeCorrelationMonitor initialise — "
            f"threshold={cluster_threshold}, max_cluster={max_cluster_pct}%, "
            f"reduction={reduction_factor}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, positions: List[dict], returns_data: Dict[str, list]) -> dict:
        """Recalcule les correlations et detecte les clusters.

        Args:
            positions: liste de dicts {symbol, notional, ...}
            returns_data: dict {symbol: [float, ...]} rendements journaliers

        Returns:
            {matrix, clusters, alerts, sizing_overrides}
        """
        self._alerts = []
        self._sizing_overrides = {}

        # Pas assez de positions pour une correlation
        if len(positions) < 2:
            self._corr_matrix = None
            self._symbols = [p["symbol"] for p in positions] if positions else []
            self._clusters = []
            self._last_update = datetime.now(timezone.utc)
            return {
                "matrix": None,
                "clusters": [],
                "alerts": [],
                "sizing_overrides": {},
            }

        # Extraire les symboles des positions
        symbols = [p["symbol"] for p in positions]
        self._symbols = symbols

        # Construire la matrice de rendements
        corr_matrix = self._compute_correlation_matrix(symbols, returns_data)
        self._corr_matrix = corr_matrix

        # Identifier les clusters
        clusters = self._find_clusters(symbols, corr_matrix)
        self._clusters = clusters

        # Verifier la concentration de chaque cluster
        total_notional = sum(abs(p.get("notional", 0)) for p in positions)
        if total_notional > 0:
            for cluster in clusters:
                cluster_notional = sum(
                    abs(p.get("notional", 0))
                    for p in positions
                    if p["symbol"] in cluster
                )
                cluster_pct = (cluster_notional / total_notional) * 100

                if cluster_pct > self.max_cluster_pct:
                    alert = {
                        "type": "CLUSTER_CONCENTRATION",
                        "cluster": cluster,
                        "pct": round(cluster_pct, 1),
                        "threshold": self.max_cluster_pct,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self._alerts.append(alert)
                    logger.warning(
                        f"ALERTE CORRELATION: cluster {cluster} = "
                        f"{cluster_pct:.1f}% du capital (seuil {self.max_cluster_pct}%)"
                    )

                    # Appliquer la reduction de sizing
                    for sym in cluster:
                        self._sizing_overrides[sym] = self.reduction_factor

        self._last_update = datetime.now(timezone.utc)

        return {
            "matrix": corr_matrix,
            "clusters": clusters,
            "alerts": self._alerts,
            "sizing_overrides": dict(self._sizing_overrides),
        }

    def get_effective_positions(
        self, positions: List[dict], corr_matrix: Optional[np.ndarray] = None
    ) -> float:
        """Calcule le nombre effectif de positions (ajuste pour la correlation).

        Formule : N_eff = N / (1 + (N-1) * avg_rho)

        Args:
            positions: liste de positions
            corr_matrix: matrice de correlation (utilise la derniere si None)

        Returns:
            nombre effectif de positions (float)
        """
        n = len(positions)
        if n <= 1:
            return float(n)

        matrix = corr_matrix if corr_matrix is not None else self._corr_matrix
        if matrix is None:
            return float(n)

        # Moyenne des correlations hors diagonale (en valeur absolue)
        mask = ~np.eye(matrix.shape[0], dtype=bool)
        avg_rho = np.abs(matrix[mask]).mean()

        denominator = 1 + (n - 1) * avg_rho
        if denominator <= 0:
            return float(n)

        n_eff = n / denominator
        return round(n_eff, 2)

    def get_sizing_override(self, symbol: str) -> float:
        """Retourne le multiplicateur de sizing pour un symbole.

        Args:
            symbol: symbole de l'instrument

        Returns:
            1.0 si pas d'override, < 1.0 si dans un cluster concentre
        """
        return self._sizing_overrides.get(symbol, 1.0)

    def get_cluster_report(self) -> dict:
        """Rapport complet sur les clusters actuels.

        Returns:
            {
                clusters: [[sym, ...], ...],
                n_clusters: int,
                symbols: [str],
                alerts: [dict],
                sizing_overrides: {sym: float},
                last_update: str or None,
            }
        """
        return {
            "clusters": self._clusters,
            "n_clusters": len(self._clusters),
            "symbols": list(self._symbols),
            "alerts": list(self._alerts),
            "sizing_overrides": dict(self._sizing_overrides),
            "last_update": (
                self._last_update.isoformat() if self._last_update else None
            ),
        }

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _compute_correlation_matrix(
        self, symbols: List[str], returns_data: Dict[str, list]
    ) -> np.ndarray:
        """Construit la matrice de correlation a partir des rendements.

        Si un symbole n'a pas de donnees, on met 0.0 de correlation avec les
        autres (independance par defaut).
        """
        n = len(symbols)
        matrix = np.eye(n)

        # Construire la matrice de rendements alignes
        max_len = 0
        for sym in symbols:
            if sym in returns_data:
                max_len = max(max_len, len(returns_data[sym]))

        if max_len < 2:
            return matrix

        # Pad les series courtes avec NaN
        returns_array = np.full((max_len, n), np.nan)
        for i, sym in enumerate(symbols):
            if sym in returns_data and len(returns_data[sym]) > 0:
                data = returns_data[sym]
                returns_array[-len(data):, i] = data

        # Calculer les correlations pairwise (ignorer NaN)
        for i in range(n):
            for j in range(i + 1, n):
                col_i = returns_array[:, i]
                col_j = returns_array[:, j]

                # Garder seulement les lignes ou les deux valeurs sont presentes
                valid = ~np.isnan(col_i) & ~np.isnan(col_j)
                if valid.sum() < 3:
                    # Pas assez de donnees — independance par defaut
                    continue

                r_i = col_i[valid]
                r_j = col_j[valid]

                # Eviter division par zero si variance nulle
                std_i = np.std(r_i)
                std_j = np.std(r_j)
                if std_i < 1e-12 or std_j < 1e-12:
                    continue

                corr = np.corrcoef(r_i, r_j)[0, 1]
                if np.isnan(corr):
                    corr = 0.0

                matrix[i, j] = corr
                matrix[j, i] = corr

        return matrix

    def _find_clusters(
        self, symbols: List[str], corr_matrix: np.ndarray
    ) -> List[List[str]]:
        """Identifie les clusters de symboles correles (union-find simplifie).

        Deux symboles sont dans le meme cluster si |corr| > threshold.
        """
        n = len(symbols)
        if n < 2:
            return []

        # Union-Find
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path compression
                x = parent[x]
            return x

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Regrouper les paires au dessus du seuil
        for i in range(n):
            for j in range(i + 1, n):
                if abs(corr_matrix[i, j]) > self.cluster_threshold:
                    union(i, j)

        # Construire les clusters (seulement ceux avec > 1 membre)
        cluster_map: Dict[int, List[str]] = {}
        for i in range(n):
            root = find(i)
            if root not in cluster_map:
                cluster_map[root] = []
            cluster_map[root].append(symbols[i])

        # Filtrer les singletons
        clusters = [members for members in cluster_map.values() if len(members) > 1]

        if clusters:
            logger.info(
                f"Clusters correles detectes: {len(clusters)} cluster(s), "
                f"symboles: {clusters}"
            )

        return clusters
