"""
Portfolio Correlation & Allocation — diversification multi-stratégies.

Objectif :
  Maximiser le Sharpe du portefeuille en pondérant les stratégies par performance
  ET en pénalisant les corrélations élevées entre equity curves.

Formule d'allocation :
  weight_i = sharpe_i / sum_j(sharpe_j * correlation_penalty_ij)

  Où correlation_penalty_ij = 1 + max(0, corr_ij) pour les corrélations positives
  (les stratégies corrélées positivement reçoivent moins de capital)

Contraintes :
  - Poids maximum par stratégie : max_weight (défaut 40%)
  - Poids minimum : 0 (stratégies avec Sharpe < 0 → poids nul)
  - Somme des poids = 1.0

Usage :
  pc = PortfolioCorrelation()
  result = pc.allocate([result1, result2, result3])
  print(result.weights)
  print(result.expected_sharpe)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AllocationResult:
    """Résultat de l'allocation du portefeuille."""
    strategy_ids: list[str]
    weights: dict[str, float]               # Poids normalisé 0-1 par stratégie
    weights_pct: dict[str, float]           # Poids en pourcentage
    correlation_matrix: pd.DataFrame        # Matrice de corrélation des equity curves
    expected_sharpe: float                  # Sharpe estimé du portefeuille
    diversification_ratio: float            # Ratio de diversification (>1 = bénéfice)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  PORTFOLIO ALLOCATION ({len(self.strategy_ids)} strategies)",
            f"{'='*60}",
        ]
        for sid in self.strategy_ids:
            lines.append(f"  {sid:<40} {self.weights_pct[sid]:>6.1f}%")
        lines += [
            f"  {'-'*55}",
            f"  Sharpe portfolio estime : {self.expected_sharpe:.3f}",
            f"  Ratio diversification   : {self.diversification_ratio:.3f}",
        ]
        if self.warnings:
            lines.append(f"  WARNINGS : {'; '.join(self.warnings)}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


class PortfolioCorrelation:
    """
    Calcule la corrélation entre les equity curves de plusieurs backtests
    et alloue le capital de façon optimale.

    La diversification est réelle uniquement si les stratégies ne sont pas
    corrélées — ce module quantifie cela et pénalise les allocations naïves.
    """

    def __init__(self, max_weight: float = 0.40, min_sharpe: float = 0.0):
        """
        max_weight  : poids maximum par stratégie (défaut 40%)
        min_sharpe  : Sharpe minimum pour inclure la stratégie (défaut 0)
        """
        self.max_weight = max_weight
        self.min_sharpe = min_sharpe

    def allocate(self, backtest_results: list[dict]) -> AllocationResult:
        """
        backtest_results : liste de dicts issus de BacktestResult.to_dict()
                          Chaque dict DOIT contenir une clé "equity_curve" (pd.Series)
                          ou "wf_sharpes" pour l'estimation du Sharpe.

        Retourne une AllocationResult avec les poids optimaux.
        """
        if not backtest_results:
            return AllocationResult(
                strategy_ids=[],
                weights={},
                weights_pct={},
                correlation_matrix=pd.DataFrame(),
                expected_sharpe=0.0,
                diversification_ratio=1.0,
                warnings=["Aucun résultat fourni"],
            )

        ids = [r.get("strategy_id", f"strategy_{i}") for i, r in enumerate(backtest_results)]
        sharpes = [r.get("sharpe_ratio", 0.0) for r in backtest_results]
        warnings = []

        # Filtrer les stratégies sous le Sharpe minimum
        valid_mask = [s > self.min_sharpe for s in sharpes]
        if not any(valid_mask):
            warnings.append(f"Aucune stratégie avec Sharpe > {self.min_sharpe} — poids égaux")
            valid_mask = [True] * len(sharpes)

        valid_ids = [ids[i] for i in range(len(ids)) if valid_mask[i]]
        valid_sharpes = np.array([sharpes[i] for i in range(len(sharpes)) if valid_mask[i]])
        valid_results = [backtest_results[i] for i in range(len(backtest_results)) if valid_mask[i]]

        # Calcul de la matrice de corrélation sur les equity curves
        corr_matrix, has_equity = self._compute_correlation(valid_ids, valid_results)

        if not has_equity:
            warnings.append("Equity curves absentes — corrélation neutre (matrice identité)")

        # Allocation par performance ajustée de la corrélation
        raw_weights = self._compute_weights(valid_sharpes, corr_matrix)

        # Clamp au poids maximum
        raw_weights = np.clip(raw_weights, 0, self.max_weight)

        # Re-normaliser après clamp
        total = raw_weights.sum()
        if total > 0:
            weights_norm = raw_weights / total
        else:
            weights_norm = np.ones(len(valid_ids)) / len(valid_ids)
            warnings.append("Somme des poids nulle — poids égaux appliqués")

        # Sharpe estimé du portefeuille (approximation linéaire pondérée)
        expected_sharpe = float(np.dot(weights_norm, valid_sharpes))

        # Ratio de diversification : Sharpe portfolio / Sharpe moyen pondéré non-diversifié
        avg_sharpe = float(np.mean(valid_sharpes[valid_sharpes > 0])) if any(valid_sharpes > 0) else 1.0
        div_ratio = expected_sharpe / avg_sharpe if avg_sharpe > 0 else 1.0

        # Construction du dictionnaire de poids (toutes stratégies, y compris exclues)
        all_weights: dict[str, float] = {sid: 0.0 for sid in ids}
        for i, sid in enumerate(valid_ids):
            all_weights[sid] = round(float(weights_norm[i]), 4)

        weights_pct = {sid: round(w * 100, 1) for sid, w in all_weights.items()}

        logger.info(
            f"Portfolio allocation : {len(valid_ids)} stratégies, "
            f"Sharpe estimé={expected_sharpe:.3f}, div_ratio={div_ratio:.3f}"
        )

        return AllocationResult(
            strategy_ids=ids,
            weights=all_weights,
            weights_pct=weights_pct,
            correlation_matrix=corr_matrix,
            expected_sharpe=round(expected_sharpe, 3),
            diversification_ratio=round(div_ratio, 3),
            warnings=warnings,
        )

    def correlation_matrix(self, backtest_results: list[dict]) -> pd.DataFrame:
        """Retourne uniquement la matrice de corrélation (utile pour analyse standalone)."""
        ids = [r.get("strategy_id", f"s{i}") for i, r in enumerate(backtest_results)]
        corr, _ = self._compute_correlation(ids, backtest_results)
        return corr

    # ─── Internals ────────────────────────────────────────────────────────────

    def _compute_correlation(
        self, ids: list[str], results: list[dict]
    ) -> tuple[pd.DataFrame, bool]:
        """
        Calcule la matrice de corrélation de Pearson sur les equity curves.
        Si les equity curves ne sont pas disponibles, retourne la matrice identité.
        """
        curves = []
        for r in results:
            eq = r.get("equity_curve")
            if eq is not None and isinstance(eq, pd.Series) and len(eq) > 10:
                curves.append(eq.pct_change().dropna())
            else:
                curves.append(None)

        has_equity = all(c is not None for c in curves)

        if not has_equity or len(curves) < 2:
            # Matrice identité = pas de corrélation supposée
            n = len(ids)
            corr = pd.DataFrame(np.eye(n), index=ids, columns=ids)
            return corr, False

        # Aligner les séries sur un index commun (les périodes peuvent différer)
        aligned = pd.concat(
            {ids[i]: curves[i].rename(ids[i]) for i in range(len(ids))},
            axis=1,
        ).dropna()

        if len(aligned) < 10:
            n = len(ids)
            corr = pd.DataFrame(np.eye(n), index=ids, columns=ids)
            return corr, False

        corr = aligned.corr()
        return corr, True

    def _compute_weights(
        self, sharpes: np.ndarray, corr_matrix: pd.DataFrame
    ) -> np.ndarray:
        """
        Poids proportionnels au Sharpe divisé par la pénalité de corrélation.

        Pour chaque stratégie i :
          penalty_i = sum_j( max(0, corr_ij) * sharpe_j ) / sum_j(sharpe_j)
          weight_i  = max(0, sharpe_i) / (1 + penalty_i)

        Les stratégies corrélées à des stratégies performantes reçoivent moins.
        """
        n = len(sharpes)
        if n == 1:
            return np.array([1.0])

        corr = corr_matrix.values.copy()
        np.fill_diagonal(corr, 0)  # exclure auto-corrélation

        # Pénalité de corrélation pondérée par les Sharpe (positifs uniquement)
        positive_sharpes = np.maximum(sharpes, 0)
        total_sharpe = positive_sharpes.sum()

        if total_sharpe == 0:
            return np.ones(n) / n

        # penalty_i = somme des corrélations positives avec j, pondérée par sharpe_j
        penalties = np.zeros(n)
        for i in range(n):
            for j in range(n):
                if i != j and corr[i, j] > 0:
                    penalties[i] += corr[i, j] * positive_sharpes[j] / total_sharpe

        # Poids = sharpe / (1 + penalty)
        weights = np.maximum(sharpes, 0) / (1 + penalties)
        return weights
