"""
OPT-B4 : Signal Confluence Amplifier.

Detecte quand 2+ strategies signalent le meme ticker dans la meme direction.
- Confluence 2 strategies → taille x1.5
- Confluence 3+ strategies → taille x2.0
- Directions opposees → CONFLICT (skip le ticker)
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class ConfluenceDetector:
    """Detecte quand 2+ strategies signalent le meme ticker."""

    # Multiplicateurs de taille par niveau de confluence
    SIZE_MULTIPLIERS = {
        1: 1.0,   # Solo — pas de boost
        2: 1.5,   # 2 strategies concordantes
    }
    # 3+ strategies → multiplicateur max
    MAX_MULTIPLIER = 2.0

    def detect(self, signals: list) -> dict:
        """Analyse les signaux et detecte les confluences.

        Args:
            signals: liste de dicts avec les cles :
                - ticker:    symbole (ex: 'AAPL')
                - direction: 'BUY' ou 'SELL'
                - strategy:  nom de la strategie emettrice
                - strength:  force du signal (0.0 a 1.0, optionnel)

        Returns:
            {ticker: {
                direction:        'BUY' | 'SELL' | 'CONFLICT',
                strategies:       [liste des strategies],
                confluence_level: int (1 = solo, 2+ = confluence),
                size_multiplier:  float (1.0 solo, 1.5 confluence 2, 2.0 confluence 3+),
                avg_strength:     float (moyenne des strengths),
            }}
        """
        if not signals:
            return {}

        # Grouper par ticker
        by_ticker = defaultdict(list)
        for sig in signals:
            ticker = sig.get("ticker")
            if not ticker:
                continue
            by_ticker[ticker].append(sig)

        result = {}

        for ticker, sigs in by_ticker.items():
            directions = set(s.get("direction") for s in sigs)
            strategies = [s.get("strategy", "unknown") for s in sigs]
            strengths = [s.get("strength", 1.0) for s in sigs]
            avg_strength = sum(strengths) / len(strengths) if strengths else 0.0

            # Detecter les conflits (BUY et SELL sur le meme ticker)
            has_buy = "BUY" in directions
            has_sell = "SELL" in directions

            if has_buy and has_sell:
                result[ticker] = {
                    "direction": "CONFLICT",
                    "strategies": strategies,
                    "confluence_level": len(sigs),
                    "size_multiplier": 0.0,
                    "avg_strength": avg_strength,
                }
                logger.warning(
                    "CONFLICT on %s: %d strategies disagree (%s)",
                    ticker,
                    len(sigs),
                    strategies,
                )
                continue

            # Direction unique
            direction = "BUY" if has_buy else "SELL"
            level = len(sigs)
            multiplier = self._get_multiplier(level)

            result[ticker] = {
                "direction": direction,
                "strategies": strategies,
                "confluence_level": level,
                "size_multiplier": multiplier,
                "avg_strength": round(avg_strength, 4),
            }

            if level >= 2:
                logger.info(
                    "CONFLUENCE %s on %s: %d strategies (%s), multiplier=%.1fx",
                    direction,
                    ticker,
                    level,
                    strategies,
                    multiplier,
                )

        return result

    def _get_multiplier(self, confluence_level: int) -> float:
        """Retourne le multiplicateur de taille pour un niveau de confluence.

        Args:
            confluence_level: nombre de strategies concordantes

        Returns:
            multiplicateur (1.0, 1.5, ou 2.0)
        """
        if confluence_level >= 3:
            return self.MAX_MULTIPLIER
        return self.SIZE_MULTIPLIERS.get(confluence_level, 1.0)

    def filter_actionable(self, confluence_result: dict) -> dict:
        """Filtre les resultats pour ne garder que les tickers actionnables.

        Exclut les CONFLICT et retourne seulement les tickers avec
        une direction claire.

        Args:
            confluence_result: resultat de detect()

        Returns:
            sous-dict sans les CONFLICT
        """
        return {
            ticker: info
            for ticker, info in confluence_result.items()
            if info["direction"] != "CONFLICT"
        }
