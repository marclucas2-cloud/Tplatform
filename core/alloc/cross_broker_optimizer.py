"""U7-01: Cross-Broker Capital Optimizer — recommends capital transfers.

Does NOT auto-transfer (transfers are manual/slow). Only recommends.
Max 1 recommendation per week to avoid spam.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("alloc.cross_broker")


@dataclass
class TransferRecommendation:
    from_broker: str
    to_broker: str
    amount_usd: float
    reason: str
    timestamp: str = ""
    status: str = "PENDING"  # PENDING, EXECUTED, IGNORED

    def to_dict(self) -> dict:
        return {
            "from": self.from_broker,
            "to": self.to_broker,
            "amount": round(self.amount_usd, 0),
            "reason": self.reason,
            "timestamp": self.timestamp,
            "status": self.status,
        }


class CrossBrokerOptimizer:
    """Recommends capital transfers between brokers based on utilization.

    Constraint: inter-broker transfers are manual (bank wire, 2-3 days).
    This module only RECOMMENDS — never auto-transfers.

    Usage:
        optimizer = CrossBrokerOptimizer()
        recs = optimizer.analyze(
            equity={"binance": 10000, "ibkr": 10000, "alpaca": 30000},
            utilization={"binance": 5, "ibkr": 15, "alpaca": 0},
            n_active_strats={"binance": 4, "ibkr": 4, "alpaca": 4},
        )
    """

    def __init__(self, min_surplus: float = 2000, cooldown_days: int = 7):
        self._min_surplus = min_surplus
        self._cooldown = timedelta(days=cooldown_days)
        self._last_recommendation: datetime | None = None
        self._history: List[TransferRecommendation] = []

    def analyze(
        self,
        equity: Dict[str, float],
        utilization: Dict[str, float],
        n_active_strats: Dict[str, float] = None,
    ) -> List[TransferRecommendation]:
        """Analyze and generate recommendations."""
        n_active_strats = n_active_strats or {}

        # Cooldown check
        if self._last_recommendation:
            if datetime.now() - self._last_recommendation < self._cooldown:
                return []

        recs = []
        total_equity = sum(equity.values())

        # Find over/under-capitalized brokers
        for broker, eq in equity.items():
            util = utilization.get(broker, 0)
            n_strats = n_active_strats.get(broker, 0)

            # Optimal capital = proportional to number of active strategies
            total_strats = sum(n_active_strats.values()) or 1
            optimal = total_equity * (n_strats / total_strats)

            surplus = eq - optimal

            if surplus > self._min_surplus and util < 20:
                # This broker has too much capital and isn't using it
                # Find the most under-capitalized broker
                for other_broker, other_eq in equity.items():
                    if other_broker == broker:
                        continue
                    other_optimal = total_equity * (n_active_strats.get(other_broker, 0) / total_strats)
                    if other_eq < other_optimal - self._min_surplus:
                        transfer = min(surplus, other_optimal - other_eq)
                        if transfer >= self._min_surplus:
                            recs.append(TransferRecommendation(
                                from_broker=broker,
                                to_broker=other_broker,
                                amount_usd=transfer,
                                reason=(
                                    f"{broker} utilization {util:.0f}% with ${eq:,.0f}, "
                                    f"{other_broker} could use ${transfer:,.0f} more"
                                ),
                                timestamp=datetime.now().isoformat(),
                            ))

        if recs:
            self._last_recommendation = datetime.now()
            self._history.extend(recs)

        return recs
