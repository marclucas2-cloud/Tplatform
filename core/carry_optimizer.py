"""
ROC-004 Carry FX Swap Optimization — Optimisation du rendement FX via swaps positifs.

Pour les paires FX avec un swap long positif :
  - Signal LONG  -> trader normalement
  - Signal SHORT -> trader normalement
  - Signal NEUTRAL -> maintenir une micro-position long (5% du sizing normal)
    pour capturer le swap positif

Paires eligibles (swap long positif, mars 2026) :
  - EUR/JPY : ~$0.80/lot/jour
  - AUD/JPY : ~$0.60/lot/jour

Contrainte : la micro-position ne doit pas depasser 2% du capital.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Swaps long positifs par defaut ($/lot/jour, mars 2026)
DEFAULT_CARRY_PAIRS: dict[str, dict[str, float]] = {
    "EUR/JPY": {"swap_per_lot_day": 0.80, "lot_size": 25_000},
    "AUD/JPY": {"swap_per_lot_day": 0.60, "lot_size": 25_000},
}

# Fraction du sizing normal pour la micro-position carry
CARRY_FRACTION = 0.05  # 5% du sizing normal
# Cap maximum de la micro-position en % du capital
MAX_CARRY_PCT = 0.02   # 2% du capital


class CarryOptimizer:
    """Optimise le rendement FX en maintenant des micro-positions carry.

    Quand le signal est NEUTRAL sur une paire avec swap long positif,
    on maintient une micro-position long pour capturer le swap.
    """

    def __init__(
        self,
        capital: float,
        carry_pairs: dict[str, dict[str, float]] | None = None,
        carry_fraction: float = CARRY_FRACTION,
        max_carry_pct: float = MAX_CARRY_PCT,
    ):
        self._capital = capital
        self._carry_pairs = carry_pairs if carry_pairs is not None else dict(DEFAULT_CARRY_PAIRS)
        self._carry_fraction = carry_fraction
        self._max_carry_pct = max_carry_pct

        # Tracking des positions carry actives
        self._active_carries: dict[str, dict[str, Any]] = {}
        # Historique des gains carry estimes
        self._total_carry_earned: float = 0.0
        self._total_carry_days: int = 0

    # ------------------------------------------------------------------
    # Methodes publiques
    # ------------------------------------------------------------------

    def should_hold_carry(self, symbol: str, current_signal: Any) -> bool:
        """Determine si on doit maintenir une micro-position carry.

        Args:
            symbol: paire FX (e.g. "EUR/JPY")
            current_signal: signal actuel — NEUTRAL/None/0/"flat" = pas de signal actif

        Returns:
            True si on doit maintenir une micro-position carry long.
        """
        # Paire non eligible pour le carry
        if symbol not in self._carry_pairs:
            return False

        # Si le signal est actif (LONG ou SHORT), pas de carry — on trade normalement
        if self._is_signal_active(current_signal):
            return False

        # Signal NEUTRAL -> maintenir la micro-position carry
        logger.debug(
            f"CarryOptimizer: signal NEUTRAL sur {symbol}, "
            f"micro-position carry recommandee"
        )
        return True

    def get_carry_size(self, symbol: str) -> float:
        """Calcule la taille de la micro-position carry (en notional USD).

        La taille est le minimum entre :
          - carry_fraction (5%) du lot normal de la paire
          - max_carry_pct (2%) du capital total

        Args:
            symbol: paire FX

        Returns:
            Taille en USD (notional). 0 si la paire n'est pas eligible.
        """
        if symbol not in self._carry_pairs:
            return 0.0

        pair_info = self._carry_pairs[symbol]
        lot_size = pair_info.get("lot_size", 25_000)

        # Taille basee sur la fraction du lot normal
        fraction_size = lot_size * self._carry_fraction

        # Cap base sur le capital
        capital_cap = self._capital * self._max_carry_pct

        carry_size = min(fraction_size, capital_cap)

        logger.debug(
            f"CarryOptimizer: {symbol} carry_size=${carry_size:.2f} "
            f"(fraction=${fraction_size:.2f}, cap=${capital_cap:.2f})"
        )

        return round(carry_size, 2)

    def get_daily_carry_estimate(self) -> float:
        """Estime le revenu carry journalier total sur toutes les paires.

        Calcule pour chaque paire eligible :
          carry_size / lot_size * swap_per_lot_day

        Returns:
            Estimation du revenu carry quotidien en USD.
        """
        total_daily = 0.0

        for symbol, pair_info in self._carry_pairs.items():
            carry_size = self.get_carry_size(symbol)
            if carry_size <= 0:
                continue

            lot_size = pair_info.get("lot_size", 25_000)
            swap_per_lot_day = pair_info.get("swap_per_lot_day", 0)

            # Proportionnel a la taille de la position
            daily_swap = (carry_size / lot_size) * swap_per_lot_day
            total_daily += daily_swap

        return round(total_daily, 4)

    def get_carry_stats(self) -> dict:
        """Retourne les statistiques completes du carry optimizer.

        Returns:
            dict avec pairs, total_earned, days_held, daily_estimate, etc.
        """
        daily_estimate = self.get_daily_carry_estimate()

        pairs_detail = {}
        for symbol, pair_info in self._carry_pairs.items():
            carry_size = self.get_carry_size(symbol)
            lot_size = pair_info.get("lot_size", 25_000)
            swap_per_lot_day = pair_info.get("swap_per_lot_day", 0)
            daily_swap = (carry_size / lot_size) * swap_per_lot_day if lot_size > 0 else 0

            active = self._active_carries.get(symbol, {})
            pairs_detail[symbol] = {
                "carry_size": carry_size,
                "swap_per_lot_day": swap_per_lot_day,
                "daily_swap": round(daily_swap, 4),
                "is_active": symbol in self._active_carries,
                "days_held": active.get("days_held", 0),
                "earned": round(active.get("earned", 0), 4),
            }

        return {
            "pairs": pairs_detail,
            "n_carry_pairs": len(self._carry_pairs),
            "daily_estimate": daily_estimate,
            "monthly_estimate": round(daily_estimate * 30, 2),
            "annual_estimate": round(daily_estimate * 365, 2),
            "total_earned": round(self._total_carry_earned, 4),
            "total_days_held": self._total_carry_days,
            "capital": self._capital,
            "carry_fraction": self._carry_fraction,
            "max_carry_pct": self._max_carry_pct,
        }

    def update_capital(self, new_capital: float) -> None:
        """Met a jour le capital (utile apres rebalancing).

        Args:
            new_capital: nouveau capital total
        """
        self._capital = new_capital
        logger.debug(f"CarryOptimizer: capital mis a jour a ${new_capital:.2f}")

    def record_carry_day(self, symbol: str) -> dict:
        """Enregistre un jour de carry pour une paire (appele par le scheduler).

        Args:
            symbol: paire FX

        Returns:
            dict avec le gain du jour
        """
        if symbol not in self._carry_pairs:
            return {"symbol": symbol, "earned": 0, "error": "not_carry_pair"}

        pair_info = self._carry_pairs[symbol]
        carry_size = self.get_carry_size(symbol)
        lot_size = pair_info.get("lot_size", 25_000)
        swap_per_lot_day = pair_info.get("swap_per_lot_day", 0)
        daily_earned = (carry_size / lot_size) * swap_per_lot_day if lot_size > 0 else 0

        # Mettre a jour le tracking
        if symbol not in self._active_carries:
            self._active_carries[symbol] = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "days_held": 0,
                "earned": 0.0,
            }

        self._active_carries[symbol]["days_held"] += 1
        self._active_carries[symbol]["earned"] += daily_earned
        self._total_carry_earned += daily_earned
        self._total_carry_days += 1

        logger.info(
            f"CarryOptimizer: {symbol} jour {self._active_carries[symbol]['days_held']} "
            f"— +${daily_earned:.4f} (total ${self._active_carries[symbol]['earned']:.4f})"
        )

        return {
            "symbol": symbol,
            "earned": round(daily_earned, 4),
            "total_earned": round(self._active_carries[symbol]["earned"], 4),
            "days_held": self._active_carries[symbol]["days_held"],
        }

    # ------------------------------------------------------------------
    # Methodes internes
    # ------------------------------------------------------------------

    @staticmethod
    def _is_signal_active(signal: Any) -> bool:
        """Determine si un signal est actif (LONG ou SHORT) ou neutre.

        Signaux consideres comme NEUTRAL :
          - None, 0, "neutral", "flat", "", "NEUTRAL", "FLAT"

        Tout le reste est considere comme actif.
        """
        if signal is None:
            return False
        if isinstance(signal, (int, float)) and signal == 0:
            return False
        if isinstance(signal, str) and signal.upper() in ("", "NEUTRAL", "FLAT", "NONE"):
            return False
        return True
