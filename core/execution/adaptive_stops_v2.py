"""P2-02: Adaptive Stop-Loss v2 — volatility-calibrated stops per regime.

Improvements over V1:
  1. Noise floor: minimum SL distance based on historical adverse moves
  2. Regime-specific ATR multipliers (4 regimes instead of 2)
  3. Take-profit linked to SL via risk/reward ratio (min 1.5)
  4. Config-driven via config/stops.yaml

SL = max(noise_floor × 1.2, ATR(14) × regime_multiplier)
TP = SL × reward_ratio (min 1.5)
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "stops.yaml"

# Default multipliers per regime (used if no config/stops.yaml)
DEFAULT_MULTIPLIERS = {
    "TREND_STRONG": 2.0,   # Laisser courir
    "MEAN_REVERT": 1.5,    # Plus serre
    "HIGH_VOL": 3.0,       # Plus large
    "PANIC": 1.0,          # Tres serre — proteger le capital
    "LOW_LIQUIDITY": 2.5,  # Large (spreads + slippage)
    "UNKNOWN": 2.0,        # Default
    "TRENDING_UP": 2.0,
    "TRENDING_DOWN": 2.0,
    "RANGING": 1.5,
    "VOLATILE": 3.0,
    # Crypto regimes
    "BULL": 2.0,
    "BEAR": 1.5,
    "CHOP": 2.5,
}


@dataclass
class StopConfig:
    """Configuration for a strategy's stop-loss behavior."""
    method: str = "ATR_ADAPTIVE"  # ATR_ADAPTIVE or FIXED_PCT
    atr_period: int = 14
    multipliers: dict[str, float] | None = None
    min_sl_pct: float = 0.003   # 0.3% minimum
    max_sl_pct: float = 0.05    # 5% maximum
    noise_floor_lookback: int = 60  # Days
    reward_ratio: float = 2.0   # TP = SL × reward_ratio
    min_reward_ratio: float = 1.5

    def get_multiplier(self, regime: str) -> float:
        if self.multipliers:
            return self.multipliers.get(regime, 2.0)
        return DEFAULT_MULTIPLIERS.get(regime, 2.0)


@dataclass
class StopResult:
    """Computed SL and TP prices."""
    stop_loss: float
    take_profit: float
    sl_distance_pct: float
    tp_distance_pct: float
    atr_used: float
    multiplier_used: float
    noise_floor: float
    regime: str
    method: str


class AdaptiveStopCalculatorV2:
    """Computes adaptive SL/TP based on ATR, noise floor, and regime.

    Usage:
        calc = AdaptiveStopCalculatorV2()
        result = calc.calculate(
            entry_price=45000,
            direction="BUY",
            atr=1200,
            regime="TREND_STRONG",
            strategy="btc_eth_dual_momentum",
            historical_prices=df["close"],  # for noise floor
        )
        print(result.stop_loss, result.take_profit)
    """

    def __init__(self, config_path: Path | None = None):
        self._configs: dict[str, StopConfig] = {}
        self._load_config(config_path or CONFIG_PATH)

    def _load_config(self, path: Path):
        """Load config/stops.yaml if it exists."""
        if not path.exists():
            logger.debug("No stops.yaml found, using defaults")
            return

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        for strat_name, cfg in data.get("strategies", {}).items():
            self._configs[strat_name] = StopConfig(
                method=cfg.get("method", "ATR_ADAPTIVE"),
                atr_period=cfg.get("atr_period", 14),
                multipliers=cfg.get("multipliers"),
                min_sl_pct=cfg.get("min_sl_pct", 0.003),
                max_sl_pct=cfg.get("max_sl_pct", 0.05),
                noise_floor_lookback=cfg.get("noise_floor_lookback", 60),
                reward_ratio=cfg.get("reward_ratio", 2.0),
                min_reward_ratio=cfg.get("min_reward_ratio", 1.5),
            )

    def get_config(self, strategy: str) -> StopConfig:
        """Get config for a strategy, falling back to default."""
        return self._configs.get(strategy, StopConfig())

    def calculate(
        self,
        entry_price: float,
        direction: str,
        atr: float,
        regime: str = "UNKNOWN",
        strategy: str = "default",
        historical_prices: pd.Series | None = None,
    ) -> StopResult:
        """Calculate adaptive SL and TP.

        Args:
            entry_price: Entry price
            direction: "BUY" or "SELL"
            atr: Current ATR(14) value
            regime: Market regime string
            strategy: Strategy name (for config lookup)
            historical_prices: Close prices for noise floor calculation
        """
        config = self.get_config(strategy)

        # 1. Compute noise floor
        noise_floor = 0.0
        if historical_prices is not None and len(historical_prices) > 20:
            noise_floor = self._compute_noise_floor(
                historical_prices, config.noise_floor_lookback
            )

        # 2. ATR-based stop distance
        multiplier = config.get_multiplier(regime)
        atr_distance = atr * multiplier

        # 3. SL = max(noise_floor × 1.2, ATR × multiplier)
        sl_distance = max(noise_floor * 1.2, atr_distance)

        # 4. Apply min/max bounds
        min_sl = entry_price * config.min_sl_pct
        max_sl = entry_price * config.max_sl_pct
        sl_distance = max(min_sl, min(max_sl, sl_distance))

        # 5. Compute SL price
        if direction.upper() == "BUY":
            stop_loss = entry_price - sl_distance
        else:
            stop_loss = entry_price + sl_distance

        # 6. Compute TP
        reward_ratio = max(config.reward_ratio, config.min_reward_ratio)
        tp_distance = sl_distance * reward_ratio

        if direction.upper() == "BUY":
            take_profit = entry_price + tp_distance
        else:
            take_profit = entry_price - tp_distance

        # 7. Percentages
        sl_pct = sl_distance / entry_price
        tp_pct = tp_distance / entry_price

        result = StopResult(
            stop_loss=round(stop_loss, 6),
            take_profit=round(take_profit, 6),
            sl_distance_pct=round(sl_pct, 5),
            tp_distance_pct=round(tp_pct, 5),
            atr_used=round(atr, 6),
            multiplier_used=multiplier,
            noise_floor=round(noise_floor, 6),
            regime=regime,
            method=config.method,
        )

        logger.debug(
            "%s %s %s: SL=%.2f (%.2f%%) TP=%.2f (%.2f%%) regime=%s mult=%.1f",
            strategy, direction, entry_price,
            result.stop_loss, sl_pct * 100,
            result.take_profit, tp_pct * 100,
            regime, multiplier,
        )

        return result

    def _compute_noise_floor(
        self,
        prices: pd.Series,
        lookback: int,
    ) -> float:
        """Compute noise floor: average max adverse excursion before profit.

        This is the typical "noise" move against the trade direction
        before the trade becomes profitable. SL should be wider than this.
        """
        prices = prices.iloc[-lookback:] if len(prices) > lookback else prices

        if len(prices) < 10:
            return 0.0

        returns = prices.pct_change().dropna()

        # Max Adverse Excursion proxy: rolling min return in 5-bar window
        # This represents the worst move against a hypothetical entry
        adverse_moves = returns.rolling(5).min().dropna().abs()

        if adverse_moves.empty:
            return 0.0

        # 75th percentile of adverse moves (conservative)
        noise_pct = float(np.percentile(adverse_moves, 75))

        # Convert back to price distance using latest price
        noise_floor = prices.iloc[-1] * noise_pct

        return noise_floor

    def calculate_batch(
        self,
        orders: list[dict],
        regime: str = "UNKNOWN",
    ) -> list[StopResult]:
        """Calculate stops for a batch of orders."""
        results = []
        for order in orders:
            result = self.calculate(
                entry_price=order["entry_price"],
                direction=order["direction"],
                atr=order.get("atr", order["entry_price"] * 0.01),
                regime=regime,
                strategy=order.get("strategy", "default"),
                historical_prices=order.get("historical_prices"),
            )
            results.append(result)
        return results
