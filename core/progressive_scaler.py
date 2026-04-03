"""
ProgressiveScaler — ROC-009 Scaling progressif au lieu de gate binaire.

Au lieu d'un passage tout-ou-rien entre les phases, augmente le sizing
graduellement en fonction du nombre de trades et de la sante du portefeuille :

  Trades 0-5:   1/8 Kelly (soft launch)
  Trades 6-10:  3/16 Kelly (si DD < 2% et 0 bugs)
  Trades 11-15: 1/4 Kelly (si DD < 3% et 0 bugs)
  Trades 16-20: 1/4 Kelly confirme
  Trades 20+:   Evaluation Gate M1 -> ajout de capital possible

Si le drawdown augmente -> retour au niveau precedent.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# Progression par defaut : chaque niveau avec ses conditions
DEFAULT_PROGRESSION = [
    {
        "level": 0,
        "name": "SOFT_LAUNCH",
        "fraction": 0.125,        # 1/8 Kelly
        "min_trades": 0,
        "max_dd_pct": 100.0,      # Pas de limite au niveau 0
        "max_bugs": 999,           # Pas de limite au niveau 0
    },
    {
        "level": 1,
        "name": "RAMP_UP_1",
        "fraction": 0.1875,       # 3/16 Kelly
        "min_trades": 6,
        "max_dd_pct": 2.0,
        "max_bugs": 0,
    },
    {
        "level": 2,
        "name": "RAMP_UP_2",
        "fraction": 0.25,         # 1/4 Kelly
        "min_trades": 11,
        "max_dd_pct": 3.0,
        "max_bugs": 0,
    },
    {
        "level": 3,
        "name": "CONFIRMED",
        "fraction": 0.25,         # 1/4 Kelly confirme
        "min_trades": 16,
        "max_dd_pct": 4.0,
        "max_bugs": 0,
    },
    {
        "level": 4,
        "name": "GATE_M1",
        "fraction": 0.25,         # Gate M1 -> ajout capital possible
        "min_trades": 21,
        "max_dd_pct": 5.0,
        "max_bugs": 0,
    },
]

# Criteres Gate M1
GATE_M1_PRIMARY = {
    "min_calendar_days": 14,
    "min_trades": 15,
    "min_strategies": 3,
    "max_dd_pct": 5.0,
    "max_single_loss_pct": 2.0,
    "max_bugs": 0,
    "max_recon_errors": 0,
}

GATE_M1_SECONDARY = {
    "min_sharpe": 0.3,
    "min_win_rate": 0.42,
    "min_profit_factor": 1.1,
    "max_slippage_ratio": 3.0,
    "min_execution_quality": 0.85,
}

# Nombre minimum de criteres secondaires a satisfaire
GATE_M1_SECONDARY_MIN = 3


class ProgressiveScaler:
    """Scaling progressif : augmente la fraction Kelly graduellement.

    Verifie les conditions a chaque niveau avant de monter.
    Revient au niveau precedent si le drawdown se degrade.
    """

    def __init__(self, progression: List[Dict] | None = None):
        """
        Args:
            progression: liste de niveaux avec conditions (override le defaut)
        """
        self.progression = progression or [dict(p) for p in DEFAULT_PROGRESSION]

        # Trier par niveau pour garantir l'ordre
        self.progression.sort(key=lambda x: x["level"])

        # Niveau actuel (demarre a 0)
        self._current_level = 0

        logger.info(
            "ProgressiveScaler initialise — %d niveaux, depart=%s (%.4f Kelly)",
            len(self.progression),
            self.progression[0]["name"],
            self.progression[0]["fraction"],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_fraction(
        self,
        trade_count: int,
        max_drawdown_pct: float,
        critical_bugs: int = 0,
    ) -> dict:
        """Determine la fraction Kelly actuelle basee sur la progression.

        Evalue le niveau le plus eleve atteignable en fonction des conditions,
        puis retourne la fraction correspondante.

        Args:
            trade_count: nombre total de trades executes
            max_drawdown_pct: drawdown max observe (en %, positif = perte)
            critical_bugs: nombre de bugs critiques non resolus

        Returns:
            {
                fraction: float,
                level_name: str,
                level: int,
                next_level_at: int or None,
                conditions_met: bool,
            }
        """
        # Trouver le niveau le plus eleve atteignable
        best_level = 0

        for prog in self.progression:
            level = prog["level"]
            if level == 0:
                # Niveau 0 toujours accessible
                best_level = 0
                continue

            # Verifier toutes les conditions
            trades_ok = trade_count >= prog["min_trades"]
            dd_ok = max_drawdown_pct <= prog["max_dd_pct"]
            bugs_ok = critical_bugs <= prog["max_bugs"]

            if trades_ok and dd_ok and bugs_ok:
                best_level = level
            else:
                # On ne peut pas sauter un niveau
                break

        # Verifier si on doit revert (le DD a augmente)
        if best_level < self._current_level:
            logger.warning(
                "Revert de niveau %d (%s) -> %d (%s) — conditions non satisfaites",
                self._current_level,
                self.progression[self._current_level]["name"],
                best_level,
                self.progression[best_level]["name"],
            )

        # Mettre a jour le niveau actuel
        if best_level != self._current_level:
            old_name = self.progression[self._current_level]["name"]
            new_name = self.progression[best_level]["name"]
            if best_level > self._current_level:
                logger.info(
                    "Progression: %s -> %s (fraction %.4f -> %.4f)",
                    old_name,
                    new_name,
                    self.progression[self._current_level]["fraction"],
                    self.progression[best_level]["fraction"],
                )
            self._current_level = best_level

        current_prog = self.progression[self._current_level]

        # Determiner le prochain niveau
        next_level_at = None
        conditions_met = True
        if self._current_level < len(self.progression) - 1:
            next_prog = self.progression[self._current_level + 1]
            next_level_at = next_prog["min_trades"]

            # Verifier si les conditions du niveau actuel sont satisfaites
            # (utile pour savoir si on est bloque par le DD ou les bugs)
            if self._current_level > 0:
                curr = self.progression[self._current_level]
                conditions_met = (
                    trade_count >= curr["min_trades"]
                    and max_drawdown_pct <= curr["max_dd_pct"]
                    and critical_bugs <= curr["max_bugs"]
                )

        return {
            "fraction": current_prog["fraction"],
            "level_name": current_prog["name"],
            "level": current_prog["level"],
            "next_level_at": next_level_at,
            "conditions_met": conditions_met,
        }

    def should_revert(self, current_level: int, max_drawdown_pct: float) -> bool:
        """Verifie si le drawdown actuel necessite un retour au niveau precedent.

        Args:
            current_level: niveau actuel (0-4)
            max_drawdown_pct: drawdown max observe (en %, positif = perte)

        Returns:
            True si le retour est necessaire
        """
        if current_level <= 0:
            return False

        if current_level >= len(self.progression):
            return False

        prog = self.progression[current_level]
        should = max_drawdown_pct > prog["max_dd_pct"]

        if should:
            logger.warning(
                "Revert recommande: DD %.1f%% > max %.1f%% pour niveau %d (%s)",
                max_drawdown_pct,
                prog["max_dd_pct"],
                current_level,
                prog["name"],
            )

        return should

    def get_scaling_timeline(self) -> list:
        """Retourne le plan de progression complet.

        Returns:
            Liste de dicts avec les details de chaque niveau
        """
        timeline = []
        for prog in self.progression:
            timeline.append({
                "level": prog["level"],
                "name": prog["name"],
                "fraction": prog["fraction"],
                "kelly_display": f"{prog['fraction']:.4f}",
                "min_trades": prog["min_trades"],
                "max_dd_pct": prog["max_dd_pct"],
                "max_bugs": prog["max_bugs"],
            })
        return timeline

    def evaluate_gate_m1(self, stats: dict) -> dict:
        """Evaluation complete de la Gate M1 pour passer a l'ajout de capital.

        La Gate M1 est le point de decision pour augmenter le capital deploye.
        Tous les criteres primaires doivent etre satisfaits.
        Au moins 3/5 criteres secondaires doivent etre satisfaits.

        Args:
            stats: dict avec les metriques de performance :
                - calendar_days: int
                - trades: int
                - strategies: int
                - max_dd_pct: float
                - max_single_loss_pct: float
                - bugs: int
                - recon_errors: int
                - sharpe: float
                - win_rate: float (0-1)
                - profit_factor: float
                - slippage_ratio: float
                - execution_quality: float (0-1)

        Returns:
            {
                passed: bool,
                primary_passed: bool,
                secondary_passed: bool,
                primary_results: [{name, passed, actual, threshold}],
                secondary_results: [{name, passed, actual, threshold}],
                secondary_met: int,
                secondary_required: int,
                recommendation: str,
            }
        """
        # --- Criteres primaires (TOUS requis) ---
        primary_results = []

        primary_checks = [
            ("min_calendar_days", stats.get("calendar_days", 0),
             GATE_M1_PRIMARY["min_calendar_days"], ">="),
            ("min_trades", stats.get("trades", 0),
             GATE_M1_PRIMARY["min_trades"], ">="),
            ("min_strategies", stats.get("strategies", 0),
             GATE_M1_PRIMARY["min_strategies"], ">="),
            ("max_dd_pct", stats.get("max_dd_pct", 100.0),
             GATE_M1_PRIMARY["max_dd_pct"], "<="),
            ("max_single_loss_pct", stats.get("max_single_loss_pct", 100.0),
             GATE_M1_PRIMARY["max_single_loss_pct"], "<="),
            ("max_bugs", stats.get("bugs", 999),
             GATE_M1_PRIMARY["max_bugs"], "<="),
            ("max_recon_errors", stats.get("recon_errors", 999),
             GATE_M1_PRIMARY["max_recon_errors"], "<="),
        ]

        for name, actual, threshold, op in primary_checks:
            if op == ">=":
                passed = actual >= threshold
            else:  # "<="
                passed = actual <= threshold

            primary_results.append({
                "name": name,
                "passed": passed,
                "actual": actual,
                "threshold": threshold,
            })

        primary_passed = all(r["passed"] for r in primary_results)

        # --- Criteres secondaires (3/5 requis) ---
        secondary_results = []

        secondary_checks = [
            ("min_sharpe", stats.get("sharpe", 0.0),
             GATE_M1_SECONDARY["min_sharpe"], ">="),
            ("min_win_rate", stats.get("win_rate", 0.0),
             GATE_M1_SECONDARY["min_win_rate"], ">="),
            ("min_profit_factor", stats.get("profit_factor", 0.0),
             GATE_M1_SECONDARY["min_profit_factor"], ">="),
            ("max_slippage_ratio", stats.get("slippage_ratio", 999.0),
             GATE_M1_SECONDARY["max_slippage_ratio"], "<="),
            ("min_execution_quality", stats.get("execution_quality", 0.0),
             GATE_M1_SECONDARY["min_execution_quality"], ">="),
        ]

        for name, actual, threshold, op in secondary_checks:
            if op == ">=":
                passed = actual >= threshold
            else:  # "<="
                passed = actual <= threshold

            secondary_results.append({
                "name": name,
                "passed": passed,
                "actual": actual,
                "threshold": threshold,
            })

        secondary_met = sum(1 for r in secondary_results if r["passed"])
        secondary_passed = secondary_met >= GATE_M1_SECONDARY_MIN

        # --- Decision finale ---
        overall_passed = primary_passed and secondary_passed

        if overall_passed:
            recommendation = "GATE M1 PASSED — ajout de capital autorise"
        elif not primary_passed:
            failed_primary = [r["name"] for r in primary_results if not r["passed"]]
            recommendation = (
                f"GATE M1 FAILED — criteres primaires non satisfaits: "
                f"{', '.join(failed_primary)}"
            )
        else:
            recommendation = (
                f"GATE M1 FAILED — seulement {secondary_met}/{GATE_M1_SECONDARY_MIN} "
                f"criteres secondaires satisfaits"
            )

        logger.info(
            "Gate M1 evaluation: %s (primary=%s, secondary=%d/%d)",
            "PASSED" if overall_passed else "FAILED",
            "OK" if primary_passed else "FAIL",
            secondary_met,
            GATE_M1_SECONDARY_MIN,
        )

        return {
            "passed": overall_passed,
            "primary_passed": primary_passed,
            "secondary_passed": secondary_passed,
            "primary_results": primary_results,
            "secondary_results": secondary_results,
            "secondary_met": secondary_met,
            "secondary_required": GATE_M1_SECONDARY_MIN,
            "recommendation": recommendation,
        }
