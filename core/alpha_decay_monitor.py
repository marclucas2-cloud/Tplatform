"""
Alpha Decay Monitor — surveillance du declin du Sharpe ratio par strategie.

Detecte la degradation progressive de l'alpha (edge) d'une strategie via :
  1. Sharpe rolling sur une fenetre glissante
  2. Regression lineaire sur le Sharpe rolling (pente + p-value)
  3. Estimation de la date de crossing zero (quand le Sharpe atteint 0)
  4. Generation d'alertes et rapport markdown

Usage :
    monitor = AlphaDecayMonitor()

    # A partir des returns journaliers d'une strategie
    returns = [0.002, -0.001, 0.003, ...]
    rolling = monitor.calculate_rolling_sharpe(returns, window=30)
    decay = monitor.detect_decay(rolling)

    if decay["alert"]:
        print(f"ALERTE: {decay['slope']:.4f}/jour, crossing zero dans {decay['days_to_zero']}j")

    # Rapport complet multi-strategies
    report = monitor.generate_report(strategies_data)
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Seuils par defaut
DEFAULT_WINDOW = 30          # Rolling window en nombre de trades
DEFAULT_P_THRESHOLD = 0.10   # p-value pour declarer une tendance significative
ANNUALIZATION_FACTOR = math.sqrt(252)  # Pour annualiser le Sharpe


class AlphaDecayMonitor:
    """Surveille le declin du Sharpe ratio par strategie.

    Le Sharpe rolling est calcule sur les N derniers trades (pas jours),
    car les strategies intraday ne tradent pas tous les jours.
    """

    def __init__(self, window: int = DEFAULT_WINDOW,
                 p_threshold: float = DEFAULT_P_THRESHOLD):
        """
        Args:
            window: nombre de trades pour le calcul du Sharpe rolling.
            p_threshold: seuil de p-value pour l'alerte de decay.
        """
        self.window = window
        self.p_threshold = p_threshold

    def calculate_rolling_sharpe(self, returns: List[float],
                                  window: Optional[int] = None) -> List[float]:
        """Calcule le Sharpe ratio rolling sur les N derniers trades.

        Args:
            returns: liste des returns par trade (pas annualises).
            window: taille de la fenetre (defaut: self.window).

        Returns:
            Liste de Sharpe ratios (annualises). Longueur = len(returns) - window + 1.
            Retourne une liste vide si pas assez de donnees.
        """
        if window is None:
            window = self.window

        if len(returns) < window:
            logger.debug(
                f"Pas assez de trades ({len(returns)} < {window}) "
                f"pour le rolling Sharpe"
            )
            return []

        arr = np.array(returns, dtype=float)
        rolling_sharpes = []

        for i in range(len(arr) - window + 1):
            chunk = arr[i:i + window]
            mean_ret = np.mean(chunk)
            std_ret = np.std(chunk, ddof=1)

            if std_ret > 1e-10:
                sharpe = (mean_ret / std_ret) * ANNUALIZATION_FACTOR
            else:
                # Volatilite quasi nulle — Sharpe indefini, on met 0
                sharpe = 0.0

            rolling_sharpes.append(round(float(sharpe), 4))

        return rolling_sharpes

    def detect_decay(self, rolling_sharpes: List[float],
                     p_threshold: Optional[float] = None) -> dict:
        """Detecte un decay significatif via regression lineaire.

        Regression : Sharpe_i = a + b * i + epsilon
        Si b < 0 et p-value(b) < seuil → decay confirme.

        Args:
            rolling_sharpes: liste de Sharpe rolling (output de calculate_rolling_sharpe).
            p_threshold: seuil de p-value (defaut: self.p_threshold).

        Returns:
            {
                slope: float,            # pente de la regression (negatif = decay)
                intercept: float,        # ordonnee a l'origine
                p_value: float,          # significativite de la pente
                r_squared: float,        # qualite de l'ajustement
                current_sharpe: float,   # dernier Sharpe rolling
                days_to_zero: int | None,  # jours estimes avant Sharpe = 0
                alert: bool,             # True si decay significatif
                severity: str,           # "none", "warning", "critical"
                message: str,            # description humaine
            }
        """
        if p_threshold is None:
            p_threshold = self.p_threshold

        if len(rolling_sharpes) < 5:
            return {
                "slope": 0.0,
                "intercept": 0.0,
                "p_value": 1.0,
                "r_squared": 0.0,
                "current_sharpe": rolling_sharpes[-1] if rolling_sharpes else 0.0,
                "days_to_zero": None,
                "alert": False,
                "severity": "none",
                "message": "Pas assez de donnees pour detecter un trend",
            }

        n = len(rolling_sharpes)
        x = np.arange(n, dtype=float)
        y = np.array(rolling_sharpes, dtype=float)

        # Regression lineaire manuelle (evite scipy comme dependance lourde)
        slope, intercept, r_squared, p_value = self._linear_regression(x, y)

        current_sharpe = rolling_sharpes[-1]

        # Estimation du crossing zero
        days_to_zero = None
        if slope < -1e-8 and current_sharpe > 0:
            # Sharpe actuel + slope * days = 0 → days = -current / slope
            steps_to_zero = -current_sharpe / slope
            # Convertir steps en jours (approximation : 1 step = 1 jour)
            days_to_zero = max(1, int(round(steps_to_zero)))

        # Classification de la severite
        alert = False
        severity = "none"
        message = "Sharpe stable — pas de decay detecte"

        if slope < 0 and p_value < p_threshold:
            alert = True
            if days_to_zero is not None and days_to_zero < 30:
                severity = "critical"
                message = (
                    f"CRITICAL: Sharpe en chute libre — crossing zero dans ~{days_to_zero}j "
                    f"(pente={slope:.4f}/step, p={p_value:.4f})"
                )
            elif days_to_zero is not None and days_to_zero < 90:
                severity = "warning"
                message = (
                    f"WARNING: Decay detecte — crossing zero dans ~{days_to_zero}j "
                    f"(pente={slope:.4f}/step, p={p_value:.4f})"
                )
            else:
                severity = "warning"
                message = (
                    f"WARNING: Tendance baissiere detectee "
                    f"(pente={slope:.4f}/step, p={p_value:.4f})"
                )
        elif slope < 0 and p_value < 0.2:
            message = f"Tendance legerement baissiere mais non significative (p={p_value:.4f})"

        return {
            "slope": round(float(slope), 6),
            "intercept": round(float(intercept), 4),
            "p_value": round(float(p_value), 6),
            "r_squared": round(float(r_squared), 4),
            "current_sharpe": round(float(current_sharpe), 4),
            "days_to_zero": days_to_zero,
            "alert": alert,
            "severity": severity,
            "message": message,
        }

    def analyze_strategy(self, returns: List[float],
                          strategy_name: str = "unknown") -> dict:
        """Analyse complete d'une strategie : rolling Sharpe + detection decay.

        Args:
            returns: returns par trade.
            strategy_name: nom de la strategie.

        Returns:
            Dict avec rolling_sharpes, decay_analysis, et metadata.
        """
        rolling = self.calculate_rolling_sharpe(returns)
        decay = self.detect_decay(rolling)

        return {
            "strategy": strategy_name,
            "total_trades": len(returns),
            "rolling_sharpes": rolling,
            "decay": decay,
            "overall_sharpe": self._compute_sharpe(returns),
        }

    def generate_report(self, strategies_data: Dict[str, List[float]]) -> str:
        """Genere un rapport markdown avec les trends par strategie.

        Args:
            strategies_data: {strategy_name: [returns_par_trade]}

        Returns:
            Rapport markdown.
        """
        lines = [
            "# Alpha Decay Report",
            f"",
            f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Strategies analysees : {len(strategies_data)}",
            f"",
        ]

        # Tableau resume
        lines.append("## Resume")
        lines.append("")
        lines.append("| Strategie | Trades | Sharpe | Sharpe rolling | Pente | p-value | Statut |")
        lines.append("|-----------|--------|--------|----------------|-------|---------|--------|")

        analyses = {}
        for name, returns in sorted(strategies_data.items()):
            analysis = self.analyze_strategy(returns, name)
            analyses[name] = analysis

            d = analysis["decay"]
            status = {
                "none": "OK",
                "warning": "**WARNING**",
                "critical": "**CRITICAL**",
            }.get(d["severity"], "?")

            lines.append(
                f"| {name} | {analysis['total_trades']} "
                f"| {analysis['overall_sharpe']:.2f} "
                f"| {d['current_sharpe']:.2f} "
                f"| {d['slope']:.4f} "
                f"| {d['p_value']:.4f} "
                f"| {status} |"
            )

        # Alertes detaillees
        alerts = [
            (name, a) for name, a in analyses.items()
            if a["decay"]["alert"]
        ]

        if alerts:
            lines.extend(["", "## Alertes"])
            for name, analysis in alerts:
                d = analysis["decay"]
                lines.extend([
                    f"",
                    f"### {name} — {d['severity'].upper()}",
                    f"- {d['message']}",
                    f"- Sharpe global : {analysis['overall_sharpe']:.2f}",
                    f"- Sharpe rolling actuel : {d['current_sharpe']:.2f}",
                    f"- R-squared : {d['r_squared']:.4f}",
                ])
                if d["days_to_zero"] is not None:
                    lines.append(f"- Crossing zero estime : ~{d['days_to_zero']} jours")
                lines.append(f"- **Action recommandee** : reduire l'allocation ou desactiver")
        else:
            lines.extend(["", "## Alertes", "", "Aucune alerte — toutes les strategies sont stables."])

        # Recommandations
        lines.extend(["", "## Recommandations"])
        stable = [n for n, a in analyses.items() if not a["decay"]["alert"]]
        if stable:
            lines.append(f"- Strategies stables ({len(stable)}) : {', '.join(stable)}")
        if alerts:
            lines.append(
                f"- Strategies en decay ({len(alerts)}) : "
                f"{', '.join(n for n, _ in alerts)} → review necessaire"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _linear_regression(self, x: np.ndarray, y: np.ndarray) -> Tuple[float, float, float, float]:
        """Regression lineaire simple avec calcul de p-value.

        Retourne (slope, intercept, r_squared, p_value).
        Calcul sans scipy pour eviter la dependance.
        """
        n = len(x)
        if n < 3:
            return 0.0, 0.0, 0.0, 1.0

        x_mean = np.mean(x)
        y_mean = np.mean(y)

        ss_xx = np.sum((x - x_mean) ** 2)
        ss_yy = np.sum((y - y_mean) ** 2)
        ss_xy = np.sum((x - x_mean) * (y - y_mean))

        if ss_xx < 1e-15:
            return 0.0, float(y_mean), 0.0, 1.0

        slope = ss_xy / ss_xx
        intercept = y_mean - slope * x_mean

        # R-squared
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 1e-15 else 0.0

        # p-value via t-test sur la pente
        residuals = y - (slope * x + intercept)
        sse = np.sum(residuals ** 2)
        mse = sse / (n - 2) if n > 2 else 1e-10
        se_slope = math.sqrt(mse / ss_xx) if mse / ss_xx > 0 else 1e-10

        t_stat = slope / se_slope if se_slope > 1e-15 else 0.0
        df = n - 2

        # Approximation de la p-value via la distribution t
        # (evite scipy.stats.t.sf)
        p_value = self._t_to_pvalue(abs(t_stat), df) * 2  # two-tailed

        return float(slope), float(intercept), float(r_squared), float(min(p_value, 1.0))

    @staticmethod
    def _t_to_pvalue(t: float, df: int) -> float:
        """Approximation de P(T > t) pour une distribution t a df degres de liberte.

        Utilise l'approximation de Abramowitz & Stegun pour eviter scipy.
        Precision suffisante pour nos besoins (detection de trend).
        """
        if df <= 0 or t <= 0:
            return 0.5

        # Pour df > 30, approximation normale
        if df > 30:
            # Approximation z via formule de Cornish-Fisher
            z = t * (1 - 1 / (4 * df))
            # Approximation de la CDF normale (Abramowitz & Stegun 26.2.17)
            return AlphaDecayMonitor._normal_sf(z)

        # Pour petit df, approximation via beta incomplete
        # Formule : P(T > t) = 0.5 * I(df/(df+t^2); df/2, 1/2)
        x = df / (df + t * t)
        return 0.5 * AlphaDecayMonitor._betainc_approx(x, df / 2, 0.5)

    @staticmethod
    def _normal_sf(z: float) -> float:
        """Survival function de la distribution normale standard (1 - CDF).

        Approximation de Abramowitz & Stegun (formule 26.2.17).
        Precision : erreur < 7.5e-8.
        """
        if z < 0:
            return 1.0 - AlphaDecayMonitor._normal_sf(-z)
        if z > 8:
            return 0.0

        # Constantes
        b1 = 0.319381530
        b2 = -0.356563782
        b3 = 1.781477937
        b4 = -1.821255978
        b5 = 1.330274429
        p = 0.2316419

        t = 1.0 / (1.0 + p * z)
        t2 = t * t
        t3 = t2 * t
        t4 = t3 * t
        t5 = t4 * t

        pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
        return pdf * (b1 * t + b2 * t2 + b3 * t3 + b4 * t4 + b5 * t5)

    @staticmethod
    def _betainc_approx(x: float, a: float, b: float) -> float:
        """Approximation grossiere de la fonction beta incomplete regularisee I(x; a, b).

        Suffisante pour la detection de trend (pas pour un calcul statistique precis).
        Pour une precision superieure, installer scipy.
        """
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0

        # Approximation via serie de puissance (premiers termes)
        # I(x; a, b) ~ x^a * (1-x)^b / (a * B(a,b)) pour petit x
        try:
            log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
            log_val = a * math.log(x) + b * math.log(1 - x) - log_beta - math.log(a)

            # Correction pour ameliorer la precision
            result = math.exp(log_val)

            # Borne [0, 1]
            return max(0.0, min(1.0, result))
        except (ValueError, OverflowError):
            return 0.5

    @staticmethod
    def _compute_sharpe(returns: List[float]) -> float:
        """Calcule le Sharpe ratio annualise sur une serie de returns."""
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns, dtype=float)
        mean = np.mean(arr)
        std = np.std(arr, ddof=1)
        if std < 1e-10:
            return 0.0
        return round(float((mean / std) * ANNUALIZATION_FACTOR), 4)
