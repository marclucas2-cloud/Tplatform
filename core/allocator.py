"""
Dynamic Allocator V2 — Risk Parity + Momentum + Correlation-adjusted.

Calcule les poids de chaque strategie en combinant :
  1. Risk Parity (inverse volatilite)
  2. Momentum boost/cut (Sharpe rolling)
  3. Penalite de correlation
  4. Tier caps (S/A/B/C)
  5. Normalisation a (1 - cash_reserve)
  6. Multiplicateurs de regime (bull/bear x vol)
"""

import yaml
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


class DynamicAllocator:
    """Allocation dynamique Risk Parity + Momentum + Correlation-adjusted."""

    def __init__(self, config_path=None):
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "allocation.yaml"
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

    def calculate_weights(self, strategies: dict) -> dict:
        """Calcule les poids optimaux pour chaque strategie.

        Args:
            strategies: {name: {sharpe, volatility, correlation_avg, edge_type}}
                - sharpe: Sharpe ratio rolling (e.g. 2.5)
                - volatility: volatilite annualisee (e.g. 0.15)
                - correlation_avg: correlation moyenne avec les autres strategies
                - edge_type: 'momentum', 'mean_reversion', 'event', 'short'

        Returns:
            {name: weight_pct} — poids normalises (somme = 1 - cash_reserve)
        """
        if not strategies:
            return {}

        weights = {}

        # Step 1: Risk Parity (inverse vol)
        for name, s in strategies.items():
            vol = s.get("volatility", 0.01)
            weights[name] = 1.0 / vol if vol > 0 else 0.0
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        # Step 2: Momentum boost/cut (Sharpe rolling)
        for name, s in strategies.items():
            sharpe = s.get("sharpe", 0)
            if sharpe > 2.0:
                weights[name] *= 1.3
            elif sharpe > 1.0:
                weights[name] *= 1.1
            elif sharpe < 0:
                weights[name] *= 0.5

        # Step 3: Correlation penalty
        for name, s in strategies.items():
            avg_corr = s.get("correlation_avg", 0)
            if avg_corr > 0.6:
                weights[name] *= (1.0 - avg_corr)

        # Step 4+5: Normalize to (1 - cash_reserve) then apply tier caps
        # Iterative: normalize, cap, re-normalize until stable
        cash_reserve = self.config["portfolio"]["min_cash_reserve"]
        target = 1.0 - cash_reserve
        tiers = self.config.get("tiers", {})

        # Build strategy -> max_alloc map
        strat_caps = {}
        for tier_name, tier_config in tiers.items():
            max_alloc = tier_config["max_alloc"]
            for strat in tier_config["strategies"]:
                strat_caps[strat] = max_alloc

        for _iteration in range(10):
            total = sum(weights.values()) or 1.0
            weights = {k: v / total * target for k, v in weights.items()}

            # Apply caps
            capped = False
            for strat, cap in strat_caps.items():
                if strat in weights and weights[strat] > cap + 1e-12:
                    weights[strat] = cap
                    capped = True

            if not capped:
                break

        return weights

    def get_regime_multipliers(self, regime: str) -> dict:
        """Retourne les multiplicateurs par type d'edge selon le regime de marche.

        Args:
            regime: un de 'BULL_NORMAL', 'BULL_HIGH_VOL', 'BEAR_NORMAL', 'BEAR_HIGH_VOL'

        Returns:
            {edge_type: multiplier} pour ajuster les poids
        """
        multipliers = {
            "BULL_NORMAL": {
                "momentum": 1.0,
                "mean_reversion": 1.0,
                "event": 1.0,
                "short": 0.5,
            },
            "BULL_HIGH_VOL": {
                "momentum": 0.8,
                "mean_reversion": 1.2,
                "event": 1.3,
                "short": 0.7,
            },
            "BEAR_NORMAL": {
                "momentum": 0.7,
                "mean_reversion": 0.8,
                "event": 0.9,
                "short": 1.5,
            },
            "BEAR_HIGH_VOL": {
                "momentum": 0.5,
                "mean_reversion": 0.6,
                "event": 0.8,
                "short": 2.0,
            },
        }
        return multipliers.get(regime, multipliers["BULL_NORMAL"])

    def apply_regime(self, weights: dict, strategies: dict, regime: str) -> dict:
        """Applique les multiplicateurs de regime aux poids.

        Args:
            weights: {name: weight} — poids pre-calcules
            strategies: {name: {edge_type, ...}}
            regime: regime de marche actuel

        Returns:
            {name: adjusted_weight} — re-normalise apres ajustement
        """
        if not weights:
            return {}

        mults = self.get_regime_multipliers(regime)
        adjusted = {}
        for name, w in weights.items():
            edge_type = strategies.get(name, {}).get("edge_type", "momentum")
            mult = mults.get(edge_type, 1.0)
            adjusted[name] = w * mult

        # Re-normaliser
        cash_reserve = self.config["portfolio"]["min_cash_reserve"]
        total = sum(adjusted.values()) or 1.0
        target = 1.0 - cash_reserve
        adjusted = {k: v / total * target for k, v in adjusted.items()}

        return adjusted

    def get_bucket_targets(self) -> Dict[str, dict]:
        """Retourne la configuration des buckets.

        Returns:
            {bucket_name: {target, strategies}}
        """
        return self.config.get("buckets", {})

    def get_tier_for_strategy(self, strategy_name: str) -> str:
        """Trouve le tier (S/A/B/C) d'une strategie.

        Args:
            strategy_name: nom de la strategie

        Returns:
            tier name ou 'unknown'
        """
        tiers = self.config.get("tiers", {})
        for tier_name, tier_config in tiers.items():
            if strategy_name in tier_config.get("strategies", []):
                return tier_name
        return "unknown"
