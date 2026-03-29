"""Strategy Throttling System — auto-reduce activity on degradation.

Rules:
  - High correlation → reduce frequency of correlated strategies
  - Drawdown → pause lowest-Sharpe strategies
  - Execution degraded → stop affected strategies

Usage:
    throttler = StrategyThrottler(correlation_engine)
    actions = throttler.evaluate(strategies_state)
    for action in actions:
        if action.action == "PAUSE":
            pause_strategy(action.strategy)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ThrottleAction:
    strategy: str
    action: str  # CONTINUE | REDUCE_SIZE | PAUSE | STOP
    reason: str
    size_multiplier: float  # 1.0 = normal, 0.5 = half size, 0 = no trading
    duration_minutes: int  # How long to maintain this action (0 = indefinite)
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "action": self.action,
            "reason": self.reason,
            "size_multiplier": round(self.size_multiplier, 2),
            "duration_minutes": self.duration_minutes,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class StrategyState:
    """Current state of a strategy for throttling evaluation."""
    name: str
    sharpe_live: float  # Live Sharpe (rolling)
    n_trades: int  # Total trades
    win_rate: float  # Win rate (0-1)
    slippage_ratio: float  # Actual/backtest slippage ratio
    consecutive_losses: int
    is_in_cluster: bool  # Part of correlated cluster
    cluster_level: str  # OK | WARNING | CRITICAL
    last_trade_age_hours: float  # Hours since last trade


class StrategyThrottler:
    """Dynamically throttle strategies based on live conditions."""

    # Thresholds
    SHARPE_PAUSE = -0.5  # Pause if live Sharpe < -0.5
    SHARPE_REDUCE = 0.0  # Reduce size if Sharpe < 0
    WIN_RATE_MIN = 0.25  # Pause if win rate < 25% (min 15 trades)
    SLIPPAGE_REDUCE = 2.5  # Reduce if slippage > 2.5x backtest
    SLIPPAGE_STOP = 4.0  # Stop if slippage > 4x backtest
    CONSEC_LOSSES_REDUCE = 4  # Reduce after 4 consecutive losses
    CONSEC_LOSSES_PAUSE = 6  # Pause after 6 consecutive losses
    MIN_TRADES_FOR_EVAL = 10  # Need at least 10 trades to evaluate

    # Cluster throttling
    CLUSTER_CRITICAL_SIZE_MULT = 0.5  # Halve size in CRITICAL cluster
    CLUSTER_WARNING_SIZE_MULT = 0.75  # Reduce 25% in WARNING cluster

    def __init__(
        self,
        correlation_engine=None,
        min_trades: int = MIN_TRADES_FOR_EVAL,
    ):
        self.correlation_engine = correlation_engine
        self.min_trades = min_trades
        self._pause_until: Dict[str, datetime] = {}

    def evaluate(
        self,
        strategies: List[StrategyState],
        drawdown_pct: float = 0.0,
    ) -> List[ThrottleAction]:
        """Evaluate all strategies and return throttle actions.

        Args:
            strategies: Current state of each active strategy.
            drawdown_pct: Portfolio-level drawdown (positive fraction).
        """
        actions = []
        now = datetime.utcnow()

        for strat in strategies:
            action = self._evaluate_single(strat, drawdown_pct, now)
            actions.append(action)

        # Portfolio-level: if DD > 3%, pause lowest-Sharpe strategies
        if drawdown_pct >= 0.03 and len(strategies) > 3:
            sorted_by_sharpe = sorted(strategies, key=lambda s: s.sharpe_live)
            n_to_pause = max(1, len(strategies) // 4)  # Pause bottom 25%

            for strat in sorted_by_sharpe[:n_to_pause]:
                # Override existing action if not already paused/stopped
                existing = next(
                    (a for a in actions if a.strategy == strat.name), None
                )
                if existing and existing.action not in ("PAUSE", "STOP"):
                    actions = [a for a in actions if a.strategy != strat.name]
                    actions.append(ThrottleAction(
                        strategy=strat.name,
                        action="PAUSE",
                        reason=f"DD={drawdown_pct:.1%}, lowest Sharpe={strat.sharpe_live:.2f}",
                        size_multiplier=0.0,
                        duration_minutes=60,
                        timestamp=now,
                    ))

        return actions

    def is_paused(self, strategy: str) -> bool:
        """Check if a strategy is currently paused."""
        if strategy not in self._pause_until:
            return False
        return datetime.utcnow() < self._pause_until[strategy]

    def pause(self, strategy: str, minutes: int = 60) -> None:
        """Manually pause a strategy."""
        self._pause_until[strategy] = datetime.utcnow() + timedelta(minutes=minutes)
        logger.info(f"Strategy {strategy} paused for {minutes}min")

    def resume(self, strategy: str) -> None:
        """Resume a paused strategy."""
        self._pause_until.pop(strategy, None)
        logger.info(f"Strategy {strategy} resumed")

    def get_throttle_summary(self) -> Dict[str, Any]:
        """Summary of all throttled strategies."""
        now = datetime.utcnow()
        paused = {
            s: (until - now).total_seconds() / 60
            for s, until in self._pause_until.items()
            if until > now
        }
        return {
            "n_paused": len(paused),
            "paused": {s: f"{mins:.0f}min remaining" for s, mins in paused.items()},
        }

    # ─── Internal ────────────────────────────────────────────────────────

    def _evaluate_single(
        self,
        strat: StrategyState,
        dd_pct: float,
        now: datetime,
    ) -> ThrottleAction:
        """Evaluate a single strategy."""

        # Check if currently paused
        if strat.name in self._pause_until and now < self._pause_until[strat.name]:
            return ThrottleAction(
                strategy=strat.name,
                action="PAUSE",
                reason="still paused",
                size_multiplier=0.0,
                duration_minutes=int(
                    (self._pause_until[strat.name] - now).total_seconds() / 60
                ),
                timestamp=now,
            )

        # Not enough trades → continue with caution
        if strat.n_trades < self.min_trades:
            return ThrottleAction(
                strategy=strat.name,
                action="CONTINUE",
                reason=f"insufficient data ({strat.n_trades}/{self.min_trades} trades)",
                size_multiplier=0.75,  # Slightly reduced size during probation
                duration_minutes=0,
                timestamp=now,
            )

        # STOP: slippage catastrophically high
        if strat.slippage_ratio >= self.SLIPPAGE_STOP:
            logger.warning(
                f"STOP {strat.name}: slippage {strat.slippage_ratio:.1f}x backtest"
            )
            return ThrottleAction(
                strategy=strat.name,
                action="STOP",
                reason=f"slippage {strat.slippage_ratio:.1f}x > {self.SLIPPAGE_STOP}x",
                size_multiplier=0.0,
                duration_minutes=0,  # Indefinite until manual review
                timestamp=now,
            )

        # PAUSE: very negative Sharpe
        if strat.sharpe_live < self.SHARPE_PAUSE:
            self._pause_until[strat.name] = now + timedelta(hours=4)
            return ThrottleAction(
                strategy=strat.name,
                action="PAUSE",
                reason=f"Sharpe={strat.sharpe_live:.2f} < {self.SHARPE_PAUSE}",
                size_multiplier=0.0,
                duration_minutes=240,
                timestamp=now,
            )

        # PAUSE: too many consecutive losses
        if strat.consecutive_losses >= self.CONSEC_LOSSES_PAUSE:
            self._pause_until[strat.name] = now + timedelta(hours=2)
            return ThrottleAction(
                strategy=strat.name,
                action="PAUSE",
                reason=f"{strat.consecutive_losses} consecutive losses",
                size_multiplier=0.0,
                duration_minutes=120,
                timestamp=now,
            )

        # PAUSE: terrible win rate
        if strat.n_trades >= 15 and strat.win_rate < self.WIN_RATE_MIN:
            self._pause_until[strat.name] = now + timedelta(hours=2)
            return ThrottleAction(
                strategy=strat.name,
                action="PAUSE",
                reason=f"win_rate={strat.win_rate:.0%} < {self.WIN_RATE_MIN:.0%}",
                size_multiplier=0.0,
                duration_minutes=120,
                timestamp=now,
            )

        # REDUCE: various degradation signals
        size_mult = 1.0
        reasons = []

        if strat.sharpe_live < self.SHARPE_REDUCE:
            size_mult *= 0.5
            reasons.append(f"Sharpe={strat.sharpe_live:.2f}")

        if strat.slippage_ratio >= self.SLIPPAGE_REDUCE:
            size_mult *= 0.7
            reasons.append(f"slippage={strat.slippage_ratio:.1f}x")

        if strat.consecutive_losses >= self.CONSEC_LOSSES_REDUCE:
            size_mult *= 0.7
            reasons.append(f"{strat.consecutive_losses} consec losses")

        # Cluster penalty
        if strat.is_in_cluster:
            if strat.cluster_level == "CRITICAL":
                size_mult *= self.CLUSTER_CRITICAL_SIZE_MULT
                reasons.append("CRITICAL cluster")
            elif strat.cluster_level == "WARNING":
                size_mult *= self.CLUSTER_WARNING_SIZE_MULT
                reasons.append("WARNING cluster")

        if size_mult < 1.0:
            return ThrottleAction(
                strategy=strat.name,
                action="REDUCE_SIZE",
                reason=", ".join(reasons),
                size_multiplier=max(0.2, size_mult),
                duration_minutes=30,
                timestamp=now,
            )

        return ThrottleAction(
            strategy=strat.name,
            action="CONTINUE",
            reason="all clear",
            size_multiplier=1.0,
            duration_minutes=0,
            timestamp=now,
        )
