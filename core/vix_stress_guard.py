"""
VIX / SPY Stress Guard -- reduit le sizing des nouvelles positions en cas de stress marche.

Triggers :
  - VIX > 30  -> reduce sizing 50%
  - VIX > 40  -> reduce sizing 75%
  - SPY intraday DD > 3%  -> reduce sizing 50%
  - SPY intraday DD > 5%  -> HALT (sizing = 0, aucune nouvelle entree)

Intervalle de check : toutes les 15 minutes pendant les heures de marche.

Ce n'est PAS un kill switch : ne ferme aucune position existante.
Reduit uniquement le sizing des NOUVELLES positions.
"""

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Niveaux de stress
NORMAL = "NORMAL"
WARN = "WARN"
CRITICAL = "CRITICAL"
HALT = "HALT"

# Import optionnel de yfinance
try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    yf = None
    _HAS_YFINANCE = False


class VixStressGuard:
    """
    Reduce exposure when market stress is detected.

    Triggers:
    - VIX > 30 -> reduce sizing by 50%
    - VIX > 40 -> reduce sizing by 75%
    - SPY intraday DD > 3% -> reduce sizing by 50%
    - SPY intraday DD > 5% -> HALT all new entries

    Check interval: every 15 minutes during market hours.

    NOT a kill switch (doesn't close positions).
    Just reduces NEW position sizing.
    """

    def __init__(
        self,
        vix_warn: float = 30.0,
        vix_critical: float = 40.0,
        spy_dd_warn: float = 3.0,
        spy_dd_halt: float = 5.0,
    ):
        """
        Args:
            vix_warn: seuil VIX pour reduction 50% (defaut 30)
            vix_critical: seuil VIX pour reduction 75% (defaut 40)
            spy_dd_warn: seuil drawdown SPY intraday % pour reduction 50% (defaut 3.0)
            spy_dd_halt: seuil drawdown SPY intraday % pour HALT (defaut 5.0)
        """
        self.vix_warn = vix_warn
        self.vix_critical = vix_critical
        self.spy_dd_warn = spy_dd_warn
        self.spy_dd_halt = spy_dd_halt

        # Etat interne cache
        self._last_check: datetime | None = None
        self._last_vix: float | None = None
        self._last_spy_change: float | None = None
        self._cached_result: dict = {
            "sizing_factor": 1.0,
            "level": NORMAL,
            "reason": "Pas encore de check effectue",
        }

        logger.info(
            f"VixStressGuard initialise: VIX warn={vix_warn}, critical={vix_critical}, "
            f"SPY DD warn={spy_dd_warn}%, halt={spy_dd_halt}%"
        )

    def check(
        self,
        vix_level: float | None = None,
        spy_change_pct: float | None = None,
    ) -> dict:
        """Evalue le niveau de stress marche et retourne le facteur de sizing.

        Si vix_level ou spy_change_pct ne sont pas fournis, tente de les
        recuperer via yfinance (fetch_vix_level / fetch_spy_change).

        Args:
            vix_level: niveau VIX actuel (ex: 25.3). None = auto-fetch.
            spy_change_pct: variation intraday SPY en % (ex: -3.5 pour -3.5%). None = auto-fetch.

        Returns:
            {
                "sizing_factor": float 0.0-1.0,
                "level": "NORMAL" | "WARN" | "CRITICAL" | "HALT",
                "reason": str,
            }
        """
        # Auto-fetch si pas fourni
        if vix_level is None:
            vix_level = self.fetch_vix_level()
        if spy_change_pct is None:
            spy_change_pct = self.fetch_spy_change()

        # Mettre a jour le cache des valeurs brutes
        self._last_vix = vix_level
        self._last_spy_change = spy_change_pct
        self._last_check = datetime.now(UTC)

        # Determiner le niveau de stress le plus severe
        sizing_factor = 1.0
        level = NORMAL
        reasons = []

        # --- Checks VIX ---
        if vix_level is not None:
            if vix_level > self.vix_critical:
                # VIX > 40 : reduction 75%
                vix_factor = 0.25
                vix_level_str = CRITICAL
                reasons.append(f"VIX={vix_level:.1f} > {self.vix_critical} (critical, sizing x0.25)")
            elif vix_level > self.vix_warn:
                # VIX > 30 : reduction 50%
                vix_factor = 0.50
                vix_level_str = WARN
                reasons.append(f"VIX={vix_level:.1f} > {self.vix_warn} (warn, sizing x0.50)")
            else:
                vix_factor = 1.0
                vix_level_str = NORMAL
        else:
            vix_factor = 1.0
            vix_level_str = NORMAL

        # --- Checks SPY drawdown intraday ---
        if spy_change_pct is not None:
            # spy_change_pct est negatif en cas de baisse (ex: -4.2)
            spy_dd = abs(spy_change_pct) if spy_change_pct < 0 else 0.0

            if spy_dd > self.spy_dd_halt:
                # SPY DD > 5% : HALT complet
                spy_factor = 0.0
                spy_level_str = HALT
                reasons.append(
                    f"SPY DD={spy_dd:.1f}% > {self.spy_dd_halt}% (HALT, aucune nouvelle entree)"
                )
            elif spy_dd > self.spy_dd_warn:
                # SPY DD > 3% : reduction 50%
                spy_factor = 0.50
                spy_level_str = WARN
                reasons.append(f"SPY DD={spy_dd:.1f}% > {self.spy_dd_warn}% (warn, sizing x0.50)")
            else:
                spy_factor = 1.0
                spy_level_str = NORMAL
        else:
            spy_factor = 1.0
            spy_level_str = NORMAL

        # Prendre le facteur le plus restrictif (minimum)
        sizing_factor = min(vix_factor, spy_factor)

        # Prendre le niveau le plus severe
        level_priority = {NORMAL: 0, WARN: 1, CRITICAL: 2, HALT: 3}
        level = max(
            [vix_level_str, spy_level_str],
            key=lambda x: level_priority[x],
        )

        if not reasons:
            reasons.append("Conditions normales")

        result = {
            "sizing_factor": sizing_factor,
            "level": level,
            "reason": " | ".join(reasons),
        }

        # Logger selon le niveau
        if level == HALT:
            logger.critical(f"STRESS GUARD HALT: {result['reason']}")
        elif level == CRITICAL:
            logger.warning(f"STRESS GUARD CRITICAL: {result['reason']}")
        elif level == WARN:
            logger.warning(f"STRESS GUARD WARN: {result['reason']}")
        else:
            logger.debug(f"STRESS GUARD OK: {result['reason']}")

        # Mettre en cache
        self._cached_result = result
        return result

    def get_sizing_factor(self) -> float:
        """Retourne le facteur de sizing cache depuis le dernier check.

        Returns:
            float entre 0.0 (HALT) et 1.0 (normal)
        """
        return self._cached_result["sizing_factor"]

    def get_status(self) -> dict:
        """Retourne l'etat complet du guard.

        Returns:
            {
                "level": str,
                "sizing_factor": float,
                "reason": str,
                "last_check": str ISO ou None,
                "vix_level": float ou None,
                "spy_change_pct": float ou None,
            }
        """
        return {
            "level": self._cached_result["level"],
            "sizing_factor": self._cached_result["sizing_factor"],
            "reason": self._cached_result["reason"],
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "vix_level": self._last_vix,
            "spy_change_pct": self._last_spy_change,
        }

    def fetch_vix_level(self) -> float | None:
        """Recupere le niveau VIX actuel via yfinance.

        Returns:
            Niveau VIX (float) ou None si indisponible.
        """
        if not _HAS_YFINANCE:
            logger.debug("yfinance non disponible, VIX non recupere")
            return None

        try:
            ticker = yf.Ticker("^VIX")
            hist = ticker.history(period="1d")
            if hist.empty:
                logger.warning("VIX: pas de donnees retournees par yfinance")
                return None
            vix = float(hist["Close"].iloc[-1])
            logger.debug(f"VIX fetched: {vix:.2f}")
            return vix
        except Exception as e:
            logger.warning(f"Erreur fetch VIX: {e}")
            return None

    def fetch_spy_change(self) -> float | None:
        """Recupere la variation intraday SPY via yfinance.

        Returns:
            Variation en % (ex: -2.5 pour -2.5%) ou None si indisponible.
        """
        if not _HAS_YFINANCE:
            logger.debug("yfinance non disponible, SPY change non recupere")
            return None

        try:
            ticker = yf.Ticker("SPY")
            hist = ticker.history(period="1d")
            if hist.empty:
                logger.warning("SPY: pas de donnees retournees par yfinance")
                return None
            open_price = float(hist["Open"].iloc[-1])
            close_price = float(hist["Close"].iloc[-1])
            if open_price <= 0:
                return None
            change_pct = ((close_price - open_price) / open_price) * 100.0
            logger.debug(f"SPY change fetched: {change_pct:+.2f}%")
            return round(change_pct, 2)
        except Exception as e:
            logger.warning(f"Erreur fetch SPY change: {e}")
            return None
