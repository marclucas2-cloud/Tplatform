"""
D1-02 — Strategy Activation Matrix.

Maps (strategy, regime) -> sizing multiplier (0.0 to 1.0).
0.0 = strategy OFF (no new trades, existing SL maintained).
0.5 = sizing reduced 50%.
1.0 = nominal sizing.

Loaded from config/regime.yaml, with manual override via Telegram.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .multi_asset_regime import Regime, AssetClass

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "regime.yaml"
OVERRIDE_PATH = ROOT / "data" / "regime_override.json"

# Default activation matrix — used if config/regime.yaml not found
DEFAULT_MATRIX: dict[str, dict[str, float]] = {
    # FX strategies
    "fx_carry_vol_scaled":     {"TREND_STRONG": 1.0, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "fx_carry_momentum":       {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.8, "HIGH_VOL": 0.3, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    # Crypto strategies
    "crypto_dual_momentum":    {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.7, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "crypto_vol_breakout":     {"TREND_STRONG": 0.5, "MEAN_REVERT": 0.0, "HIGH_VOL": 1.0, "PANIC": 0.5, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.3},
    "crypto_btc_dom_rotation": {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "crypto_borrow_carry":     {"TREND_STRONG": 1.0, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.5, "UNKNOWN": 0.7},
    "crypto_liq_momentum":     {"TREND_STRONG": 0.3, "MEAN_REVERT": 0.3, "HIGH_VOL": 1.0, "PANIC": 1.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.3},
    "crypto_weekend_gap":      {"TREND_STRONG": 0.5, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "crypto_range_bb":         {"TREND_STRONG": 0.3, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "crypto_vol_expansion":    {"TREND_STRONG": 0.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 1.0, "PANIC": 1.0, "LOW_LIQUIDITY": 0.5, "UNKNOWN": 0.3},
    # Futures strategies
    "mes_trend":               {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.3, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.0, "UNKNOWN": 0.3},
    "mes_mnq_pairs":           {"TREND_STRONG": 0.3, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.0, "UNKNOWN": 0.3},
    # US equity strategies
    "dow_seasonal":            {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.3, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "corr_hedge":              {"TREND_STRONG": 0.5, "MEAN_REVERT": 0.5, "HIGH_VOL": 1.0, "PANIC": 1.0, "LOW_LIQUIDITY": 0.5, "UNKNOWN": 0.5},
    "vix_short":               {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.0, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.3},
    "high_beta_short":         {"TREND_STRONG": 0.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 1.0, "PANIC": 1.0, "LOW_LIQUIDITY": 0.5, "UNKNOWN": 0.3},
    "late_day_mr":             {"TREND_STRONG": 0.3, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    # EU equity strategies
    "eu_gap_open":             {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.3, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "bce_momentum":            {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "auto_sector_german":      {"TREND_STRONG": 0.5, "MEAN_REVERT": 1.0, "HIGH_VOL": 0.3, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "brent_lag":               {"TREND_STRONG": 1.0, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
    "eu_close_us":             {"TREND_STRONG": 0.5, "MEAN_REVERT": 0.5, "HIGH_VOL": 0.5, "PANIC": 0.0, "LOW_LIQUIDITY": 0.3, "UNKNOWN": 0.5},
}

# Map strategies to their asset class for regime lookup
STRATEGY_ASSET_CLASS: dict[str, str] = {
    "fx_carry_vol_scaled": "FX",
    "fx_carry_momentum": "FX",
    "crypto_dual_momentum": "CRYPTO",
    "crypto_vol_breakout": "CRYPTO",
    "crypto_btc_dom_rotation": "CRYPTO",
    "crypto_borrow_carry": "CRYPTO",
    "crypto_liq_momentum": "CRYPTO",
    "crypto_weekend_gap": "CRYPTO",
    "crypto_range_bb": "CRYPTO",
    "crypto_vol_expansion": "CRYPTO",
    "mes_trend": "FUTURES",
    "mes_mnq_pairs": "FUTURES",
    "dow_seasonal": "US_EQUITY",
    "corr_hedge": "US_EQUITY",
    "vix_short": "US_EQUITY",
    "high_beta_short": "US_EQUITY",
    "late_day_mr": "US_EQUITY",
    "eu_gap_open": "EU_EQUITY",
    "bce_momentum": "EU_EQUITY",
    "auto_sector_german": "EU_EQUITY",
    "brent_lag": "EU_EQUITY",
    "eu_close_us": "EU_EQUITY",
}


# Aliases: real strategy IDs → matrix keys
# The worker uses STRAT-xxx or registry names; the matrix uses shorter keys.
STRATEGY_ALIASES: dict[str, str] = {
    # Crypto
    "STRAT-001": "crypto_dual_momentum",
    "btc_eth_dual_momentum": "crypto_dual_momentum",
    "STRAT-002": "crypto_dual_momentum",  # altcoin RS ~ same profile
    "STRAT-003": "crypto_dual_momentum",  # BTC mean reversion ~ similar
    "STRAT-004": "crypto_vol_breakout",
    "vol_breakout": "crypto_vol_breakout",
    "STRAT-005": "crypto_btc_dom_rotation",
    "STRAT-006": "crypto_borrow_carry",
    "borrow_rate_carry": "crypto_borrow_carry",
    "STRAT-007": "crypto_liq_momentum",
    "liquidation_momentum": "crypto_liq_momentum",
    "STRAT-008": "crypto_weekend_gap",
    "STRAT-009": "crypto_vol_expansion",  # trend short ~ vol expansion profile
    "STRAT-010": "crypto_range_bb",       # MR scalp ~ range BB profile
    "STRAT-011": "crypto_liq_momentum",   # liq spike ~ same profile
    "STRAT-012": "crypto_vol_expansion",
    "STRAT-014": "crypto_range_bb",
    # US equity
    "lateday_meanrev": "late_day_mr",
    "rsi2_mean_reversion": "late_day_mr",  # similar MR profile
    # FX
    "fx_carry_momentum_filter": "fx_carry_momentum",
}


class ActivationMatrix:
    """Strategy x Regime activation matrix.

    Usage::

        matrix = ActivationMatrix()
        mult = matrix.get_multiplier("fx_carry_vol_scaled", Regime.PANIC)
        # mult = 0.0 → strategy OFF
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or CONFIG_PATH
        self._matrix: dict[str, dict[str, float]] = {}
        self._strategy_asset_class: dict[str, str] = dict(STRATEGY_ASSET_CLASS)
        self._manual_override: Optional[str] = None  # Telegram override
        self._load()

    def _load(self) -> None:
        """Load matrix from YAML config, fallback to defaults."""
        try:
            if self._config_path.exists():
                with open(self._config_path, "r") as f:
                    cfg = yaml.safe_load(f) or {}
                self._matrix = cfg.get("activation_matrix", {})
                # Merge strategy-asset mapping from config
                sac = cfg.get("strategy_asset_class", {})
                if sac:
                    self._strategy_asset_class.update(sac)
                logger.info(
                    "Activation matrix loaded from %s (%d strategies)",
                    self._config_path, len(self._matrix),
                )
            else:
                logger.info("No regime.yaml found, using default matrix")
        except Exception as e:
            logger.error("Error loading regime config: %s", e)

        # Fill missing strategies from defaults
        for strat, regimes in DEFAULT_MATRIX.items():
            if strat not in self._matrix:
                self._matrix[strat] = regimes

    def get_multiplier(
        self,
        strategy_id: str,
        regime: Regime,
        asset_class: Optional[str] = None,
    ) -> float:
        """Get sizing multiplier for a strategy in the given regime.

        Args:
            strategy_id: Strategy identifier (e.g. "fx_carry_vol_scaled").
            regime: Current regime for the strategy's asset class.
            asset_class: Override asset class (auto-detected if None).

        Returns:
            Float 0.0-1.0 sizing multiplier.
        """
        # Manual override via Telegram takes precedence
        if self._manual_override:
            try:
                override_regime = Regime(self._manual_override)
                regime = override_regime
            except ValueError:
                pass

        regime_str = regime.value if isinstance(regime, Regime) else str(regime)

        # Resolve alias first
        resolved = STRATEGY_ALIASES.get(strategy_id, strategy_id)

        # Exact match
        if resolved in self._matrix:
            return self._matrix[resolved].get(regime_str, 0.5)

        # Fuzzy match: try prefix matching
        for key in self._matrix:
            if resolved.startswith(key) or key.startswith(resolved):
                return self._matrix[key].get(regime_str, 0.5)

        # Unknown strategy — conservative default
        if regime in (Regime.PANIC, Regime.HIGH_VOL):
            return 0.3
        return 0.5

    def get_asset_class(self, strategy_id: str) -> str:
        """Get asset class for a strategy."""
        resolved = STRATEGY_ALIASES.get(strategy_id, strategy_id)
        if resolved in self._strategy_asset_class:
            return self._strategy_asset_class[resolved]
        if strategy_id in self._strategy_asset_class:
            return self._strategy_asset_class[strategy_id]
        # Heuristic
        if "crypto" in strategy_id.lower():
            return "CRYPTO"
        if "fx" in strategy_id.lower() or "carry" in strategy_id.lower():
            return "FX"
        if "mes" in strategy_id.lower() or "mnq" in strategy_id.lower():
            return "FUTURES"
        if "eu_" in strategy_id.lower() or "bce" in strategy_id.lower():
            return "EU_EQUITY"
        return "US_EQUITY"

    def set_manual_override(self, regime_str: Optional[str]) -> None:
        """Set manual regime override (from Telegram /regime_override)."""
        if regime_str is None:
            self._manual_override = None
            logger.info("Regime manual override cleared")
            return
        try:
            Regime(regime_str)
            self._manual_override = regime_str
            logger.warning("REGIME MANUAL OVERRIDE SET: %s", regime_str)
            # Persist override
            OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OVERRIDE_PATH, "w") as f:
                json.dump({"override": regime_str, "timestamp": datetime.now(timezone.utc).isoformat()}, f)
        except ValueError:
            logger.error("Invalid regime override: %s", regime_str)

    def get_report(self) -> dict:
        """Full matrix report for dashboard."""
        return {
            "matrix": self._matrix,
            "strategy_count": len(self._matrix),
            "manual_override": self._manual_override,
        }
