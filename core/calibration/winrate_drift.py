"""P6-02: Win Rate Drift Detector — detect degradation of strategy performance.

NEED_LIVE: Requires 30+ trades per strategy.

Detection:
  Z-score = (wr_live - wr_oos) / sqrt(wr_oos * (1-wr_oos) / n_trades)
  z < -1.5: LOG + monitoring
  z < -2.0: WARN + reduce Kelly 50%
  z < -3.0: PAUSE strategy + alert for manual review

Actions are automatic thresholds, not manual.
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)

MIN_TRADES_PER_STRAT = 30
CONSECUTIVE_ALERTS_BEFORE_ACTION = 3


class DriftLevel(str, Enum):
    OK = "OK"
    MONITOR = "MONITOR"       # z < -1.5
    WARNING = "WARNING"       # z < -2.0
    CRITICAL = "CRITICAL"     # z < -3.0


@dataclass
class DriftResult:
    """Win rate drift detection result for a strategy."""
    strategy: str
    oos_win_rate: float
    live_win_rate: float
    n_live_trades: int
    z_score: float
    level: DriftLevel
    consecutive_alerts: int = 0
    recommended_action: str = "NONE"
    kelly_multiplier: float = 1.0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "oos_win_rate": round(self.oos_win_rate, 3),
            "live_win_rate": round(self.live_win_rate, 3),
            "n_live_trades": self.n_live_trades,
            "z_score": round(self.z_score, 2),
            "level": self.level.value,
            "consecutive_alerts": self.consecutive_alerts,
            "recommended_action": self.recommended_action,
            "kelly_multiplier": round(self.kelly_multiplier, 2),
        }


@dataclass
class StrategyStats:
    """Running statistics for a strategy's live performance."""
    oos_win_rate: float
    live_wins: int = 0
    live_losses: int = 0
    consecutive_drift_alerts: int = 0
    last_z_scores: deque = field(default_factory=lambda: deque(maxlen=10))
    paused: bool = False

    @property
    def n_trades(self) -> int:
        return self.live_wins + self.live_losses

    @property
    def live_win_rate(self) -> float:
        if self.n_trades == 0:
            return 0.0
        return self.live_wins / self.n_trades


class WinRateDriftDetector:
    """Detects degradation in strategy win rates versus OOS expectations.

    NEED_LIVE: Requires 30+ trades per strategy.

    Usage:
        detector = WinRateDriftDetector()
        detector.register_strategy("fx_carry_vs", oos_win_rate=0.58)

        # Record each trade outcome:
        detector.record_trade("fx_carry_vs", won=True)
        detector.record_trade("fx_carry_vs", won=False)
        # ... after 30+ trades:

        result = detector.check("fx_carry_vs")
        if result.level == DriftLevel.CRITICAL:
            pause_strategy("fx_carry_vs")
    """

    def __init__(self, alert_callback: Callable | None = None):
        self._strategies: dict[str, StrategyStats] = {}
        self._alert_callback = alert_callback

    def register_strategy(self, strategy: str, oos_win_rate: float):
        """Register a strategy with its OOS win rate baseline."""
        self._strategies[strategy] = StrategyStats(oos_win_rate=oos_win_rate)

    def record_trade(self, strategy: str, won: bool):
        """Record a live trade outcome."""
        stats = self._strategies.get(strategy)
        if not stats:
            logger.warning("Strategy %s not registered for drift detection", strategy)
            return

        if won:
            stats.live_wins += 1
        else:
            stats.live_losses += 1

    def check(self, strategy: str) -> DriftResult:
        """Check for win rate drift on a strategy."""
        stats = self._strategies.get(strategy)
        if not stats:
            return DriftResult(
                strategy=strategy, oos_win_rate=0, live_win_rate=0,
                n_live_trades=0, z_score=0, level=DriftLevel.OK,
            )

        n = stats.n_trades
        if n < MIN_TRADES_PER_STRAT:
            return DriftResult(
                strategy=strategy,
                oos_win_rate=stats.oos_win_rate,
                live_win_rate=stats.live_win_rate,
                n_live_trades=n,
                z_score=0,
                level=DriftLevel.OK,
                details={"note": f"Only {n}/{MIN_TRADES_PER_STRAT} trades — too early"},
            )

        # Z-score
        p = stats.oos_win_rate
        se = math.sqrt(p * (1 - p) / n) if 0 < p < 1 else 0.01
        z = (stats.live_win_rate - p) / se if se > 0 else 0

        stats.last_z_scores.append(z)

        # Determine level
        if z < -3.0:
            level = DriftLevel.CRITICAL
        elif z < -2.0:
            level = DriftLevel.WARNING
        elif z < -1.5:
            level = DriftLevel.MONITOR
        else:
            level = DriftLevel.OK

        # Track consecutive alerts
        if level in (DriftLevel.WARNING, DriftLevel.CRITICAL):
            stats.consecutive_drift_alerts += 1
        else:
            stats.consecutive_drift_alerts = 0

        # Actions
        action = "NONE"
        kelly_mult = 1.0

        if level == DriftLevel.MONITOR:
            action = "LOG + enhanced monitoring"
            kelly_mult = 1.0
        elif level == DriftLevel.WARNING:
            action = "WARN + reduce Kelly 50%"
            kelly_mult = 0.5
        elif level == DriftLevel.CRITICAL:
            if stats.consecutive_drift_alerts >= CONSECUTIVE_ALERTS_BEFORE_ACTION:
                action = "PAUSE strategy + manual review"
                kelly_mult = 0.0
                stats.paused = True
            else:
                action = f"CRITICAL z-score ({stats.consecutive_drift_alerts}/{CONSECUTIVE_ALERTS_BEFORE_ACTION} consecutive)"
                kelly_mult = 0.25

        result = DriftResult(
            strategy=strategy,
            oos_win_rate=stats.oos_win_rate,
            live_win_rate=stats.live_win_rate,
            n_live_trades=n,
            z_score=z,
            level=level,
            consecutive_alerts=stats.consecutive_drift_alerts,
            recommended_action=action,
            kelly_multiplier=kelly_mult,
        )

        # Alert
        if level in (DriftLevel.WARNING, DriftLevel.CRITICAL):
            msg = (
                f"WIN RATE DRIFT: {strategy}\n"
                f"OOS: {stats.oos_win_rate:.1%} → Live: {stats.live_win_rate:.1%}\n"
                f"Z-score: {z:.2f} ({level.value})\n"
                f"Action: {action}"
            )
            logger.warning(msg)
            if self._alert_callback:
                self._alert_callback(msg, level="warning" if level == DriftLevel.WARNING else "critical")

        return result

    def check_all(self) -> dict[str, DriftResult]:
        """Check all registered strategies."""
        return {
            strat: self.check(strat)
            for strat in self._strategies
        }

    def get_paused_strategies(self) -> list[str]:
        """Get list of strategies paused due to drift."""
        return [
            strat for strat, stats in self._strategies.items()
            if stats.paused
        ]

    def unpause(self, strategy: str):
        """Unpause a strategy after manual review."""
        stats = self._strategies.get(strategy)
        if stats:
            stats.paused = False
            stats.consecutive_drift_alerts = 0
            logger.info("Strategy %s unpaused after manual review", strategy)
