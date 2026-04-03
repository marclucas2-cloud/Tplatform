"""
ROC-007 — Sniper Entries pour strategies Mean Reversion.

Au lieu d'un ordre market immediat, place un ordre limit avec un offset
calcule selon la volatilite recente du symbole. Si l'ordre n'est pas
rempli apres un timeout, il est converti en ordre market (le signal
reste valide).

Resultat attendu : amelioration moyenne de 2-5 bps par trade MR.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# --- Configuration des offsets par symbole ---
OFFSETS = {
    "BTCUSDT": {"offset_pct": 0.0005, "timeout_seconds": 300},
    "EURGBP": {"offset_pips": 2, "timeout_seconds": 300},
    "EURUSD": {"offset_pips": 1.5, "timeout_seconds": 300},
    "EURJPY": {"offset_pips": 3, "timeout_seconds": 300},
    "AUDJPY": {"offset_pips": 2, "timeout_seconds": 300},
}

# Strategies eligibles au sniper entry (mean reversion seulement)
MR_STRATEGIES = [
    "eurgbp_mr",
    "btc_mean_reversion",
    "btc_mr",
    "mean_reversion",
]

# Taille d'un pip par famille de paire
PIP_SIZE = {
    "default": 0.0001,  # paires XXX/USD, EUR/GBP, etc.
    "JPY": 0.01,        # paires XXX/JPY
}


class SniperEntry:
    """Gestion des entrees sniper pour ameliorer le fill price des strats MR.

    Place un ordre limit avec offset au lieu d'un market order.
    Convertit en market apres timeout si non rempli.
    """

    def __init__(
        self,
        offsets: Dict | None = None,
        default_timeout_seconds: int = 300,
    ):
        """
        Args:
            offsets: config custom {symbol: {offset_pct ou offset_pips, timeout_seconds}}
            default_timeout_seconds: timeout par defaut avant fallback market
        """
        self.offsets = offsets if offsets is not None else dict(OFFSETS)
        self.default_timeout_seconds = default_timeout_seconds

        # Statistiques de performance
        self._stats = {
            "total_attempts": 0,
            "limit_fills": 0,
            "market_fallbacks": 0,
            "total_improvement_bps": 0.0,
        }

        logger.info(
            f"SniperEntry initialise — "
            f"{len(self.offsets)} symboles configures, "
            f"timeout par defaut={default_timeout_seconds}s"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_sniper_order(
        self,
        symbol: str,
        side: str,
        current_price: float,
        quantity: float,
        strategy_name: str = "",
    ) -> dict:
        """Cree un ordre sniper (limit avec offset) ou market si non eligible.

        Args:
            symbol: symbole de l'instrument
            side: "BUY" ou "SELL"
            current_price: prix actuel du marche
            quantity: quantite a executer
            strategy_name: nom de la strategie (pour verifier eligibilite MR)

        Returns:
            {
                order_type: "LIMIT" ou "MARKET",
                price: float (limit price, ou current_price pour market),
                quantity: float,
                timeout: int (seconds, 0 pour market),
                fallback: "MARKET" ou None,
                symbol: str,
                side: str,
                offset_applied: float,
            }
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Side invalide: {side}. Doit etre BUY ou SELL.")

        # Si la strategie n'est pas MR ou le symbole n'a pas de config → market
        if not self.is_mean_reversion_strategy(strategy_name):
            return self._market_order(symbol, side, current_price, quantity)

        if symbol not in self.offsets:
            logger.info(
                f"Symbole {symbol} non configure pour sniper — fallback market"
            )
            return self._market_order(symbol, side, current_price, quantity)

        # Calculer l'offset
        offset = self.get_offset(symbol, current_price)
        timeout = self.offsets[symbol].get(
            "timeout_seconds", self.default_timeout_seconds
        )

        # Appliquer l'offset selon le side
        if side == "BUY":
            # Acheter en dessous du prix actuel
            limit_price = round(current_price - offset, 6)
        else:
            # Vendre au dessus du prix actuel
            limit_price = round(current_price + offset, 6)

        self._stats["total_attempts"] += 1

        logger.info(
            f"SNIPER ORDER: {side} {quantity} {symbol} @ {limit_price} "
            f"(offset={offset:.6f}, timeout={timeout}s)"
        )

        return {
            "order_type": "LIMIT",
            "price": limit_price,
            "quantity": quantity,
            "timeout": timeout,
            "fallback": "MARKET",
            "symbol": symbol,
            "side": side,
            "offset_applied": offset,
        }

    def is_mean_reversion_strategy(self, strategy_name: str) -> bool:
        """Verifie si une strategie est eligible au sniper entry.

        Args:
            strategy_name: nom de la strategie

        Returns:
            True si la strategie est de type mean reversion
        """
        if not strategy_name:
            return False
        return strategy_name.lower() in [s.lower() for s in MR_STRATEGIES]

    def get_offset(self, symbol: str, current_price: float) -> float:
        """Calcule l'offset absolu en unites de prix pour un symbole.

        Args:
            symbol: symbole de l'instrument
            current_price: prix actuel

        Returns:
            offset en unites de prix (float)
        """
        if symbol not in self.offsets:
            return 0.0

        config = self.offsets[symbol]

        # Offset en pourcentage (crypto)
        if "offset_pct" in config:
            return current_price * config["offset_pct"]

        # Offset en pips (FX)
        if "offset_pips" in config:
            pips = config["offset_pips"]
            pip_size = self._get_pip_size(symbol)
            return pips * pip_size

        return 0.0

    def record_fill(self, was_limit_fill: bool, improvement_bps: float = 0.0):
        """Enregistre le resultat d'un fill pour les statistiques.

        Args:
            was_limit_fill: True si rempli en limit, False si fallback market
            improvement_bps: amelioration en bps par rapport au market
        """
        if was_limit_fill:
            self._stats["limit_fills"] += 1
            self._stats["total_improvement_bps"] += improvement_bps
        else:
            self._stats["market_fallbacks"] += 1

    def get_sniper_stats(self) -> dict:
        """Statistiques de performance du sniper entry.

        Returns:
            {
                total_attempts: int,
                limit_fills: int,
                market_fallbacks: int,
                fill_rate: float (0.0-1.0),
                avg_improvement_bps: float,
                timeouts: int,
            }
        """
        total = self._stats["total_attempts"]
        fills = self._stats["limit_fills"]
        fallbacks = self._stats["market_fallbacks"]

        fill_rate = fills / total if total > 0 else 0.0
        avg_improvement = (
            self._stats["total_improvement_bps"] / fills if fills > 0 else 0.0
        )

        return {
            "total_attempts": total,
            "limit_fills": fills,
            "market_fallbacks": fallbacks,
            "fill_rate": round(fill_rate, 4),
            "avg_improvement_bps": round(avg_improvement, 2),
            "timeouts": fallbacks,  # timeout = fallback market
        }

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _market_order(
        self, symbol: str, side: str, current_price: float, quantity: float
    ) -> dict:
        """Construit un ordre market standard (pas de sniper)."""
        return {
            "order_type": "MARKET",
            "price": current_price,
            "quantity": quantity,
            "timeout": 0,
            "fallback": None,
            "symbol": symbol,
            "side": side,
            "offset_applied": 0.0,
        }

    def _get_pip_size(self, symbol: str) -> float:
        """Determine la taille d'un pip selon le symbole."""
        # Paires JPY : 0.01 par pip
        if "JPY" in symbol.upper():
            return PIP_SIZE["JPY"]
        return PIP_SIZE["default"]
