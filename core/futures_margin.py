"""
Futures Margin Tracker — suivi en temps reel de la marge utilisee/disponible.

Regles de risque :
  - Max 10% du capital par position futures
  - Max 30% du capital total en marge futures
  - Alertes a 70%, 85%, 95% d'utilisation
  - Micro-contrats uniquement si capital < $100K
"""
from __future__ import annotations

import logging
import math
from typing import Any

from core.broker.ibkr_futures import FuturesContractManager

logger = logging.getLogger(__name__)

# Seuils d'alerte marge
ALERT_THRESHOLDS = {
    "green": 0.70,    # < 70% = OK
    "yellow": 0.85,   # 70-85% = attention
    "orange": 0.95,   # 85-95% = danger
    "red": 1.00,      # > 95% = critique
}


class FuturesMarginTracker:
    """Suivi de la marge futures en temps reel.

    Usage:
        tracker = FuturesMarginTracker(total_capital=25000)
        health = tracker.check_margin_health(positions)
        # → {used: 2800, available: 4700, ratio: 0.37, alert_level: "green",
        #    max_total_margin: 7500, margin_by_position: [...]}

        max_mes = tracker.max_contracts("MES")
        # → 1 (car $1400 margin, max 10% de $25K = $2500)
    """

    def __init__(
        self,
        total_capital: float,
        contract_mgr: FuturesContractManager | None = None,
        max_margin_per_position: float = 0.10,
        max_total_futures_margin: float = 0.30,
    ):
        """
        Args:
            total_capital: capital total du compte en USD
            contract_mgr: gestionnaire de contrats (cree un par defaut)
            max_margin_per_position: % max du capital par position (defaut 10%)
            max_total_futures_margin: % max du capital en marge futures (defaut 30%)
        """
        if total_capital <= 0:
            raise ValueError(f"Capital invalide: {total_capital}")

        self._capital = total_capital
        self._contract_mgr = contract_mgr or FuturesContractManager()
        self._max_pos_pct = max_margin_per_position
        self._max_total_pct = max_total_futures_margin

    @property
    def total_capital(self) -> float:
        return self._capital

    @total_capital.setter
    def total_capital(self, value: float):
        if value <= 0:
            raise ValueError(f"Capital invalide: {value}")
        self._capital = value

    @property
    def max_total_margin(self) -> float:
        """Marge futures max autorisee en USD."""
        return self._capital * self._max_total_pct

    @property
    def max_position_margin(self) -> float:
        """Marge max par position en USD."""
        return self._capital * self._max_pos_pct

    def calculate_margin_used(self, positions: list[dict]) -> float:
        """Calcule la marge totale utilisee par les positions futures.

        Args:
            positions: [{symbol, qty}] — positions futures ouvertes

        Returns:
            Marge totale en USD (basee sur margin_initial)
        """
        total_margin = 0.0
        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = abs(pos.get("qty", 0))
            if qty == 0:
                continue

            try:
                margin = self._contract_mgr.get_margin_requirement(symbol)
                pos_margin = margin["initial"] * qty
                total_margin += pos_margin
            except ValueError:
                logger.warning(f"Symbole futures inconnu pour marge: {symbol}")

        return total_margin

    def calculate_margin_available(self, positions: list[dict] | None = None) -> float:
        """Calcule la marge disponible pour de nouvelles positions.

        Args:
            positions: positions ouvertes (defaut: aucune)

        Returns:
            Marge disponible en USD
        """
        if positions is None:
            positions = []

        used = self.calculate_margin_used(positions)
        available = self.max_total_margin - used
        return max(0.0, available)

    def check_margin_health(self, positions: list[dict] | None = None) -> dict:
        """Diagnostic complet de la sante de la marge.

        Args:
            positions: positions futures ouvertes

        Returns:
            {
                used: float,           # marge utilisee en USD
                available: float,      # marge disponible en USD
                max_total_margin: float,  # limite max (30% capital)
                ratio: float,          # used / max (0.0 = vide, 1.0 = plein)
                alert_level: str,      # "green", "yellow", "orange", "red"
                margin_by_position: [   # detail par position
                    {symbol, qty, margin, pct_of_capital}
                ],
                violations: [str],     # liste des violations detectees
            }
        """
        if positions is None:
            positions = []

        used = self.calculate_margin_used(positions)
        available = self.calculate_margin_available(positions)
        ratio = used / self.max_total_margin if self.max_total_margin > 0 else 0.0

        # Determiner le niveau d'alerte
        alert_level = "green"
        if ratio >= ALERT_THRESHOLDS["red"]:
            alert_level = "red"
        elif ratio >= ALERT_THRESHOLDS["orange"]:
            alert_level = "orange"
        elif ratio >= ALERT_THRESHOLDS["yellow"]:
            alert_level = "yellow"

        # Detail par position
        margin_by_position = []
        violations = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = abs(pos.get("qty", 0))
            if qty == 0:
                continue

            try:
                margin = self._contract_mgr.get_margin_requirement(symbol)
                pos_margin = margin["initial"] * qty
                pct = pos_margin / self._capital if self._capital > 0 else 0

                margin_by_position.append({
                    "symbol": symbol,
                    "qty": qty,
                    "margin": pos_margin,
                    "pct_of_capital": round(pct, 4),
                })

                # Verifier la limite par position
                if pct > self._max_pos_pct:
                    violations.append(
                        f"{symbol}: marge ${pos_margin:,.0f} = {pct:.1%} du capital "
                        f"(max {self._max_pos_pct:.0%})"
                    )
            except ValueError:
                pass

        # Verifier la limite totale
        if ratio > 1.0:
            violations.append(
                f"Marge totale ${used:,.0f} depasse la limite "
                f"${self.max_total_margin:,.0f} ({ratio:.1%})"
            )

        if alert_level in ("orange", "red"):
            logger.warning(
                f"MARGE FUTURES ALERT [{alert_level.upper()}]: "
                f"${used:,.0f}/${self.max_total_margin:,.0f} ({ratio:.1%})"
            )

        return {
            "used": round(used, 2),
            "available": round(available, 2),
            "max_total_margin": round(self.max_total_margin, 2),
            "ratio": round(ratio, 4),
            "alert_level": alert_level,
            "margin_by_position": margin_by_position,
            "violations": violations,
        }

    def max_contracts(
        self,
        symbol: str,
        max_pct: float | None = None,
        current_positions: list[dict] | None = None,
    ) -> int:
        """Calcule le nombre max de contrats qu'on peut ouvrir.

        Prend en compte :
          1. La limite par position (max_pct du capital)
          2. La marge disponible (apres positions existantes)

        Args:
            symbol: symbole futures (ex. "MES")
            max_pct: % max du capital pour cette position (defaut: max_margin_per_position)
            current_positions: positions existantes pour calculer la marge disponible

        Returns:
            Nombre max de contrats (entier, >= 0)
        """
        if max_pct is None:
            max_pct = self._max_pos_pct

        try:
            margin = self._contract_mgr.get_margin_requirement(symbol)
        except ValueError:
            return 0

        margin_initial = margin["initial"]
        if margin_initial <= 0:
            return 0

        # Limite 1 : % du capital par position
        max_by_position = math.floor(
            (self._capital * max_pct) / margin_initial
        )

        # Limite 2 : marge disponible
        available = self.calculate_margin_available(current_positions or [])
        max_by_available = math.floor(available / margin_initial)

        result = max(0, min(max_by_position, max_by_available))

        logger.debug(
            f"max_contracts({symbol}): margin_init=${margin_initial}, "
            f"max_by_pos={max_by_position}, max_by_avail={max_by_available} "
            f"→ {result}"
        )
        return result

    def validate_new_position(
        self,
        symbol: str,
        qty: int,
        current_positions: list[dict] | None = None,
    ) -> tuple[bool, str]:
        """Valide une nouvelle position futures contre les limites de marge.

        Args:
            symbol: symbole futures
            qty: nombre de contrats souhaite
            current_positions: positions existantes

        Returns:
            (passed: bool, message: str)
        """
        if current_positions is None:
            current_positions = []

        try:
            margin = self._contract_mgr.get_margin_requirement(symbol)
        except ValueError as e:
            return False, str(e)

        new_margin = margin["initial"] * abs(qty)
        new_pct = new_margin / self._capital if self._capital > 0 else float("inf")

        # Check 1 : limite par position
        if new_pct > self._max_pos_pct:
            return False, (
                f"REFUSE: marge {symbol} x{qty} = ${new_margin:,.0f} "
                f"({new_pct:.1%} du capital, max {self._max_pos_pct:.0%})"
            )

        # Check 2 : marge disponible
        available = self.calculate_margin_available(current_positions)
        if new_margin > available:
            return False, (
                f"REFUSE: marge insuffisante pour {symbol} x{qty}. "
                f"Requis: ${new_margin:,.0f}, disponible: ${available:,.0f}"
            )

        # Check 3 : micro uniquement si capital < $100K
        ok, msg = self._contract_mgr.validate_capital(symbol, self._capital)
        if not ok:
            return False, msg

        return True, f"OK: {symbol} x{qty}, marge ${new_margin:,.0f}"
