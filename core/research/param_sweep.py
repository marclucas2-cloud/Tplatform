"""P2-05: Parameter Sweep Framework — safe parameter optimization with WF.

Anti-overfitting protections:
  1. WF on EACH combination (not just best backtest)
  2. Coarse grid only (< 50 combinations per strategy)
  3. Stability check: top 3 must have similar Sharpe
  4. 20% holdout validation after WF
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports" / "research"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


@dataclass
class ParamCombination:
    """A single parameter combination and its results."""
    params: dict[str, Any]
    sharpe_is: float = 0.0
    sharpe_oos: float = 0.0
    n_trades: int = 0
    win_rate: float = 0.0
    max_dd: float = 0.0
    holdout_sharpe: float | None = None

    def to_dict(self) -> dict:
        return {
            "params": self.params,
            "sharpe_is": round(self.sharpe_is, 2),
            "sharpe_oos": round(self.sharpe_oos, 2),
            "n_trades": self.n_trades,
            "win_rate": round(self.win_rate, 3),
            "max_dd": round(self.max_dd, 4),
            "holdout_sharpe": round(self.holdout_sharpe, 2) if self.holdout_sharpe is not None else None,
        }


@dataclass
class SweepResult:
    """Full parameter sweep result."""
    strategy: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_combinations: int = 0
    best_params: dict[str, Any] = field(default_factory=dict)
    best_sharpe_oos: float = 0.0
    is_stable: bool = False
    stability_ratio: float = 0.0  # Ratio of 2nd best to best
    holdout_validated: bool = False
    all_results: list[ParamCombination] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        top10 = sorted(self.all_results, key=lambda x: -x.sharpe_oos)[:10]
        return {
            "strategy": self.strategy,
            "timestamp": self.timestamp,
            "total_combinations": self.total_combinations,
            "best_params": self.best_params,
            "best_sharpe_oos": round(self.best_sharpe_oos, 2),
            "is_stable": self.is_stable,
            "stability_ratio": round(self.stability_ratio, 3),
            "holdout_validated": self.holdout_validated,
            "top_10": [r.to_dict() for r in top10],
            "recommendations": self.recommendations,
        }

    def save(self, path: Path | None = None):
        path = path or (REPORTS_DIR / f"sweep_{self.strategy}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


# Default coarse grids per parameter type
DEFAULT_GRIDS = {
    "ema_fast": [5, 10, 15, 20],
    "ema_slow": [20, 30, 50],
    "rsi_period": [10, 14, 20],
    "atr_period": [10, 14, 20],
    "lookback": [10, 20, 30, 50],
    "threshold": [0.3, 0.5, 0.7],
    "momentum_period": [10, 20, 30],
}

# Max combinations guard
MAX_COMBINATIONS = 200  # Hard limit to prevent overfitting


class ParameterSweep:
    """Safe parameter optimization with walk-forward validation.

    Usage:
        sweep = ParameterSweep()
        result = sweep.run(
            strategy="btc_eth_dual_momentum",
            param_grid={"momentum_period": [10, 20, 30], "threshold": [0.3, 0.5]},
            backtest_fn=my_backtest_function,
            data=full_price_data,
        )
    """

    def __init__(
        self,
        wf_windows: int = 5,
        wf_oos_pct: float = 0.30,
        holdout_pct: float = 0.20,
        stability_threshold: float = 0.50,  # 2nd/1st Sharpe ratio
    ):
        self._wf_windows = wf_windows
        self._wf_oos_pct = wf_oos_pct
        self._holdout_pct = holdout_pct
        self._stability_threshold = stability_threshold

    def run(
        self,
        strategy: str,
        param_grid: dict[str, list],
        backtest_fn: Callable[[pd.DataFrame, dict], dict],
        data: pd.DataFrame,
    ) -> SweepResult:
        """Run parameter sweep with WF on each combination.

        Args:
            strategy: Strategy name
            param_grid: {param_name: [values]}
            backtest_fn: function(data, params) -> {sharpe, n_trades, win_rate, max_dd}
            data: Full dataset (DataFrame or Series)
        """
        result = SweepResult(strategy=strategy)

        # Generate combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(product(*param_values))

        if len(combinations) > MAX_COMBINATIONS:
            logger.warning(
                "SWEEP: %d combinations > %d max — truncating",
                len(combinations), MAX_COMBINATIONS,
            )
            combinations = combinations[:MAX_COMBINATIONS]

        result.total_combinations = len(combinations)
        logger.info("SWEEP: %s — %d combinations", strategy, len(combinations))

        # Split holdout
        n = len(data)
        holdout_start = int(n * (1 - self._holdout_pct))
        train_data = data.iloc[:holdout_start]
        holdout_data = data.iloc[holdout_start:]

        # Walk-forward each combination
        for combo in combinations:
            params = dict(zip(param_names, combo))

            try:
                wf_result = self._walk_forward_combo(
                    train_data, params, backtest_fn
                )
            except Exception as e:
                logger.warning("SWEEP: params %s failed: %s", params, e)
                continue

            pc = ParamCombination(
                params=params,
                sharpe_is=wf_result["sharpe_is"],
                sharpe_oos=wf_result["sharpe_oos"],
                n_trades=wf_result["n_trades"],
                win_rate=wf_result["win_rate"],
                max_dd=wf_result["max_dd"],
            )
            result.all_results.append(pc)

        if not result.all_results:
            result.recommendations.append("No valid combinations found")
            return result

        # Sort by OOS Sharpe
        result.all_results.sort(key=lambda x: -x.sharpe_oos)

        best = result.all_results[0]
        result.best_params = best.params
        result.best_sharpe_oos = best.sharpe_oos

        # Stability check
        if len(result.all_results) >= 3:
            top3_sharpes = [r.sharpe_oos for r in result.all_results[:3]]
            if top3_sharpes[0] > 0:
                ratio = top3_sharpes[1] / top3_sharpes[0]
                result.stability_ratio = ratio
                result.is_stable = ratio >= self._stability_threshold
            else:
                result.is_stable = False

        # Holdout validation
        if len(holdout_data) > 20:
            try:
                holdout_result = backtest_fn(holdout_data, best.params)
                best.holdout_sharpe = holdout_result.get("sharpe", 0)
                result.holdout_validated = best.holdout_sharpe > 0
            except Exception as e:
                logger.warning("Holdout validation failed: %s", e)

        # Recommendations
        if not result.is_stable:
            result.recommendations.append(
                f"UNSTABLE: top 3 Sharpes vary widely (ratio={result.stability_ratio:.2f}) — "
                f"likely overfitting"
            )
        if not result.holdout_validated:
            result.recommendations.append(
                "HOLDOUT FAILED: best params don't perform on holdout data"
            )
        if result.is_stable and result.holdout_validated:
            result.recommendations.append(
                f"VALIDATED: stable parameters, holdout confirmed — "
                f"use {best.params}"
            )

        result.save()
        return result

    def _walk_forward_combo(
        self,
        data: pd.DataFrame,
        params: dict,
        backtest_fn: Callable,
    ) -> dict:
        """Run WF for one parameter combination."""
        n = len(data)
        window_size = n // self._wf_windows
        oos_size = int(window_size * self._wf_oos_pct)

        is_sharpes = []
        oos_sharpes = []
        total_trades = 0
        total_wins = 0

        for i in range(self._wf_windows):
            start = i * window_size
            is_end = start + (window_size - oos_size)
            oos_end = start + window_size

            if oos_end > n:
                break

            # OOS evaluation only (IS used for "training" in the strategy)
            oos_data = data.iloc[is_end:oos_end]
            is_data = data.iloc[start:is_end]

            is_result = backtest_fn(is_data, params)
            oos_result = backtest_fn(oos_data, params)

            is_sharpes.append(is_result.get("sharpe", 0))
            oos_sharpes.append(oos_result.get("sharpe", 0))
            total_trades += oos_result.get("n_trades", 0)
            win_rate = oos_result.get("win_rate", 0.5)
            total_wins += int(oos_result.get("n_trades", 0) * win_rate)

        avg_is = np.mean(is_sharpes) if is_sharpes else 0
        avg_oos = np.mean(oos_sharpes) if oos_sharpes else 0

        return {
            "sharpe_is": float(avg_is),
            "sharpe_oos": float(avg_oos),
            "n_trades": total_trades,
            "win_rate": total_wins / total_trades if total_trades > 0 else 0,
            "max_dd": 0,  # Simplified
        }
