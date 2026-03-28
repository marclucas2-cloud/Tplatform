"""
Live Performance Guard — auto-disable strategies with poor live performance.

Evaluates after minimum trade count:
- Sharpe < 0 after 10 trades -> DISABLE
- Win rate < 30% after 10 trades -> DISABLE
- Slippage > 5x backtest -> DISABLE
- 5 consecutive losses -> ALERT + REVIEW

Disabled strategies move to paper-only automatically.
Marc can reactivate manually after diagnosis.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
import math

logger = logging.getLogger(__name__)


# Actions
CONTINUE = "CONTINUE"
DISABLE = "DISABLE"
ALERT = "ALERT"


class LivePerformanceGuard:
    """Monitors live strategy performance and auto-disables underperformers."""

    DEFAULT_THRESHOLDS = {
        "min_trades_for_eval": 10,
        "sharpe_disable": 0.0,
        "win_rate_disable": 0.30,
        "max_consecutive_losses": 5,
        "slippage_disable_ratio": 5.0,
    }

    def __init__(self, thresholds: dict = None, alert_callback=None):
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.alert_callback = alert_callback
        self._disabled_strategies: set = set()
        self._consecutive_losses: dict = {}

    def evaluate(self, strategy_name: str, live_trades: list,
                 backtest_slippage_bps: float = 0) -> tuple:
        """Evaluate a strategy's live performance.

        Args:
            strategy_name: strategy identifier
            live_trades: list of trade dicts with {pnl, slippage_bps, ...}
            backtest_slippage_bps: average slippage in backtest for comparison

        Returns:
            (action, reason) where action is CONTINUE, DISABLE, or ALERT
        """
        n_trades = len(live_trades)
        min_trades = self.thresholds["min_trades_for_eval"]

        # Not enough data yet
        if n_trades < min_trades:
            return CONTINUE, f"Only {n_trades}/{min_trades} trades — too early to evaluate"

        # Calculate metrics
        pnls = [t.get("pnl", 0) for t in live_trades]

        # Sharpe
        sharpe = self._calc_sharpe(pnls)
        if sharpe < self.thresholds["sharpe_disable"]:
            reason = f"Sharpe live = {sharpe:.2f} < {self.thresholds['sharpe_disable']} after {n_trades} trades"
            self._disable(strategy_name, reason)
            return DISABLE, reason

        # Win rate
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / n_trades if n_trades > 0 else 0
        if win_rate < self.thresholds["win_rate_disable"]:
            reason = f"Win rate = {win_rate:.1%} < {self.thresholds['win_rate_disable']:.0%} after {n_trades} trades"
            self._disable(strategy_name, reason)
            return DISABLE, reason

        # Slippage ratio
        if backtest_slippage_bps > 0:
            live_slippage = sum(t.get("slippage_bps", 0) for t in live_trades) / n_trades
            slippage_ratio = live_slippage / backtest_slippage_bps
            if slippage_ratio > self.thresholds["slippage_disable_ratio"]:
                reason = f"Slippage = {slippage_ratio:.1f}x backtest after {n_trades} trades"
                self._disable(strategy_name, reason)
                return DISABLE, reason

        # Consecutive losses (ALERT, not DISABLE)
        consec = self._count_consecutive_losses(pnls)
        if consec >= self.thresholds["max_consecutive_losses"]:
            reason = f"{consec} consecutive losses — manual review recommended"
            if self.alert_callback:
                self.alert_callback(
                    f"STRATEGY ALERT: {strategy_name} — {reason}",
                    "warning"
                )
            return ALERT, reason

        return CONTINUE, f"OK — Sharpe={sharpe:.2f}, WR={win_rate:.1%}, {n_trades} trades"

    def is_disabled(self, strategy_name: str) -> bool:
        return strategy_name in self._disabled_strategies

    def reactivate(self, strategy_name: str, authorized_by: str = "manual"):
        """Manually reactivate a disabled strategy."""
        if strategy_name in self._disabled_strategies:
            self._disabled_strategies.discard(strategy_name)
            logger.info(f"Strategy REACTIVATED: {strategy_name} by {authorized_by}")

    def get_disabled(self) -> list:
        return list(self._disabled_strategies)

    def _disable(self, strategy_name: str, reason: str):
        self._disabled_strategies.add(strategy_name)
        logger.warning(f"STRATEGY AUTO-DISABLED: {strategy_name} — {reason}")
        if self.alert_callback:
            self.alert_callback(
                f"STRATEGY DISABLED: {strategy_name}\nReason: {reason}",
                "critical"
            )

    @staticmethod
    def _calc_sharpe(pnls: list, annualize: bool = False) -> float:
        if len(pnls) < 2:
            return 0.0
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance) if variance > 0 else 0.001
        sharpe = mean_pnl / std_pnl
        if annualize:
            sharpe *= math.sqrt(252)
        return round(sharpe, 3)

    @staticmethod
    def _count_consecutive_losses(pnls: list) -> int:
        max_consec = 0
        current = 0
        for p in pnls:
            if p < 0:
                current += 1
                max_consec = max(max_consec, current)
            else:
                current = 0
        return max_consec
