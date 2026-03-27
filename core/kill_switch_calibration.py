"""
RISK-002 : Calibration du kill switch par Monte Carlo.

Le kill switch actuel (-2% sur 5j rolling) est un seuil arbitraire.
Ce module le calibre par simulation Monte Carlo pour chaque strategie :
  - Faux positifs < 5% en conditions normales
  - Detection > 95% quand la strategie a reellement devie (Sharpe OOS < 0)

Methode :
  1. Resampler 10,000 trajectoires du P&L (bootstrap avec remplacement)
  2. Pour chaque trajectoire, calculer le min 5j rolling P&L
  3. Trouver le seuil optimal qui equilibre faux positifs / detection

Usage :
    calibrator = KillSwitchCalibrator()
    result = calibrator.calibrate(strategy_name, daily_returns, sharpe_oos)
"""

import json
import logging
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class KillSwitchCalibrator:
    """Calibre le seuil du kill switch par Monte Carlo.

    Pour chaque strategie, simule des milliers de trajectoires P&L
    en resampleant les trades historiques, puis determine le seuil
    optimal de drawdown sur 5j rolling.
    """

    def __init__(
        self,
        n_simulations: int = 10_000,
        lookback_days: int = 5,
        max_false_positive_rate: float = 0.05,
        min_detection_rate: float = 0.95,
        seed: Optional[int] = None,
    ):
        """
        Args:
            n_simulations: nombre de trajectoires Monte Carlo (default 10,000)
            lookback_days: fenetre rolling pour le min P&L (default 5j)
            max_false_positive_rate: taux max de faux positifs accepte (default 5%)
            min_detection_rate: taux min de detection quand Sharpe < 0 (default 95%)
            seed: seed pour reproductibilite (None = aleatoire)
        """
        self.n_simulations = n_simulations
        self.lookback_days = lookback_days
        self.max_false_positive_rate = max_false_positive_rate
        self.min_detection_rate = min_detection_rate
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        else:
            self.rng = np.random.default_rng()

    def _rolling_min_pnl(self, returns: np.ndarray, window: int) -> float:
        """Calcule le min du P&L cumule sur une fenetre rolling.

        Args:
            returns: array de rendements quotidiens
            window: taille de la fenetre rolling

        Returns:
            Minimum du P&L cumule sur la fenetre (valeur negative = pire drawdown)
        """
        if len(returns) < window:
            return float(returns.sum())
        # P&L cumule sur chaque fenetre rolling
        cumsum = np.cumsum(returns)
        # Somme rolling = cumsum[i] - cumsum[i-window] (avec convention cumsum[-1] = 0)
        padded = np.concatenate(([0.0], cumsum))
        rolling_sums = padded[window:] - padded[:-window]
        return float(rolling_sums.min()) if len(rolling_sums) > 0 else float(returns.sum())

    def _simulate_normal_trajectories(self, returns: np.ndarray) -> np.ndarray:
        """Simule n_simulations trajectoires en conditions normales.

        Resample les rendements historiques avec remplacement (bootstrap).

        Returns:
            Array de shape (n_simulations,) contenant le min 5j rolling de chaque trajectoire
        """
        n = len(returns)
        min_pnls = np.empty(self.n_simulations)
        for i in range(self.n_simulations):
            # Bootstrap : tirer n rendements avec remplacement
            sample = self.rng.choice(returns, size=n, replace=True)
            min_pnls[i] = self._rolling_min_pnl(sample, self.lookback_days)
        return min_pnls

    def _simulate_degraded_trajectories(self, returns: np.ndarray) -> np.ndarray:
        """Simule des trajectoires ou la strategie a devie (Sharpe < 0).

        Methode : inverser le signe du rendement moyen (garder la vol)
        pour simuler un regime ou la strategie perd son edge.

        Returns:
            Array de shape (n_simulations,) contenant le min 5j rolling
        """
        # Decentrer les rendements : retirer le mean et ajouter -|mean|
        mean = returns.mean()
        vol = returns.std()
        # Creer des rendements avec mean negatif (= strategie cassee)
        # On prend le pire : mean = -abs(mean) - 0.5*vol pour simuler une vraie degradation
        degraded_mean = -abs(mean) - 0.3 * vol
        degraded_returns = returns - mean + degraded_mean

        n = len(degraded_returns)
        min_pnls = np.empty(self.n_simulations)
        for i in range(self.n_simulations):
            sample = self.rng.choice(degraded_returns, size=n, replace=True)
            min_pnls[i] = self._rolling_min_pnl(sample, self.lookback_days)
        return min_pnls

    def _find_optimal_threshold(
        self,
        normal_min_pnls: np.ndarray,
        degraded_min_pnls: np.ndarray,
    ) -> float:
        """Trouve le seuil optimal qui equilibre faux positifs et detection.

        Le seuil optimal est le plus grand (= le moins agressif) qui :
        - Se declenche < max_false_positive_rate en conditions normales
        - Se declenche > min_detection_rate quand la strategie a devie

        Si aucun seuil ne satisfait les deux, on prend le percentile
        max_false_positive_rate des trajectoires normales.

        Args:
            normal_min_pnls: min 5j rolling en conditions normales
            degraded_min_pnls: min 5j rolling en conditions degradees

        Returns:
            Seuil optimal (valeur negative)
        """
        # Tester des candidats du percentile 1% au percentile 10%
        candidates = np.percentile(normal_min_pnls, np.arange(1, 11))

        best_threshold = None
        for threshold in sorted(candidates, reverse=True):  # du moins agressif au plus
            # Faux positif = % de trajectoires normales qui declenchent
            fp_rate = (normal_min_pnls <= threshold).mean()
            # Detection = % de trajectoires degradees qui declenchent
            detect_rate = (degraded_min_pnls <= threshold).mean()

            if fp_rate <= self.max_false_positive_rate and detect_rate >= self.min_detection_rate:
                best_threshold = threshold
                break

        # Fallback : prendre le percentile qui donne exactement max_false_positive_rate
        if best_threshold is None:
            best_threshold = float(
                np.percentile(normal_min_pnls, self.max_false_positive_rate * 100)
            )

        return float(best_threshold)

    def calibrate(
        self,
        strategy_name: str,
        daily_returns: List[float],
        current_threshold: float = -0.02,
        sharpe_oos: Optional[float] = None,
    ) -> dict:
        """Calibre le seuil du kill switch pour une strategie.

        Args:
            strategy_name: nom de la strategie
            daily_returns: rendements quotidiens historiques
            current_threshold: seuil actuel (default -2%)
            sharpe_oos: Sharpe ratio out-of-sample (informatif)

        Returns:
            {
                strategy: str,
                current_threshold: float,
                optimal_threshold: float,
                false_positive_rate_current: float,
                false_positive_rate_optimal: float,
                detection_rate_optimal: float,
                percentile_5: float,
                percentile_1: float,
                n_observations: int,
                recommendation: str,
            }
        """
        arr = np.array(daily_returns, dtype=float)
        if len(arr) < self.lookback_days + 1:
            return {
                "strategy": strategy_name,
                "current_threshold": current_threshold,
                "optimal_threshold": current_threshold,
                "false_positive_rate_current": 0.0,
                "false_positive_rate_optimal": 0.0,
                "detection_rate_optimal": 0.0,
                "percentile_5": 0.0,
                "percentile_1": 0.0,
                "n_observations": len(arr),
                "recommendation": "Pas assez de donnees pour calibrer",
            }

        # Simuler les trajectoires
        normal_min_pnls = self._simulate_normal_trajectories(arr)
        degraded_min_pnls = self._simulate_degraded_trajectories(arr)

        # Trouver le seuil optimal
        optimal = self._find_optimal_threshold(normal_min_pnls, degraded_min_pnls)

        # Statistiques pour le seuil actuel
        fp_current = float((normal_min_pnls <= current_threshold).mean())
        fp_optimal = float((normal_min_pnls <= optimal).mean())
        detect_optimal = float((degraded_min_pnls <= optimal).mean())

        # Percentiles de reference
        p5 = float(np.percentile(normal_min_pnls, 5))
        p1 = float(np.percentile(normal_min_pnls, 1))

        # Recommendation
        if abs(optimal - current_threshold) < 0.002:
            recommendation = "Seuil actuel adequat — pas de changement necessaire"
        elif optimal > current_threshold:
            recommendation = (
                f"RESSERRER le seuil de {current_threshold:.3%} a {optimal:.3%} "
                f"— le seuil actuel est trop laxiste (FP={fp_current:.1%})"
            )
        else:
            recommendation = (
                f"RELACHER le seuil de {current_threshold:.3%} a {optimal:.3%} "
                f"— le seuil actuel declenche trop souvent (FP={fp_current:.1%})"
            )

        return {
            "strategy": strategy_name,
            "current_threshold": round(current_threshold, 6),
            "optimal_threshold": round(optimal, 6),
            "false_positive_rate_current": round(fp_current, 4),
            "false_positive_rate_optimal": round(fp_optimal, 4),
            "detection_rate_optimal": round(detect_optimal, 4),
            "percentile_5": round(p5, 6),
            "percentile_1": round(p1, 6),
            "n_observations": len(arr),
            "recommendation": recommendation,
        }

    def calibrate_all(
        self,
        strategy_returns: Dict[str, List[float]],
        current_threshold: float = -0.02,
        output_path: Optional[str] = None,
    ) -> Dict[str, dict]:
        """Calibre le kill switch pour toutes les strategies.

        Args:
            strategy_returns: {strategy_name: [daily_returns]}
            current_threshold: seuil actuel commun (default -2%)
            output_path: chemin pour sauvegarder le JSON (optionnel)

        Returns:
            {strategy_name: calibration_result}
        """
        results = {}
        for name, returns in strategy_returns.items():
            logger.info(f"Calibration kill switch : {name} ({len(returns)} obs)")
            results[name] = self.calibrate(name, returns, current_threshold)

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            logger.info(f"Calibration sauvegardee dans {path}")

        return results
