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

    # -----------------------------------------------------------------
    # ALLOC-1 : Rebalancing automatique EOD
    # -----------------------------------------------------------------

    def check_rebalance_needed(
        self,
        current_weights: dict,
        target_weights: dict,
        threshold: float = 0.20,
    ) -> dict:
        """Retourne les strategies qui ont drifte de > threshold depuis la cible.

        Le drift est calcule comme |current - target| / target.
        Un threshold de 0.20 signifie qu'un ecart de 20 % relatif declenche
        un rebalancing (ex : cible 12 %, actuel > 14.4 % ou < 9.6 %).

        Args:
            current_weights: {strategy: current_weight} — poids actuels
            target_weights:  {strategy: target_weight}  — poids cibles
            threshold:       seuil de drift relatif (defaut 0.20 = 20 %)

        Returns:
            {strategy: {current, target, drift_pct, action: 'increase'|'decrease'}}
            Seules les strategies dont |drift| > threshold sont incluses.
        """
        rebalance = {}
        # Union de toutes les strategies referencees
        all_strategies = set(current_weights.keys()) | set(target_weights.keys())

        for strat in all_strategies:
            current = current_weights.get(strat, 0.0)
            target = target_weights.get(strat, 0.0)

            # Si la cible est 0, toute position non-nulle doit etre reduite
            if target == 0:
                if current > 0:
                    rebalance[strat] = {
                        "current": current,
                        "target": target,
                        "drift_pct": float("inf"),
                        "action": "decrease",
                    }
                continue

            drift_pct = abs(current - target) / target

            if drift_pct > threshold:
                action = "decrease" if current > target else "increase"
                rebalance[strat] = {
                    "current": current,
                    "target": target,
                    "drift_pct": round(drift_pct, 4),
                    "action": action,
                }

        if rebalance:
            logger.info(
                "Rebalance needed for %d strategies (threshold=%.0f%%): %s",
                len(rebalance),
                threshold * 100,
                list(rebalance.keys()),
            )
        else:
            logger.debug("No rebalance needed (threshold=%.0f%%)", threshold * 100)

        return rebalance

    # -----------------------------------------------------------------
    # ALLOC-5 : Allocation bear-specific par bucket
    # -----------------------------------------------------------------

    REGIME_BUCKET_MULTIPLIERS = {
        "BULL_NORMAL": {
            "core_alpha": 1.0,
            "shorts_bear": 0.5,
            "diversifiers": 1.0,
            "satellite": 1.0,
            "daily_monthly": 1.0,
        },
        "BULL_HIGH_VOL": {
            "core_alpha": 0.8,
            "shorts_bear": 0.7,
            "diversifiers": 1.2,
            "satellite": 0.8,
            "daily_monthly": 1.0,
        },
        "BEAR_NORMAL": {
            "core_alpha": 0.6,
            "shorts_bear": 1.5,
            "diversifiers": 1.0,
            "satellite": 0.3,
            "daily_monthly": 0.8,
        },
        "BEAR_HIGH_VOL": {
            "core_alpha": 0.4,
            "shorts_bear": 2.0,
            "diversifiers": 1.0,
            "satellite": 0.0,
            "daily_monthly": 0.5,
        },
    }

    def apply_regime_buckets(self, weights: dict, regime: str) -> dict:
        """Applique les multiplicateurs de regime par bucket aux poids.

        Chaque strategie est identifiee dans un bucket (via config/allocation.yaml).
        Le multiplicateur du regime courant est applique, puis les poids sont
        re-normalises a (1 - cash_reserve).

        Args:
            weights: {strategy: weight} — poids pre-calcules
            regime:  un de 'BULL_NORMAL', 'BULL_HIGH_VOL', 'BEAR_NORMAL', 'BEAR_HIGH_VOL'

        Returns:
            {strategy: adjusted_weight} — re-normalise apres ajustement
        """
        if not weights:
            return {}

        mults = self.REGIME_BUCKET_MULTIPLIERS.get(
            regime, self.REGIME_BUCKET_MULTIPLIERS["BULL_NORMAL"]
        )

        # Build strategy -> bucket mapping
        buckets = self.config.get("buckets", {})
        strat_to_bucket: Dict[str, str] = {}
        for bucket_name, bucket_cfg in buckets.items():
            for strat in bucket_cfg.get("strategies", []):
                strat_to_bucket[strat] = bucket_name

        adjusted = {}
        for name, w in weights.items():
            bucket = strat_to_bucket.get(name)
            mult = mults.get(bucket, 1.0) if bucket else 1.0
            adjusted[name] = w * mult

        # Re-normaliser a (1 - cash_reserve)
        cash_reserve = self.config["portfolio"]["min_cash_reserve"]
        total = sum(adjusted.values()) or 1.0
        target = 1.0 - cash_reserve
        adjusted = {k: v / total * target for k, v in adjusted.items()}

        logger.info(
            "Applied regime bucket multipliers (regime=%s): %d strategies adjusted",
            regime,
            len(adjusted),
        )

        return adjusted

    # -----------------------------------------------------------------
    # ROC-6 : Allocation cross-timezone
    # -----------------------------------------------------------------

    # Budget de risque par creneau horaire CET
    TIMEZONE_ALLOCATIONS = {
        "EU_ONLY": {
            "hours": (9, 15),       # 9:00-15:30 CET (EU only)
            "eu": 0.25,
            "us": 0.00,
            "fx": 0.05,
            "shorts": 0.00,
            "us_reserve": 0.50,
            "cash": 0.20,
        },
        "OVERLAP": {
            "hours": (15, 17),      # 15:30-17:30 CET (EU + US overlap)
            "eu": 0.15,
            "us": 0.40,
            "fx": 0.05,
            "shorts": 0.15,
            "cash": 0.25,
        },
        "US_ONLY": {
            "hours": (17, 22),      # 17:30-22:00 CET (US only)
            "eu": 0.00,
            "us": 0.45,
            "fx": 0.05,
            "shorts": 0.20,
            "cash": 0.30,
        },
        "OFF_HOURS": {
            "hours": (22, 9),       # 22:00-09:00 CET (off-hours)
            "eu": 0.00,
            "us": 0.00,
            "fx": 0.10,
            "shorts": 0.00,
            "cash": 0.90,
        },
    }

    def get_timezone_allocation(self, hour_cet: int) -> dict:
        """Le budget de risque se redistribue selon les marches ouverts.

        9:00-15:30 CET (EU only) :
          EU strategies : 25% du capital
          FX carry/swing : 5%
          US reserve : 50% (pret pour ouverture US)
          Cash : 20%

        15:30-17:30 CET (OVERLAP EU+US) :
          EU : 15%, US : 40%, FX : 5%, Shorts : 15%, Cash : 25%

        17:30-22:00 CET (US only) :
          US : 45%, Shorts : 20%, FX : 5%, Cash : 30%

        22:00-9:00 CET (OFF-HOURS) :
          FX swing : 10%, Cash : 90%

        Args:
            hour_cet: heure CET (0-23). Ex: 16 pour 16:00 CET.

        Returns:
            {
                "timezone": "EU_ONLY"|"OVERLAP"|"US_ONLY"|"OFF_HOURS",
                "eu": float,
                "us": float,
                "fx": float,
                "shorts": float,
                "cash": float,
                "us_reserve": float (only EU_ONLY),
                "total_invested": float,
            }
        """
        hour = hour_cet % 24

        if 9 <= hour < 15:
            tz_name = "EU_ONLY"
        elif 15 <= hour < 17:
            tz_name = "OVERLAP"
        elif 17 <= hour < 22:
            tz_name = "US_ONLY"
        else:
            tz_name = "OFF_HOURS"

        alloc = dict(self.TIMEZONE_ALLOCATIONS[tz_name])
        alloc["timezone"] = tz_name

        # Calculer le total investi (hors cash et reserve)
        total_invested = sum(
            v for k, v in alloc.items()
            if isinstance(v, (int, float)) and k not in ("cash", "us_reserve", "hours")
        )
        alloc["total_invested"] = round(total_invested, 4)
        alloc.pop("hours", None)

        logger.info(
            "Timezone allocation (hour_cet=%d): %s — %.0f%% invested",
            hour_cet, tz_name, total_invested * 100,
        )

        return alloc

    def apply_timezone_weights(
        self,
        weights: dict,
        strategies: dict,
        hour_cet: int,
    ) -> dict:
        """Applique les limites cross-timezone aux poids des strategies.

        Chaque strategie doit avoir un champ 'market' parmi :
        'us', 'eu', 'fx', 'shorts'.

        Args:
            weights: {strategy_name: weight} — poids pre-calcules
            strategies: {strategy_name: {market: 'us'|'eu'|'fx'|'shorts', ...}}
            hour_cet: heure CET courante

        Returns:
            {strategy_name: adjusted_weight} — normalise selon les limites TZ
        """
        if not weights:
            return {}

        tz_alloc = self.get_timezone_allocation(hour_cet)
        adjusted = {}

        # Grouper par market
        market_totals: Dict[str, float] = {}
        market_strats: Dict[str, list] = {}
        for name, w in weights.items():
            market = strategies.get(name, {}).get("market", "us")
            market_totals[market] = market_totals.get(market, 0) + w
            market_strats.setdefault(market, []).append(name)

        # Appliquer les plafonds par market
        for market, strat_names in market_strats.items():
            budget = tz_alloc.get(market, 0.0)
            current_total = market_totals.get(market, 0)

            if current_total <= 0 or budget <= 0:
                # Marche ferme ou budget nul : poids a zero
                for name in strat_names:
                    adjusted[name] = 0.0
            elif current_total > budget:
                # Reduire proportionnellement
                scale = budget / current_total
                for name in strat_names:
                    adjusted[name] = weights[name] * scale
            else:
                # Sous le plafond : garder tel quel
                for name in strat_names:
                    adjusted[name] = weights[name]

        logger.info(
            "Applied timezone weights (hour_cet=%d, tz=%s): %d strategies",
            hour_cet, tz_alloc["timezone"], len(adjusted),
        )

        return adjusted
