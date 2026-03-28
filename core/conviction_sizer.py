"""
ConvictionSizer — ROC-002 Modulation du sizing basee sur la conviction du signal.

S'insere entre le generateur de signal et l'executeur d'ordres.
Le score de conviction (0-1) module le sizing de base :
  conviction >= 0.8 -> 1.5x base (STRONG)
  conviction 0.5-0.8 -> 1.0x (NORMAL)
  conviction 0.3-0.5 -> 0.7x (WEAK)
  conviction < 0.3 -> SKIP (pas de trade)

Contraintes :
  - Le sizing max ne depasse jamais la fraction Kelly de la phase suivante
  - Les limites du risk manager restent respectees
  - Chaque trade logge son score de conviction
"""

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Niveaux de conviction par defaut
DEFAULT_MULTIPLIERS = {
    "STRONG": {"min_score": 0.8, "multiplier": 1.5},
    "NORMAL": {"min_score": 0.5, "multiplier": 1.0},
    "WEAK": {"min_score": 0.3, "multiplier": 0.7},
    "SKIP": {"min_score": 0.0, "multiplier": 0.0},
}

# Poids par defaut pour le calcul du score de conviction
DEFAULT_CONVICTION_WEIGHTS = {
    "adx_strength": 0.25,
    "volume_confirmation": 0.20,
    "multi_timeframe_alignment": 0.20,
    "regime_alignment": 0.20,
    "historical_edge": 0.15,
}


class ConvictionSizer:
    """Modulate position sizing based on signal conviction strength.

    Inserted between signal generator and order executor.
    Ensures max sizing never exceeds next phase's Kelly fraction.
    """

    def __init__(
        self,
        base_kelly_fraction: float = 0.125,
        max_kelly_fraction: float = 0.25,
        multipliers: Optional[Dict] = None,
    ):
        """
        Args:
            base_kelly_fraction: fraction Kelly de base (ex: 1/8 en SOFT_LAUNCH)
            max_kelly_fraction: fraction Kelly max (phase suivante, ex: 1/4)
            multipliers: dict override {level: {min_score, multiplier}}
        """
        self.base_kelly_fraction = base_kelly_fraction
        self.max_kelly_fraction = max_kelly_fraction
        self.multipliers = multipliers or dict(DEFAULT_MULTIPLIERS)

        # Historique des convictions pour stats
        self._conviction_log: List[Dict] = []

        logger.info(
            "ConvictionSizer initialise — base_kelly=%.4f, max_kelly=%.4f",
            self.base_kelly_fraction,
            self.max_kelly_fraction,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_conviction_level(self, score: float) -> str:
        """Determine le niveau de conviction a partir du score.

        Args:
            score: valeur entre 0 et 1

        Returns:
            "STRONG", "NORMAL", "WEAK" ou "SKIP"
        """
        # Scores negatifs ou invalides -> SKIP
        if score is None or not isinstance(score, (int, float)) or score < 0:
            return "SKIP"

        # Clamp a 1.0
        score = min(score, 1.0)

        # Parcours du plus fort au plus faible
        for level in ["STRONG", "NORMAL", "WEAK"]:
            if score >= self.multipliers[level]["min_score"]:
                return level

        return "SKIP"

    def calculate_size(
        self, conviction_score: float, capital: float, price: float
    ) -> dict:
        """Calcule la taille de position ajustee par la conviction.

        Args:
            conviction_score: score de conviction entre 0 et 1
            capital: capital total disponible
            price: prix unitaire de l'actif

        Returns:
            {
                shares: int,
                kelly_used: float,
                level: str,
                skip: bool,
                base_notional: float,
                adjusted_notional: float,
            }
        """
        # Protection prix zero ou negatif
        if price is None or price <= 0:
            logger.warning("Prix invalide (%.4f) — skip", price if price else 0)
            return {
                "shares": 0,
                "kelly_used": 0.0,
                "level": "SKIP",
                "skip": True,
                "base_notional": 0.0,
                "adjusted_notional": 0.0,
            }

        # Protection capital negatif ou zero
        if capital is None or capital <= 0:
            logger.warning("Capital invalide — skip")
            return {
                "shares": 0,
                "kelly_used": 0.0,
                "level": "SKIP",
                "skip": True,
                "base_notional": 0.0,
                "adjusted_notional": 0.0,
            }

        level = self.get_conviction_level(conviction_score)
        skip = level == "SKIP"

        if skip:
            return {
                "shares": 0,
                "kelly_used": 0.0,
                "level": "SKIP",
                "skip": True,
                "base_notional": 0.0,
                "adjusted_notional": 0.0,
            }

        multiplier = self.multipliers[level]["multiplier"]

        # Sizing de base = capital * kelly_fraction
        base_notional = capital * self.base_kelly_fraction

        # Sizing ajuste par la conviction
        adjusted_kelly = self.base_kelly_fraction * multiplier

        # Cap au max_kelly_fraction (ne jamais depasser la phase suivante)
        capped_kelly = min(adjusted_kelly, self.max_kelly_fraction)
        adjusted_notional = capital * capped_kelly

        # Conversion en nombre d'actions (arrondi vers le bas)
        shares = int(math.floor(adjusted_notional / price))

        logger.debug(
            "ConvictionSizer: score=%.2f level=%s multiplier=%.1fx "
            "kelly=%.4f->%.4f notional=$%.2f shares=%d",
            conviction_score,
            level,
            multiplier,
            self.base_kelly_fraction,
            capped_kelly,
            adjusted_notional,
            shares,
        )

        return {
            "shares": shares,
            "kelly_used": capped_kelly,
            "level": level,
            "skip": False,
            "base_notional": base_notional,
            "adjusted_notional": adjusted_notional,
        }

    def log_conviction(
        self,
        trade_id: str,
        score: float,
        level: str,
        base_size: float,
        adjusted_size: float,
    ):
        """Enregistre le score de conviction pour un trade dans l'historique.

        Args:
            trade_id: identifiant unique du trade
            score: score de conviction (0-1)
            level: niveau (STRONG/NORMAL/WEAK/SKIP)
            base_size: taille de base avant ajustement
            adjusted_size: taille apres ajustement
        """
        entry = {
            "trade_id": trade_id,
            "score": score,
            "level": level,
            "base_size": base_size,
            "adjusted_size": adjusted_size,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pnl": None,  # A remplir apres cloture du trade
            "won": None,
        }
        self._conviction_log.append(entry)
        logger.info(
            "Conviction logged: trade=%s score=%.2f level=%s "
            "base=%.2f adjusted=%.2f",
            trade_id,
            score,
            level,
            base_size,
            adjusted_size,
        )

    def update_trade_result(self, trade_id: str, pnl: float):
        """Met a jour le PnL d'un trade dans le log de conviction.

        Args:
            trade_id: identifiant du trade
            pnl: profit/perte realise
        """
        for entry in self._conviction_log:
            if entry["trade_id"] == trade_id:
                entry["pnl"] = pnl
                entry["won"] = pnl > 0
                return
        logger.warning("Trade %s introuvable dans le log de conviction", trade_id)

    def get_conviction_stats(self) -> dict:
        """Statistiques par niveau de conviction.

        Returns:
            {
                "STRONG": {count, avg_pnl, win_rate, total_pnl},
                "NORMAL": {...},
                "WEAK": {...},
                "SKIP": {...},
                "total_trades": int,
            }
        """
        stats = {}
        for level in ["STRONG", "NORMAL", "WEAK", "SKIP"]:
            entries = [e for e in self._conviction_log if e["level"] == level]
            closed = [e for e in entries if e["pnl"] is not None]

            count = len(entries)
            if closed:
                total_pnl = sum(e["pnl"] for e in closed)
                avg_pnl = total_pnl / len(closed)
                wins = sum(1 for e in closed if e["won"])
                win_rate = wins / len(closed)
            else:
                total_pnl = 0.0
                avg_pnl = 0.0
                win_rate = 0.0

            stats[level] = {
                "count": count,
                "closed": len(closed),
                "avg_pnl": round(avg_pnl, 2),
                "win_rate": round(win_rate, 4),
                "total_pnl": round(total_pnl, 2),
            }

        stats["total_trades"] = len(self._conviction_log)
        return stats

    # ------------------------------------------------------------------
    # Score de conviction
    # ------------------------------------------------------------------

    @staticmethod
    def compute_conviction_score(
        adx_strength: float = 0.0,
        volume_confirmation: float = 0.0,
        multi_timeframe_alignment: float = 0.0,
        regime_alignment: float = 0.0,
        historical_edge: float = 0.0,
        weights: Optional[Dict[str, float]] = None,
    ) -> float:
        """Calcule le score de conviction a partir de composantes individuelles.

        Chaque composante est une valeur entre 0 et 1.
        Le score final est la somme ponderee, clampee entre 0 et 1.

        Poids par defaut :
          - adx_strength: 0.25
          - volume_confirmation: 0.20
          - multi_timeframe_alignment: 0.20
          - regime_alignment: 0.20
          - historical_edge: 0.15

        Args:
            adx_strength: force de la tendance ADX (0-1)
            volume_confirmation: confirmation par le volume (0-1)
            multi_timeframe_alignment: alignement multi-timeframe (0-1)
            regime_alignment: alignement avec le regime de marche (0-1)
            historical_edge: edge historique de la strategie (0-1)
            weights: dict de poids personnalises (optionnel)

        Returns:
            Score de conviction entre 0 et 1
        """
        w = weights or dict(DEFAULT_CONVICTION_WEIGHTS)

        # Clamp chaque composante entre 0 et 1
        components = {
            "adx_strength": max(0.0, min(1.0, adx_strength)),
            "volume_confirmation": max(0.0, min(1.0, volume_confirmation)),
            "multi_timeframe_alignment": max(0.0, min(1.0, multi_timeframe_alignment)),
            "regime_alignment": max(0.0, min(1.0, regime_alignment)),
            "historical_edge": max(0.0, min(1.0, historical_edge)),
        }

        score = sum(components[k] * w.get(k, 0.0) for k in components)

        # Clamp le score final entre 0 et 1
        score = max(0.0, min(1.0, score))

        return round(score, 4)
