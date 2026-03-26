"""
Capital Scheduler — Multi-horizon stacking (ROC-4).

Gere le meme capital sur plusieurs horizons temporels.

Le meme $10K peut simultanement :
- Etre alloue aux strategies intraday EU (9:00-17:30 CET)
- Servir de margin pour un swing FX (tenu 5 jours)
- Etre le buffer de securite pour les positions US (15:30-22:00)

Contrainte : gross exposure < 90% a tout moment.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Exposure max absolue (guard de securite)
MAX_GROSS_EXPOSURE = 0.90


class Position:
    """Representation d'une position ouverte avec horizon temporel."""

    def __init__(
        self,
        strategy: str,
        market: str,
        notional: float,
        margin_required: float,
        horizon: str,
        entry_hour_cet: int,
        expected_exit_hour_cet: Optional[int] = None,
        is_leveraged: bool = False,
        leverage: float = 1.0,
    ):
        """
        Args:
            strategy: nom de la strategie
            market: 'us', 'eu', 'fx', 'futures'
            notional: exposition notionnelle (USD)
            margin_required: capital effectivement immobilise (USD)
            horizon: 'intraday_eu', 'intraday_us', 'swing', 'overnight', 'monthly'
            entry_hour_cet: heure CET d'entree (0-23)
            expected_exit_hour_cet: heure CET de sortie prevue (None = fin de session)
            is_leveraged: True si la position utilise du levier (futures/FX)
            leverage: ratio de levier effectif
        """
        self.strategy = strategy
        self.market = market
        self.notional = notional
        self.margin_required = margin_required
        self.horizon = horizon
        self.entry_hour_cet = entry_hour_cet
        self.expected_exit_hour_cet = expected_exit_hour_cet
        self.is_leveraged = is_leveraged
        self.leverage = leverage

    def is_active_at(self, hour_cet: int) -> bool:
        """Verifie si la position est active a l'heure donnee.

        Les positions swing/monthly sont toujours actives.
        Les positions intraday sont actives entre entry et exit.
        """
        if self.horizon in ("swing", "monthly", "overnight"):
            return True

        entry = self.entry_hour_cet
        exit_h = self.expected_exit_hour_cet

        if exit_h is None:
            # Default exit times par marche
            defaults = {"eu": 17, "us": 22, "fx": 22, "futures": 22}
            exit_h = defaults.get(self.market, 22)

        if entry <= exit_h:
            return entry <= hour_cet < exit_h
        else:
            # Overnight wrap (ex: entry 22, exit 9)
            return hour_cet >= entry or hour_cet < exit_h

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "market": self.market,
            "notional": self.notional,
            "margin_required": self.margin_required,
            "horizon": self.horizon,
            "entry_hour_cet": self.entry_hour_cet,
            "expected_exit_hour_cet": self.expected_exit_hour_cet,
            "is_leveraged": self.is_leveraged,
            "leverage": self.leverage,
        }


class CapitalScheduler:
    """Gere le meme capital sur plusieurs horizons temporels.

    Le stacking temporel permet de reutiliser le capital libere par une
    session (EU matin) pour une autre session (US apres-midi), tout en
    respectant les contraintes de gross exposure a chaque instant.
    """

    def __init__(
        self,
        total_capital: float,
        max_gross_exposure: float = MAX_GROSS_EXPOSURE,
    ):
        """
        Args:
            total_capital: capital total du portefeuille (USD)
            max_gross_exposure: exposition brute maximale en ratio (defaut 0.90)
        """
        self.total_capital = total_capital
        self.max_gross_exposure = max_gross_exposure

    def calculate_available_capital(
        self,
        positions: List[Position],
        hour_cet: int,
    ) -> dict:
        """Capital disponible par creneau horaire.

        Calcule le capital libre en tenant compte de :
        1. Les positions actives a l'heure donnee
        2. La marge immobilisee par les positions leveragees
        3. La contrainte de gross exposure

        Args:
            positions: liste des positions ouvertes
            hour_cet: heure CET courante (0-23)

        Returns:
            {
                "hour_cet": int,
                "total_capital": float,
                "active_positions": int,
                "total_notional": float,
                "total_margin_used": float,
                "gross_exposure_ratio": float,
                "capital_free_margin": float,
                "capital_free_notional": float,
                "capital_available": float,
                "markets_active": {market: notional},
            }
        """
        active = [p for p in positions if p.is_active_at(hour_cet)]

        total_notional = sum(abs(p.notional) for p in active)
        total_margin = sum(p.margin_required for p in active)
        gross_ratio = total_notional / self.total_capital if self.total_capital > 0 else 0

        # Capital libre par la marge
        capital_free_margin = max(0, self.total_capital - total_margin)

        # Capital libre par la contrainte d'exposition brute
        max_notional = self.total_capital * self.max_gross_exposure
        notional_headroom = max(0, max_notional - total_notional)

        # Le capital disponible est le minimum des deux contraintes
        capital_available = min(capital_free_margin, notional_headroom)

        # Ventilation par marche
        markets_active: Dict[str, float] = {}
        for p in active:
            markets_active[p.market] = markets_active.get(p.market, 0) + abs(p.notional)

        result = {
            "hour_cet": hour_cet,
            "total_capital": self.total_capital,
            "active_positions": len(active),
            "total_notional": round(total_notional, 2),
            "total_margin_used": round(total_margin, 2),
            "gross_exposure_ratio": round(gross_ratio, 4),
            "capital_free_margin": round(capital_free_margin, 2),
            "capital_free_notional": round(notional_headroom, 2),
            "capital_available": round(capital_available, 2),
            "markets_active": {k: round(v, 2) for k, v in markets_active.items()},
        }

        logger.info(
            "Capital scheduler (h=%d): %d active positions, "
            "notional $%s (%.0f%%), available $%s",
            hour_cet, len(active),
            f"{total_notional:,.0f}", gross_ratio * 100,
            f"{capital_available:,.0f}",
        )

        return result

    def can_open_position(
        self,
        new_position: Position,
        existing_positions: List[Position],
        hour_cet: int,
    ) -> dict:
        """Verifie que la nouvelle position ne viole pas les limites.

        Checks :
        1. Gross exposure apres ajout < max_gross_exposure
        2. Marge suffisante (capital - margin_used > margin_new)
        3. Pas de double exposition excessive sur le meme marche

        Args:
            new_position: position a ouvrir
            existing_positions: positions deja ouvertes
            hour_cet: heure CET courante

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "gross_after": float,
                "margin_after": float,
                "headroom": float,
            }
        """
        available = self.calculate_available_capital(existing_positions, hour_cet)

        new_notional = abs(new_position.notional)
        new_margin = new_position.margin_required

        # Check 1: Gross exposure
        gross_after = (available["total_notional"] + new_notional) / self.total_capital
        if gross_after > self.max_gross_exposure:
            return {
                "allowed": False,
                "reason": (
                    f"Gross exposure {gross_after:.1%} > max {self.max_gross_exposure:.0%}. "
                    f"Headroom: ${available['capital_free_notional']:,.0f}"
                ),
                "gross_after": round(gross_after, 4),
                "margin_after": round(available["total_margin_used"] + new_margin, 2),
                "headroom": round(available["capital_available"], 2),
            }

        # Check 2: Marge suffisante
        margin_after = available["total_margin_used"] + new_margin
        if margin_after > self.total_capital:
            return {
                "allowed": False,
                "reason": (
                    f"Marge insuffisante: ${margin_after:,.0f} > "
                    f"capital ${self.total_capital:,.0f}"
                ),
                "gross_after": round(gross_after, 4),
                "margin_after": round(margin_after, 2),
                "headroom": round(available["capital_free_margin"], 2),
            }

        # Check 3: Exposition marche
        market_expo = available["markets_active"].get(new_position.market, 0)
        market_limit = self.total_capital * 0.50  # max 50% sur un seul marche
        if market_expo + new_notional > market_limit:
            return {
                "allowed": False,
                "reason": (
                    f"Market concentration [{new_position.market}]: "
                    f"${market_expo + new_notional:,.0f} > "
                    f"max ${market_limit:,.0f} (50%)"
                ),
                "gross_after": round(gross_after, 4),
                "margin_after": round(margin_after, 2),
                "headroom": round(market_limit - market_expo, 2),
            }

        logger.info(
            "Position allowed: %s on %s, notional $%s, gross %.1f%%",
            new_position.strategy, new_position.market,
            f"{new_notional:,.0f}", gross_after * 100,
        )

        return {
            "allowed": True,
            "reason": "OK",
            "gross_after": round(gross_after, 4),
            "margin_after": round(margin_after, 2),
            "headroom": round(available["capital_available"] - new_notional, 2),
        }

    def simulate_daily_schedule(
        self,
        positions: List[Position],
    ) -> Dict[int, dict]:
        """Simule l'etat du capital heure par heure sur 24h.

        Utile pour visualiser le stacking temporel et identifier
        les pics d'exposition.

        Args:
            positions: toutes les positions prevues dans la journee

        Returns:
            {hour: calculate_available_capital_result} pour chaque heure 0-23
        """
        schedule = {}
        for hour in range(24):
            schedule[hour] = self.calculate_available_capital(positions, hour)
        return schedule

    def get_stacking_efficiency(
        self,
        positions: List[Position],
    ) -> dict:
        """Calcule l'efficacite du stacking temporel.

        Compare le capital total utilise (somme des notionnels de toutes
        les positions, sans chevauchement temporel) au capital reel.

        Un ratio > 1 signifie que le meme dollar travaille plusieurs fois.

        Returns:
            {
                "total_notional_all": float,
                "peak_notional": float,
                "peak_hour": int,
                "stacking_ratio": float,
                "capital_utilization_avg": float,
            }
        """
        total_notional_all = sum(abs(p.notional) for p in positions)

        peak_notional = 0
        peak_hour = 0
        utilizations = []

        for hour in range(24):
            active = [p for p in positions if p.is_active_at(hour)]
            notional = sum(abs(p.notional) for p in active)
            utilizations.append(notional / self.total_capital if self.total_capital > 0 else 0)
            if notional > peak_notional:
                peak_notional = notional
                peak_hour = hour

        avg_utilization = sum(utilizations) / 24 if utilizations else 0

        stacking_ratio = (
            total_notional_all / self.total_capital
            if self.total_capital > 0 else 0
        )

        return {
            "total_notional_all": round(total_notional_all, 2),
            "peak_notional": round(peak_notional, 2),
            "peak_hour": peak_hour,
            "stacking_ratio": round(stacking_ratio, 2),
            "capital_utilization_avg": round(avg_utilization, 4),
        }
