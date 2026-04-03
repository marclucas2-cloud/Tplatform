"""U1-01: Capital Utilization Rate — measures % of capital working.

Definitions:
  NAV_total      = sum(equity per broker) = ~$45K
  Gross_exposure = sum(abs(position_value)) all brokers
  Utilization    = Gross_exposure / NAV_total

Targets by regime:
  TREND_STRONG: 70-85%, MEAN_REVERT: 60-80%, HIGH_VOL: 40-60%
  PANIC: 20-40%, LOW_LIQUIDITY: 30-50%, UNKNOWN: 50-70%
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("portfolio.utilization")


class UtilizationLevel(str, Enum):
    CRITICAL_LOW = "CRITICAL_LOW"    # < 10%
    LOW = "LOW"                      # < target_min
    NORMAL = "NORMAL"                # in target
    HIGH = "HIGH"                    # > target_max
    CRITICAL_HIGH = "CRITICAL_HIGH"  # > 95%


REGIME_TARGETS = {
    "TREND_STRONG":  {"min": 0.70, "max": 0.85},
    "MEAN_REVERT":   {"min": 0.60, "max": 0.80},
    "HIGH_VOL":      {"min": 0.40, "max": 0.60},
    "PANIC":         {"min": 0.20, "max": 0.40},
    "LOW_LIQUIDITY": {"min": 0.30, "max": 0.50},
    "UNKNOWN":       {"min": 0.50, "max": 0.70},
    "TRENDING_UP":   {"min": 0.65, "max": 0.85},
    "TRENDING_DOWN": {"min": 0.40, "max": 0.60},
    "RANGING":       {"min": 0.55, "max": 0.75},
    "VOLATILE":      {"min": 0.35, "max": 0.55},
    "BULL":          {"min": 0.65, "max": 0.85},
    "BEAR":          {"min": 0.30, "max": 0.50},
    "CHOP":          {"min": 0.45, "max": 0.65},
}


@dataclass
class UtilizationSnapshot:
    timestamp: datetime
    nav_total: float
    gross_exposure: float
    net_exposure: float
    cash_idle: float
    utilization_pct: float
    target_min_pct: float
    target_max_pct: float
    level: UtilizationLevel
    regime: str
    by_broker: Dict[str, float]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "nav_total": round(self.nav_total, 2),
            "gross_exposure": round(self.gross_exposure, 2),
            "net_exposure": round(self.net_exposure, 2),
            "cash_idle": round(self.cash_idle, 2),
            "utilization_pct": self.utilization_pct,
            "target_min_pct": self.target_min_pct,
            "target_max_pct": self.target_max_pct,
            "level": self.level.value,
            "regime": self.regime,
            "by_broker": {k: round(v, 1) for k, v in self.by_broker.items()},
        }


class UtilizationRateCalculator:
    """Calculates capital utilization across all brokers.

    Usage:
        calc = UtilizationRateCalculator()
        snap = calc.calculate(
            positions_by_broker={"binance": [...], "ibkr": [...]},
            equity_by_broker={"binance": 10000, "ibkr": 10000, "alpaca": 30000},
            current_regime="UNKNOWN",
        )
        if calc.should_alert_stopped():
            send_alert("Capital idle for 4h+!")
    """

    def __init__(self):
        self._history: list[UtilizationSnapshot] = []
        self._low_since: Optional[datetime] = None
        self._critical_low_since: Optional[datetime] = None

    def calculate(
        self,
        positions_by_broker: Dict[str, list],
        equity_by_broker: Dict[str, float],
        current_regime: str = "UNKNOWN",
    ) -> UtilizationSnapshot:
        nav_total = sum(equity_by_broker.values())
        if nav_total <= 0:
            nav_total = 1.0

        gross_exposure = 0.0
        net_exposure = 0.0
        by_broker = {}

        for broker, positions in positions_by_broker.items():
            broker_gross = sum(abs(p.get("value", p.get("notional", 0))) for p in positions)
            broker_net = sum(p.get("value", p.get("notional", 0)) for p in positions)
            gross_exposure += broker_gross
            net_exposure += broker_net
            broker_eq = equity_by_broker.get(broker, 1.0)
            by_broker[broker] = round(broker_gross / broker_eq * 100, 1) if broker_eq > 0 else 0

        # Add brokers with 0 positions
        for broker in equity_by_broker:
            if broker not in by_broker:
                by_broker[broker] = 0.0

        utilization = gross_exposure / nav_total
        cash_idle = max(0, nav_total - gross_exposure)

        targets = REGIME_TARGETS.get(current_regime, REGIME_TARGETS["UNKNOWN"])
        target_min = targets["min"]
        target_max = targets["max"]

        if utilization < 0.10:
            level = UtilizationLevel.CRITICAL_LOW
        elif utilization < target_min:
            level = UtilizationLevel.LOW
        elif utilization > 0.95:
            level = UtilizationLevel.CRITICAL_HIGH
        elif utilization > target_max:
            level = UtilizationLevel.HIGH
        else:
            level = UtilizationLevel.NORMAL

        now = datetime.now()
        if level in (UtilizationLevel.LOW, UtilizationLevel.CRITICAL_LOW):
            if self._low_since is None:
                self._low_since = now
            if level == UtilizationLevel.CRITICAL_LOW and self._critical_low_since is None:
                self._critical_low_since = now
        else:
            self._low_since = None
            self._critical_low_since = None

        snapshot = UtilizationSnapshot(
            timestamp=now,
            nav_total=nav_total,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            cash_idle=cash_idle,
            utilization_pct=round(utilization * 100, 1),
            target_min_pct=round(target_min * 100, 1),
            target_max_pct=round(target_max * 100, 1),
            level=level,
            regime=current_regime,
            by_broker=by_broker,
        )

        self._history.append(snapshot)
        if len(self._history) > 500:
            self._history.pop(0)

        return snapshot

    @property
    def hours_underinvested(self) -> float:
        if self._low_since is None:
            return 0.0
        return (datetime.now() - self._low_since).total_seconds() / 3600

    @property
    def hours_effectively_stopped(self) -> float:
        if self._critical_low_since is None:
            return 0.0
        return (datetime.now() - self._critical_low_since).total_seconds() / 3600

    def should_alert_underinvested(self) -> bool:
        return self.hours_underinvested > 4.0

    def should_alert_stopped(self) -> bool:
        return self.hours_effectively_stopped > 4.0

    @property
    def latest(self) -> Optional[UtilizationSnapshot]:
        return self._history[-1] if self._history else None
