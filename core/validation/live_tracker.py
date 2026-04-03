"""
D5-03 — Live Performance Tracker.

Continuous post-go-live tracking per strategy:
  - Sharpe rolling 30d
  - Comparison with backtest OOS Sharpe
  - Z-score of deviation
  - Alpha decay detection:
    z < -2.0 for 5 consecutive days → ALERT
    z < -3.0 → KILL strategy automatically

Integrates with alpha_decay_monitor.py (adds live vs backtest dimension).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "data" / "validation" / "live_performance.jsonl"

# OOS Sharpe from walk-forward validation
OOS_SHARPE = {
    "fx_carry_vol_scaled": 3.04,
    "fx_carry_momentum": 2.17,
    "crypto_dual_momentum": 1.20,
    "crypto_vol_breakout": 1.50,
    "crypto_btc_dom_rotation": 1.10,
    "crypto_borrow_carry": 0.80,
    "crypto_liq_momentum": 1.30,
    "crypto_weekend_gap": 0.90,
    "crypto_range_bb": 1.35,
    "crypto_vol_expansion": 0.85,
    "mes_trend": 1.46,
    "mes_mnq_pairs": 0.80,
}

# OOS Sharpe standard deviation (estimated from WF windows)
OOS_SHARPE_STD = {k: v * 0.3 for k, v in OOS_SHARPE.items()}  # ~30% of mean


class LivePerformanceTracker:
    """Tracks live performance and detects alpha decay.

    Usage::

        tracker = LivePerformanceTracker(
            alert_callback=send_telegram,
            kill_callback=kill_strategy,
        )
        # Daily update with new return
        tracker.add_return("fx_carry_vol_scaled", 0.0015)
        report = tracker.get_report("fx_carry_vol_scaled")
    """

    def __init__(
        self,
        alert_callback: Callable | None = None,
        kill_callback: Callable | None = None,
        rolling_window: int = 30,
    ):
        self._alert = alert_callback
        self._kill = kill_callback
        self._window = rolling_window
        self._returns: dict[str, list[float]] = defaultdict(list)
        self._z_history: dict[str, list[float]] = defaultdict(list)
        self._consecutive_low_z: dict[str, int] = defaultdict(int)
        self._killed_strategies: set[str] = set()

    def add_return(self, strategy: str, daily_return: float) -> dict | None:
        """Add a daily return and check for alpha decay.

        Returns alert dict if triggered, None otherwise.
        """
        self._returns[strategy].append(daily_return)
        if len(self._returns[strategy]) > 500:
            self._returns[strategy] = self._returns[strategy][-250:]

        returns = self._returns[strategy]
        if len(returns) < self._window:
            return None

        # Rolling Sharpe (annualized)
        recent = np.array(returns[-self._window:])
        mean_r = float(np.mean(recent))
        std_r = float(np.std(recent))
        sharpe_live = (mean_r / std_r * np.sqrt(252)) if std_r > 1e-9 else 0

        # Z-score vs OOS
        oos_sharpe = OOS_SHARPE.get(strategy, 1.0)
        oos_std = OOS_SHARPE_STD.get(strategy, 0.5)
        z_score = (sharpe_live - oos_sharpe) / oos_std if oos_std > 0 else 0

        self._z_history[strategy].append(z_score)
        if len(self._z_history[strategy]) > 365:
            self._z_history[strategy] = self._z_history[strategy][-180:]

        # Log
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "strategy": strategy,
            "sharpe_30d": round(sharpe_live, 3),
            "oos_sharpe": oos_sharpe,
            "z_score": round(z_score, 3),
        }
        self._save(entry)

        # Check alpha decay
        alert = None
        if z_score < -2.0:
            self._consecutive_low_z[strategy] += 1
        else:
            self._consecutive_low_z[strategy] = 0

        # KILL: z < -3.0
        if z_score < -3.0 and strategy not in self._killed_strategies:
            alert = {
                "level": "CRITICAL",
                "strategy": strategy,
                "action": "KILL",
                "sharpe_live": round(sharpe_live, 3),
                "z_score": round(z_score, 3),
                "message": (
                    f"ALPHA DECAY KILL: {strategy}\n"
                    f"Sharpe live: {sharpe_live:.2f} vs OOS: {oos_sharpe:.2f}\n"
                    f"Z-score: {z_score:.2f} < -3.0"
                ),
            }
            self._killed_strategies.add(strategy)
            if self._kill:
                try:
                    self._kill(strategy)
                except Exception as e:
                    logger.error("Kill strategy %s failed: %s", strategy, e)
            if self._alert:
                self._alert(alert["message"], level="critical")

        # ALERT: z < -2.0 for 5 consecutive days
        elif self._consecutive_low_z[strategy] >= 5:
            alert = {
                "level": "WARNING",
                "strategy": strategy,
                "action": "ALERT",
                "sharpe_live": round(sharpe_live, 3),
                "z_score": round(z_score, 3),
                "message": (
                    f"ALPHA DECAY WARNING: {strategy}\n"
                    f"Sharpe live: {sharpe_live:.2f} vs OOS: {oos_sharpe:.2f}\n"
                    f"Z < -2.0 for {self._consecutive_low_z[strategy]} days"
                ),
            }
            if self._alert:
                self._alert(alert["message"], level="warning")

        return alert

    def get_report(self, strategy: str | None = None) -> dict:
        """Get performance report for one or all strategies."""
        if strategy:
            return self._strategy_report(strategy)

        reports = {}
        for strat in self._returns:
            reports[strat] = self._strategy_report(strat)
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "strategies": reports,
            "killed": list(self._killed_strategies),
        }

    def _strategy_report(self, strategy: str) -> dict:
        returns = self._returns.get(strategy, [])
        if len(returns) < self._window:
            return {"strategy": strategy, "status": "INSUFFICIENT_DATA", "trades": len(returns)}

        recent = np.array(returns[-self._window:])
        sharpe = float(np.mean(recent) / np.std(recent) * np.sqrt(252)) if np.std(recent) > 0 else 0
        oos = OOS_SHARPE.get(strategy, 1.0)
        oos_std = OOS_SHARPE_STD.get(strategy, 0.5)
        z = (sharpe - oos) / oos_std if oos_std > 0 else 0

        return {
            "strategy": strategy,
            "sharpe_30d": round(sharpe, 3),
            "oos_sharpe": oos,
            "z_score": round(z, 3),
            "consecutive_low_z": self._consecutive_low_z.get(strategy, 0),
            "killed": strategy in self._killed_strategies,
            "observations": len(returns),
        }

    def _save(self, entry: dict) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error("Live tracker log failed: %s", e)
