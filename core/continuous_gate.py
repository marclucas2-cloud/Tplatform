"""
ContinuousGateEvaluator — Evaluation continue des conditions Gate M1.

ROC-003 : Evalue les conditions Gate M1 en continu (chaque heure)
au lieu d'attendre une date fixe.

Changement : min_calendar_days reduit de 21 a 14.
Le min_trades (15) est le critere le plus important.

Quand toutes les conditions sont remplies -> auto-upgrade vers Phase 1.
Quand les conditions sont PROCHES (5/7 primary + 2/5 secondary) -> notification.
"""

import logging
from typing import Optional, Callable, Dict, Any, List

logger = logging.getLogger(__name__)

# Criteres primaires (TOUS requis pour PASS)
PRIMARY_CRITERIA = {
    "min_calendar_days": 14,       # Reduit de 21 a 14 (ROC-003)
    "min_trades": 15,
    "min_strategies_active": 3,
    "max_drawdown_pct": 5.0,
    "max_single_loss_pct": 2.0,
    "bugs_critiques": 0,
    "reconciliation_errors": 0,
}

# Criteres secondaires (3/5 requis pour PASS)
SECONDARY_CRITERIA = {
    "sharpe_period": 0.3,
    "win_rate": 0.42,
    "profit_factor": 1.1,
    "slippage_ratio": 3.0,
    "execution_quality": 0.85,
}

# Seuils pour le statut NEAR
NEAR_PRIMARY_THRESHOLD = 5    # 5/7 primaires
NEAR_SECONDARY_THRESHOLD = 2  # 2/5 secondaires

# Secondaires minimum pour PASS
SECONDARY_MIN_PASS = 3


class ContinuousGateEvaluator:
    """Evaluate Gate M1 conditions CONTINUOUSLY (every hour) instead of
    waiting for a fixed date.

    Change: min_calendar_days reduced from 21 to 14.
    The min_trades (15) is the more important criterion.

    When all conditions are met -> auto-upgrade to Phase 1.
    When conditions are NEAR (5/7 primary + 2/5 secondary) -> notify.
    """

    def __init__(
        self,
        leverage_manager=None,
        alerter: Optional[Callable] = None,
    ):
        """Initialise l'evaluateur.

        Args:
            leverage_manager: instance de LeverageManager pour auto-upgrade
            alerter: callback d'alerte (ex: Telegram send_alert)
        """
        self._leverage_manager = leverage_manager
        self._alerter = alerter
        self._last_result: Optional[str] = None
        logger.info(
            "ContinuousGateEvaluator initialized — primary=%d criteria, secondary=%d criteria",
            len(PRIMARY_CRITERIA),
            len(SECONDARY_CRITERIA),
        )

    # ------------------------------------------------------------------
    # Evaluation principale
    # ------------------------------------------------------------------

    def evaluate(self, stats: dict) -> dict:
        """Evaluate Gate M1 conditions against current stats.

        Args:
            stats: dict avec les KPI actuels. Cles attendues :
                - calendar_days, trades, strategies_active
                - drawdown_pct, single_loss_pct
                - bugs_critiques, reconciliation_errors
                - sharpe_period, win_rate, profit_factor
                - slippage_ratio, execution_quality

        Returns:
            dict avec :
                - result: "PASS" | "NEAR" | "PENDING"
                - primaries_pass: int (nombre de primaires satisfaits)
                - secondaries_pass: int (nombre de secondaires satisfaits)
                - details: dict avec le detail de chaque critere
        """
        # Evaluer les criteres primaires
        primary_results = self._evaluate_primaries(stats)
        primaries_pass = sum(1 for v in primary_results.values() if v["passed"])
        primaries_total = len(PRIMARY_CRITERIA)
        all_primaries = primaries_pass == primaries_total

        # Evaluer les criteres secondaires
        secondary_results = self._evaluate_secondaries(stats)
        secondaries_pass = sum(1 for v in secondary_results.values() if v["passed"])
        enough_secondaries = secondaries_pass >= SECONDARY_MIN_PASS

        # Determiner le resultat
        if all_primaries and enough_secondaries:
            result = "PASS"
        elif (primaries_pass >= NEAR_PRIMARY_THRESHOLD and
              secondaries_pass >= NEAR_SECONDARY_THRESHOLD):
            result = "NEAR"
        else:
            result = "PENDING"

        evaluation = {
            "result": result,
            "primaries_pass": primaries_pass,
            "primaries_total": primaries_total,
            "secondaries_pass": secondaries_pass,
            "secondaries_total": len(SECONDARY_CRITERIA),
            "secondaries_required": SECONDARY_MIN_PASS,
            "details": {
                "primary": primary_results,
                "secondary": secondary_results,
            },
        }

        # Actions automatiques selon le resultat
        if result == "PASS":
            logger.info(
                "Gate M1 PASS — %d/%d primary, %d/%d secondary",
                primaries_pass, primaries_total,
                secondaries_pass, len(SECONDARY_CRITERIA),
            )
            self.on_gate_pass()
        elif result == "NEAR" and self._last_result != "NEAR":
            # Notification NEAR seulement si changement d'etat
            msg = (
                f"Gate M1 NEAR — {primaries_pass}/{primaries_total} primary, "
                f"{secondaries_pass}/{len(SECONDARY_CRITERIA)} secondary\n"
                f"Conditions proches du PASS."
            )
            logger.info(msg)
            if self._alerter:
                self._alerter(msg, level="info")

        self._last_result = result
        return evaluation

    def on_gate_pass(self):
        """Actions lors du passage de la gate : upgrade leverage + alerte.

        Appele automatiquement quand evaluate() retourne PASS.
        """
        # Upgrade le leverage manager vers PHASE_1
        if self._leverage_manager is not None:
            try:
                current = self._leverage_manager.current_phase
                if current == "SOFT_LAUNCH":
                    new_phase = self._leverage_manager.advance_phase()
                    logger.info(
                        "Gate M1 PASS — LeverageManager upgraded: %s -> %s",
                        current, new_phase,
                    )
                else:
                    logger.info(
                        "Gate M1 PASS — LeverageManager already at %s, no upgrade needed",
                        current,
                    )
            except ValueError as e:
                logger.error("Gate M1 PASS — Failed to advance phase: %s", e)

        # Envoyer l'alerte
        msg = (
            "GATE M1 PASS — Conditions remplies!\n"
            "Auto-upgrade vers PHASE_1.\n"
            "Prochaine etape : scaling capital."
        )
        logger.info(msg)
        if self._alerter:
            self._alerter(msg, level="info")

    # ------------------------------------------------------------------
    # Progression lisible
    # ------------------------------------------------------------------

    def get_progress(self, stats: dict) -> dict:
        """Human-readable progress report.

        Args:
            stats: dict avec les KPI actuels.

        Returns:
            dict avec resume lisible de la progression.
        """
        primary_results = self._evaluate_primaries(stats)
        secondary_results = self._evaluate_secondaries(stats)

        primaries_pass = sum(1 for v in primary_results.values() if v["passed"])
        secondaries_pass = sum(1 for v in secondary_results.values() if v["passed"])

        # Construire les listes lisibles
        primary_details = []
        for name, info in primary_results.items():
            status = "PASS" if info["passed"] else "FAIL"
            primary_details.append(
                f"  [{status}] {name}: {info['actual']} (seuil: {info['threshold']})"
            )

        secondary_details = []
        for name, info in secondary_results.items():
            status = "PASS" if info["passed"] else "FAIL"
            secondary_details.append(
                f"  [{status}] {name}: {info['actual']} (seuil: {info['threshold']})"
            )

        return {
            "primary_summary": f"{primaries_pass}/{len(PRIMARY_CRITERIA)} criteres primaires",
            "secondary_summary": f"{secondaries_pass}/{len(SECONDARY_CRITERIA)} criteres secondaires (min {SECONDARY_MIN_PASS})",
            "primary_details": primary_details,
            "secondary_details": secondary_details,
            "overall": (
                "PASS" if primaries_pass == len(PRIMARY_CRITERIA) and secondaries_pass >= SECONDARY_MIN_PASS
                else "NEAR" if (primaries_pass >= NEAR_PRIMARY_THRESHOLD and
                                secondaries_pass >= NEAR_SECONDARY_THRESHOLD)
                else "PENDING"
            ),
        }

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _evaluate_primaries(self, stats: dict) -> Dict[str, Dict[str, Any]]:
        """Evalue chaque critere primaire.

        Returns:
            dict {nom_critere: {passed, actual, threshold, direction}}
        """
        results = {}
        for name, threshold in PRIMARY_CRITERIA.items():
            actual = self._get_stat(name, stats)
            passed = self._check_criterion(name, actual, threshold)
            direction = self._get_direction(name)
            results[name] = {
                "passed": passed,
                "actual": actual,
                "threshold": threshold,
                "direction": direction,
            }
        return results

    def _evaluate_secondaries(self, stats: dict) -> Dict[str, Dict[str, Any]]:
        """Evalue chaque critere secondaire.

        Returns:
            dict {nom_critere: {passed, actual, threshold, direction}}
        """
        results = {}
        for name, threshold in SECONDARY_CRITERIA.items():
            actual = self._get_stat(name, stats)
            passed = self._check_criterion(name, actual, threshold)
            direction = self._get_direction(name)
            results[name] = {
                "passed": passed,
                "actual": actual,
                "threshold": threshold,
                "direction": direction,
            }
        return results

    @staticmethod
    def _get_stat(name: str, stats: dict):
        """Extrait la valeur du KPI correspondant au critere.

        Mapping des noms de criteres vers les cles stats :
            min_calendar_days -> calendar_days
            min_trades -> trades
            min_strategies_active -> strategies_active
            max_drawdown_pct -> drawdown_pct
            max_single_loss_pct -> single_loss_pct
            bugs_critiques -> bugs_critiques
            reconciliation_errors -> reconciliation_errors
            sharpe_period -> sharpe_period
            win_rate -> win_rate
            etc.
        """
        # Supprimer les prefixes min_/max_ pour trouver la cle
        key = name
        for prefix in ("min_", "max_"):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break

        return stats.get(key)

    @staticmethod
    def _get_direction(name: str) -> str:
        """Determine la direction de comparaison pour un critere."""
        if name.startswith("min_"):
            return "min"
        elif name.startswith("max_"):
            return "max"
        else:
            # Criteres zero (bugs_critiques, reconciliation_errors)
            return "zero"

    @staticmethod
    def _check_criterion(name: str, actual, threshold) -> bool:
        """Verifie si un critere est satisfait.

        Logique :
            - min_* : actual >= threshold
            - max_* : actual <= threshold
            - zero (bugs_critiques, reconciliation_errors) : actual == threshold (0)
            - secondaires min (sharpe, win_rate, profit_factor, execution_quality) : actual >= threshold
            - secondaires max (slippage_ratio) : actual <= threshold
        """
        if actual is None:
            return False

        if name.startswith("min_"):
            return actual >= threshold
        elif name.startswith("max_"):
            return actual <= threshold
        elif name in ("bugs_critiques", "reconciliation_errors"):
            # Criteres zero : doit etre exactement egal au seuil (0)
            return actual == threshold
        elif name == "slippage_ratio":
            # Secondaire : doit etre inferieur au seuil
            return actual <= threshold
        else:
            # Secondaires positifs (sharpe, win_rate, profit_factor, execution_quality)
            return actual >= threshold
