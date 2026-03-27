"""
OPT-B4 : Signal Confluence Amplifier.

Detecte quand 2+ strategies signalent le meme ticker dans la meme direction.
- Confluence 2 strategies → taille x1.5
- Confluence 3+ strategies → taille x2.0
- Directions opposees → CONFLICT (skip le ticker)

OPT-004 : Cross-Asset Confluence (P2).
Detecte la convergence/divergence entre marches (US equity, EU equity, FX, futures).
- Convergence cross-asset → multiplicateur 1.1-1.3
- Conflit cross-asset → multiplicateur 0.7 (reduction)
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

    # ------------------------------------------------------------------
    # OPT-004 : Regles de confluence cross-asset
    # ------------------------------------------------------------------
    # (signal1_market, signal1_dir, signal2_market, signal2_dir): multiplier
    CROSS_ASSET_RULES = {
        ("us_equity", "SHORT", "fx", "risk_off"): 1.3,
        ("us_equity", "LONG", "futures_index", "LONG"): 1.2,
        ("us_equity", "LONG", "futures_index", "SHORT"): 0.7,
        ("futures_metals", "LONG", "us_equity", "SHORT"): 1.2,
        ("fx", "carry_long", "us_equity", "LONG"): 1.1,
        ("eu_equity", "LONG", "us_equity", "LONG"): 1.1,
        ("eu_equity", "SHORT", "us_equity", "SHORT"): 1.2,
    }

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

    # ------------------------------------------------------------------
    # OPT-004 : Cross-Asset Confluence Detection
    # ------------------------------------------------------------------

    def detect_cross_asset(self, signals: list) -> dict:
        """Detecte la confluence cross-asset entre marches differents.

        Analyse les signaux provenant de plusieurs marches (US equity, EU equity,
        FX, futures) et detecte les convergences ou conflits entre eux.
        Le multiplicateur cross-asset s'applique EN PLUS du multiplicateur intra-ticker.

        Args:
            signals: liste de dicts avec les cles :
                - ticker:      symbole (ex: 'SPY', 'EUR/USD', 'MES')
                - direction:   direction du signal (ex: 'LONG', 'SHORT', 'risk_off', 'carry_long')
                - strategy:    nom de la strategie emettrice
                - strength:    force du signal (0.0 a 1.0, optionnel)
                - market:      marche source parmi:
                               'us_equity', 'eu_equity', 'fx',
                               'futures_index', 'futures_energy', 'futures_metals'
                - asset_class: classe d'actif (optionnel, informatif)

        Returns:
            {ticker: {
                direction:               'LONG' | 'SHORT' | 'CONFLICT',
                strategies:              [liste des strategies],
                confluence_level:        int (nombre de strategies sur ce ticker),
                cross_asset_multiplier:  float (produit des multiplicateurs cross-asset),
                conflicts:               [liste de descriptions des conflits],
            }}
        """
        if not signals:
            return {}

        # 1. Grouper par ticker (meme logique que detect())
        by_ticker = defaultdict(list)
        for sig in signals:
            ticker = sig.get("ticker")
            if not ticker:
                continue
            by_ticker[ticker].append(sig)

        # 2. Indexer tous les signaux par market pour le cross-matching
        by_market = defaultdict(list)
        for sig in signals:
            market = sig.get("market")
            if market:
                by_market[market].append(sig)

        result = {}

        for ticker, sigs in by_ticker.items():
            directions = set(s.get("direction") for s in sigs)
            strategies = [s.get("strategy", "unknown") for s in sigs]

            # Detecter les conflits intra-ticker (meme logique)
            has_long = "LONG" in directions
            has_short = "SHORT" in directions

            if has_long and has_short:
                result[ticker] = {
                    "direction": "CONFLICT",
                    "strategies": strategies,
                    "confluence_level": len(sigs),
                    "cross_asset_multiplier": 0.0,
                    "conflicts": [f"Intra-ticker conflict: LONG and SHORT on {ticker}"],
                }
                logger.warning(
                    "CROSS-ASSET CONFLICT on %s: intra-ticker LONG vs SHORT (%s)",
                    ticker, strategies,
                )
                continue

            direction = "LONG" if has_long else "SHORT" if has_short else sigs[0].get("direction", "LONG")
            ticker_market = sigs[0].get("market", "us_equity")

            # 3. Scanner les regles cross-asset
            cross_multiplier = 1.0
            conflicts = []
            matched_rules = []

            for rule_key, rule_mult in self.CROSS_ASSET_RULES.items():
                mkt1, dir1, mkt2, dir2 = rule_key

                # Cas A: le ticker est dans market1, on cherche des signaux dans market2
                if ticker_market == mkt1 and direction == dir1:
                    other_signals = by_market.get(mkt2, [])
                    for other in other_signals:
                        if other.get("ticker") == ticker:
                            continue  # Pas de self-matching
                        other_dir = other.get("direction", "")
                        if other_dir == dir2:
                            if rule_mult < 1.0:
                                conflicts.append(
                                    f"CONFLICT: {ticker} {direction} ({mkt1}) "
                                    f"vs {other['ticker']} {other_dir} ({mkt2}) "
                                    f"→ mult {rule_mult}"
                                )
                            else:
                                matched_rules.append(
                                    f"CONVERGENCE: {ticker} {direction} ({mkt1}) "
                                    f"+ {other['ticker']} {other_dir} ({mkt2}) "
                                    f"→ mult {rule_mult}"
                                )
                            cross_multiplier *= rule_mult

                # Cas B: le ticker est dans market2, on cherche des signaux dans market1
                if ticker_market == mkt2 and direction == dir2:
                    other_signals = by_market.get(mkt1, [])
                    for other in other_signals:
                        if other.get("ticker") == ticker:
                            continue
                        other_dir = other.get("direction", "")
                        if other_dir == dir1:
                            if rule_mult < 1.0:
                                conflicts.append(
                                    f"CONFLICT: {other['ticker']} {other_dir} ({mkt1}) "
                                    f"vs {ticker} {direction} ({mkt2}) "
                                    f"→ mult {rule_mult}"
                                )
                            else:
                                matched_rules.append(
                                    f"CONVERGENCE: {other['ticker']} {other_dir} ({mkt1}) "
                                    f"+ {ticker} {direction} ({mkt2}) "
                                    f"→ mult {rule_mult}"
                                )
                            cross_multiplier *= rule_mult

            result[ticker] = {
                "direction": direction,
                "strategies": strategies,
                "confluence_level": len(sigs),
                "cross_asset_multiplier": round(cross_multiplier, 4),
                "conflicts": conflicts,
            }

            if matched_rules:
                logger.info(
                    "CROSS-ASSET CONVERGENCE on %s: multiplier=%.2fx — %s",
                    ticker, cross_multiplier, "; ".join(matched_rules),
                )
            if conflicts:
                logger.warning(
                    "CROSS-ASSET CONFLICTS on %s: multiplier=%.2fx — %s",
                    ticker, cross_multiplier, "; ".join(conflicts),
                )

        return result

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
