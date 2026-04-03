"""
Dynamic Allocator V2 — Regime-Adaptive Allocation

Adjusts portfolio allocation based on HMM regime detection.
Smooth transitions prevent whipsawing.
"""


import numpy as np


class DynamicAllocatorV2:
    """
    Regime-adaptive portfolio allocation.

    Uses RegimeDetectorHMM output to shift allocation between
    risk-on (equities, trend) and risk-off (FX, gold, shorts).
    Smooth transitions at 20%/day prevent whipsawing.
    """

    REGIME_TARGETS = {
        "BULL": {
            "us_equity": 0.45, "eu_equity": 0.20, "fx": 0.12,
            "futures_trend": 0.12, "futures_hedge": 0.02,
            "cash": 0.05, "shorts": 0.04
        },
        "NEUTRAL": {
            "us_equity": 0.35, "eu_equity": 0.20, "fx": 0.18,
            "futures_trend": 0.08, "futures_hedge": 0.05,
            "cash": 0.07, "shorts": 0.07
        },
        "BEAR": {
            "us_equity": 0.15, "eu_equity": 0.10, "fx": 0.25,
            "futures_trend": 0.05, "futures_hedge": 0.15,
            "cash": 0.15, "shorts": 0.15
        },
    }

    TRANSITION_SPEED = 0.20  # Move 20% toward target per day

    def __init__(self, initial_allocation=None):
        self.current_allocation = initial_allocation or self.REGIME_TARGETS["NEUTRAL"].copy()
        self._history = []

    def calculate_regime_allocation(self, regime: str, confidence: float = 1.0) -> dict:
        """Get target allocation for a regime, adjusted by confidence."""
        target = self.REGIME_TARGETS.get(regime, self.REGIME_TARGETS["NEUTRAL"]).copy()
        neutral = self.REGIME_TARGETS["NEUTRAL"]

        # Blend with neutral based on confidence (low confidence = stay neutral)
        for key in target:
            target[key] = neutral[key] + confidence * (target[key] - neutral[key])

        return target

    def smooth_transition(self, current: dict, target: dict, speed: float = None) -> dict:
        """Gradual shift toward target allocation. Prevents whipsawing."""
        speed = speed or self.TRANSITION_SPEED
        result = {}
        for key in target:
            curr_val = current.get(key, 0.0)
            tgt_val = target[key]
            result[key] = curr_val + speed * (tgt_val - curr_val)

        # Normalize to sum to 1.0
        total = sum(result.values())
        if total > 0:
            result = {k: v / total for k, v in result.items()}

        return result

    def update(self, regime: str, confidence: float = 1.0) -> dict:
        """Update allocation based on new regime signal. Returns new allocation."""
        target = self.calculate_regime_allocation(regime, confidence)
        self.current_allocation = self.smooth_transition(self.current_allocation, target)
        self._history.append({
            "regime": regime, "confidence": confidence,
            "allocation": self.current_allocation.copy()
        })
        return self.current_allocation

    def get_strategy_weight(self, strategy_name: str, strategy_asset_class: str) -> float:
        """Get current weight for a specific strategy within its asset class bucket."""
        return self.current_allocation.get(strategy_asset_class, 0.0)

    def backtest_dynamic_vs_static(self, regimes: list, returns: dict) -> dict:
        """
        Compare dynamic regime allocation vs static.

        Args:
            regimes: list of {"date": str, "regime": str, "confidence": float}
            returns: dict of {asset_class: list of daily returns}

        Returns:
            {"dynamic_sharpe": float, "static_sharpe": float, "improvement_pct": float}
        """
        dynamic_returns = []
        static_returns = []
        static_alloc = self.REGIME_TARGETS["NEUTRAL"]

        alloc = static_alloc.copy()
        for i, regime_info in enumerate(regimes):
            target = self.calculate_regime_allocation(
                regime_info["regime"], regime_info.get("confidence", 1.0)
            )
            alloc = self.smooth_transition(alloc, target)

            # Calculate daily return for both
            dyn_ret = sum(alloc.get(ac, 0) * returns.get(ac, [0] * len(regimes))[i]
                         for ac in alloc)
            stat_ret = sum(static_alloc.get(ac, 0) * returns.get(ac, [0] * len(regimes))[i]
                          for ac in static_alloc)

            dynamic_returns.append(dyn_ret)
            static_returns.append(stat_ret)

        dyn_arr = np.array(dynamic_returns)
        stat_arr = np.array(static_returns)

        dyn_sharpe = np.mean(dyn_arr) / (np.std(dyn_arr) + 1e-10) * np.sqrt(252)
        stat_sharpe = np.mean(stat_arr) / (np.std(stat_arr) + 1e-10) * np.sqrt(252)

        return {
            "dynamic_sharpe": round(float(dyn_sharpe), 2),
            "static_sharpe": round(float(stat_sharpe), 2),
            "improvement_pct": round(float((dyn_sharpe - stat_sharpe) / (stat_sharpe + 1e-10) * 100), 1)
        }
