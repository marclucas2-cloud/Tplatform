"""
Correlation-Aware Position Sizer — ajuste le sizing en fonction des correlations.

Regle principale :
  Si 3+ positions correlees > 0.7 sont ouvertes, reduire le sizing de 30%.

Ce module s'integre apres l'allocator (qui donne le poids cible par strategie)
et avant l'envoi des ordres (qui convertit le poids en notional/qty).

Architecture :
  1. Construire la matrice de correlation a partir des returns historiques
  2. Pour chaque nouveau signal, verifier les correlations avec les positions ouvertes
  3. Appliquer le facteur de reduction si le cluster correle est trop grand
  4. Respecter les caps de l'allocator (pas d'augmentation, seulement reduction)

Integration dans le pipeline :
  allocator -> position_sizer -> risk_manager -> order_execution
"""

import logging
from typing import Dict, List

import numpy as np

logger = logging.getLogger(__name__)


class CorrelationAwareSizer:
    """Si 3 positions correlees > 0.7 sont ouvertes, reduire le sizing de 30%.

    Usage :
        sizer = CorrelationAwareSizer()
        correlation_matrix = sizer.build_correlation_matrix(returns_dict)
        size = sizer.calculate_size(
            strategy="gap_continuation",
            signal={"direction": "LONG", "base_size": 0.10},
            open_positions=["orb_5min", "triple_ema"],
            correlation_matrix=correlation_matrix,
        )
    """

    # --- Defaults ---
    CORRELATION_THRESHOLD = 0.7   # Seuil de correlation elevee
    MIN_CLUSTER_SIZE = 3          # Nombre min de positions correlees pour declencher
    REDUCTION_FACTOR = 0.30       # Reduction de 30%
    MAX_CLUSTER_REDUCTION = 0.50  # Reduction max (meme si cluster enorme)

    def __init__(
        self,
        correlation_threshold: float = None,
        min_cluster_size: int = None,
        reduction_factor: float = None,
        max_reduction: float = None,
    ):
        self.corr_threshold = correlation_threshold or self.CORRELATION_THRESHOLD
        self.min_cluster = min_cluster_size or self.MIN_CLUSTER_SIZE
        self.reduction = reduction_factor or self.REDUCTION_FACTOR
        self.max_reduction = max_reduction or self.MAX_CLUSTER_REDUCTION

    def calculate_size(
        self,
        strategy: str,
        signal: dict,
        open_positions: List[str],
        correlation_matrix: Dict[str, Dict[str, float]],
    ) -> float:
        """Retourne le sizing ajuste en fonction des correlations.

        Args:
            strategy: nom de la strategie du signal entrant.
            signal: {
                direction: 'LONG' ou 'SHORT',
                base_size: float (allocation cible, ex: 0.10 = 10%),
            }
            open_positions: liste des noms de strategies ayant des positions ouvertes.
            correlation_matrix: {strategy_a: {strategy_b: correlation_value}}

        Returns:
            float: sizing ajuste (toujours <= base_size).
        """
        base_size = signal.get("base_size", 0.10)

        if not open_positions or not correlation_matrix:
            return base_size

        # Trouver les positions ouvertes correlees avec la nouvelle strategie
        correlated = self._find_correlated_positions(
            strategy, open_positions, correlation_matrix,
        )

        # Calculer la reduction
        cluster_size = len(correlated) + 1  # +1 pour la strategie entrante
        reduction = self._calculate_reduction(cluster_size)

        adjusted_size = base_size * (1.0 - reduction)

        if reduction > 0:
            logger.info(
                "CorrelationAwareSizer: %s correlees (> %.2f) avec %s -> "
                "reduction %.0f%% (base=%.3f, ajuste=%.3f)",
                correlated, self.corr_threshold, strategy,
                reduction * 100, base_size, adjusted_size,
            )

        return round(adjusted_size, 6)

    def calculate_sizes_batch(
        self,
        signals: Dict[str, dict],
        open_positions: List[str],
        correlation_matrix: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """Calcule les sizes pour un batch de signaux simultanes.

        Args:
            signals: {strategy_name: {direction, base_size}}
            open_positions: positions deja ouvertes
            correlation_matrix: matrice de correlation

        Returns:
            {strategy_name: adjusted_size}
        """
        results = {}
        # Traiter en ajoutant les strategies deja validees aux positions ouvertes
        current_positions = list(open_positions)

        for strategy, signal in signals.items():
            size = self.calculate_size(
                strategy, signal, current_positions, correlation_matrix,
            )
            results[strategy] = size
            # Ajouter cette strategie aux positions ouvertes pour le calcul suivant
            current_positions.append(strategy)

        return results

    def build_correlation_matrix(
        self,
        returns_dict: Dict[str, List[float]],
        min_overlap: int = 20,
    ) -> Dict[str, Dict[str, float]]:
        """Construit la matrice de correlation a partir des returns historiques.

        Args:
            returns_dict: {strategy_name: [daily_returns]}
            min_overlap: nombre minimum de jours en commun pour calculer la correlation.

        Returns:
            {strategy_a: {strategy_b: correlation_value}}
        """
        strategies = sorted(returns_dict.keys())
        matrix = {}

        for i, strat_a in enumerate(strategies):
            matrix[strat_a] = {}
            returns_a = np.array(returns_dict[strat_a], dtype=float)

            for j, strat_b in enumerate(strategies):
                if i == j:
                    matrix[strat_a][strat_b] = 1.0
                    continue

                returns_b = np.array(returns_dict[strat_b], dtype=float)

                # Aligner les longueurs
                overlap = min(len(returns_a), len(returns_b))
                if overlap < min_overlap:
                    matrix[strat_a][strat_b] = 0.0
                    continue

                a = returns_a[-overlap:]
                b = returns_b[-overlap:]

                # Correlation de Pearson
                if np.std(a) == 0 or np.std(b) == 0:
                    matrix[strat_a][strat_b] = 0.0
                else:
                    corr = float(np.corrcoef(a, b)[0, 1])
                    matrix[strat_a][strat_b] = round(corr, 4)

        return matrix

    def find_correlation_clusters(
        self,
        correlation_matrix: Dict[str, Dict[str, float]],
        threshold: float = None,
    ) -> List[List[str]]:
        """Identifie les clusters de strategies correlees.

        Algorithme : union-find simplifie. Deux strategies sont dans le meme
        cluster si leur correlation depasse le seuil.

        Args:
            correlation_matrix: matrice de correlation
            threshold: seuil (defaut: self.corr_threshold)

        Returns:
            Liste de clusters (liste de noms de strategies).
        """
        if threshold is None:
            threshold = self.corr_threshold

        strategies = sorted(correlation_matrix.keys())
        parent = {s: s for s in strategies}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Fusionner les strategies correlees
        for i, s_a in enumerate(strategies):
            for j, s_b in enumerate(strategies):
                if i >= j:
                    continue
                corr = correlation_matrix.get(s_a, {}).get(s_b, 0.0)
                if abs(corr) >= threshold:
                    union(s_a, s_b)

        # Regrouper par root
        clusters_map = {}
        for s in strategies:
            root = find(s)
            clusters_map.setdefault(root, []).append(s)

        # Filtrer : seulement les clusters de taille >= 2
        return [c for c in clusters_map.values() if len(c) >= 2]

    def get_exposure_report(
        self,
        open_positions: Dict[str, dict],
        correlation_matrix: Dict[str, Dict[str, float]],
    ) -> dict:
        """Rapport d'exposition par cluster correle.

        Args:
            open_positions: {strategy: {notional, direction}}
            correlation_matrix: matrice de correlation

        Returns:
            {
                clusters: [{strategies, avg_correlation, total_notional, direction_bias}],
                max_cluster_size: int,
                concentration_risk: str ('low', 'medium', 'high'),
            }
        """
        clusters = self.find_correlation_clusters(correlation_matrix)

        cluster_reports = []
        for cluster in clusters:
            # Positions dans ce cluster
            active_in_cluster = [s for s in cluster if s in open_positions]
            if not active_in_cluster:
                continue

            # Correlation moyenne intra-cluster
            corrs = []
            for i, s_a in enumerate(cluster):
                for j, s_b in enumerate(cluster):
                    if i < j:
                        c = correlation_matrix.get(s_a, {}).get(s_b, 0.0)
                        corrs.append(abs(c))
            avg_corr = float(np.mean(corrs)) if corrs else 0.0

            # Notional total et direction bias
            total_notional = sum(
                abs(float(open_positions[s].get("notional", 0)))
                for s in active_in_cluster
            )
            long_count = sum(
                1 for s in active_in_cluster
                if open_positions[s].get("direction", "").upper() == "LONG"
            )
            short_count = len(active_in_cluster) - long_count
            direction_bias = "LONG" if long_count > short_count else "SHORT" if short_count > long_count else "MIXED"

            cluster_reports.append({
                "strategies": active_in_cluster,
                "avg_correlation": round(avg_corr, 3),
                "total_notional": round(total_notional, 2),
                "direction_bias": direction_bias,
            })

        max_size = max((len(c["strategies"]) for c in cluster_reports), default=0)
        if max_size >= 4:
            risk = "high"
        elif max_size >= 3:
            risk = "medium"
        else:
            risk = "low"

        return {
            "clusters": cluster_reports,
            "max_cluster_size": max_size,
            "concentration_risk": risk,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_correlated_positions(
        self,
        strategy: str,
        open_positions: List[str],
        correlation_matrix: Dict[str, Dict[str, float]],
    ) -> List[str]:
        """Trouve les positions ouvertes correlees avec la strategie donnee."""
        correlated = []
        strat_corrs = correlation_matrix.get(strategy, {})

        for pos in open_positions:
            corr = strat_corrs.get(pos, 0.0)
            # Aussi chercher dans l'autre sens
            if corr == 0.0:
                corr = correlation_matrix.get(pos, {}).get(strategy, 0.0)
            if abs(corr) >= self.corr_threshold:
                correlated.append(pos)

        return correlated

    def _calculate_reduction(self, cluster_size: int) -> float:
        """Calcule le facteur de reduction selon la taille du cluster.

        cluster_size < min_cluster : pas de reduction
        cluster_size == min_cluster : reduction standard (30%)
        cluster_size > min_cluster : reduction augmentee, cappee a max_reduction
        """
        if cluster_size < self.min_cluster:
            return 0.0

        # Reduction lineaire : 30% pour cluster=3, +10% par position supplementaire
        extra = (cluster_size - self.min_cluster) * 0.10
        reduction = self.reduction + extra

        return min(reduction, self.max_reduction)
