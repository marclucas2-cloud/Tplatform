"""P4-04: Cross-Timezone Capital Efficiency — reuse idle capital across sessions.

Analysis:
  00h-09h CET:  FX + crypto (20% IBKR, 40% crypto)
  09h-15h30:    EU + FX + crypto (40% IBKR, 50% crypto)
  15h30-17h30:  OVERLAP all active (70% IBKR, 60% crypto)
  17h30-22h:    US + FX + crypto (60% Alpaca, 50% crypto)
  22h-00h:      FX + crypto (25% IBKR, 30% crypto)

Goal: maximize capital utilization without increasing risk.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionWindow:
    """A trading session window."""
    name: str
    start_hour_cet: int
    end_hour_cet: int
    markets: list[str]
    utilization: dict[str, float]  # broker -> % capital active


# Session definitions (CET hours)
SESSIONS = [
    SessionWindow(
        name="ASIA_FX",
        start_hour_cet=0, end_hour_cet=9,
        markets=["fx", "crypto"],
        utilization={"ibkr": 0.20, "binance": 0.40, "alpaca": 0.0},
    ),
    SessionWindow(
        name="EU",
        start_hour_cet=9, end_hour_cet=15,
        markets=["eu_equity", "fx", "crypto"],
        utilization={"ibkr": 0.40, "binance": 0.50, "alpaca": 0.0},
    ),
    SessionWindow(
        name="OVERLAP",
        start_hour_cet=15, end_hour_cet=17,
        markets=["eu_equity", "us_equity", "fx", "crypto", "futures"],
        utilization={"ibkr": 0.70, "binance": 0.60, "alpaca": 0.60},
    ),
    SessionWindow(
        name="US",
        start_hour_cet=17, end_hour_cet=22,
        markets=["us_equity", "fx", "crypto", "futures"],
        utilization={"ibkr": 0.30, "binance": 0.50, "alpaca": 0.60},
    ),
    SessionWindow(
        name="EVENING",
        start_hour_cet=22, end_hour_cet=24,
        markets=["fx", "crypto"],
        utilization={"ibkr": 0.25, "binance": 0.30, "alpaca": 0.0},
    ),
]


@dataclass
class TimezoneAllocation:
    """Recommended allocation for the current session."""
    session: str
    active_markets: list[str]
    broker_utilization: dict[str, float]
    reallocation: dict[str, dict[str, float]]  # broker -> {from_bucket: pct_to_move}
    idle_capital: dict[str, float]  # broker -> $ idle
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session": self.session,
            "active_markets": self.active_markets,
            "broker_utilization": {
                k: round(v, 2) for k, v in self.broker_utilization.items()
            },
            "idle_capital": {k: round(v, 2) for k, v in self.idle_capital.items()},
            "reallocation": self.reallocation,
            "recommendations": self.recommendations,
        }


class TimezoneAllocator:
    """Manages cross-timezone capital efficiency.

    Usage:
        tz = TimezoneAllocator(
            capital={"ibkr": 10000, "binance": 10000, "alpaca": 30000}
        )
        alloc = tz.get_current_allocation()
        print(alloc.idle_capital)  # Which capital is sleeping
        print(alloc.recommendations)
    """

    def __init__(
        self,
        capital: dict[str, float] | None = None,
        max_reallocation_pct: float = 0.20,  # Max 20% shift per session
    ):
        self._capital = capital or {"ibkr": 10000, "binance": 10000, "alpaca": 30000}
        self._max_realloc = max_reallocation_pct

    def get_current_session(self, timestamp: datetime | None = None) -> SessionWindow:
        """Get the current trading session."""
        now = timestamp or datetime.now(timezone.utc)
        cet_hour = (now.hour + 1) % 24  # UTC -> CET approximation

        for session in SESSIONS:
            if session.start_hour_cet <= cet_hour < session.end_hour_cet:
                return session

        return SESSIONS[0]  # Default to ASIA_FX

    def get_current_allocation(
        self,
        timestamp: datetime | None = None,
        current_positions: dict[str, float] | None = None,
    ) -> TimezoneAllocation:
        """Get recommended allocation for the current session."""
        session = self.get_current_session(timestamp)

        # Calculate idle capital per broker
        idle = {}
        for broker, capital in self._capital.items():
            util = session.utilization.get(broker, 0)
            deployed = capital * util
            idle[broker] = capital - deployed

        # Find reallocation opportunities
        reallocation = {}
        recommendations = []

        total_idle = sum(idle.values())
        total_capital = sum(self._capital.values())
        idle_pct = total_idle / total_capital if total_capital > 0 else 0

        if idle_pct > 0.50:
            recommendations.append(
                f"WARNING: {idle_pct:.0%} capital idle during {session.name} session"
            )

        # Specific recommendations by session
        if session.name == "ASIA_FX":
            if idle.get("alpaca", 0) > 0:
                recommendations.append(
                    f"Alpaca ${idle['alpaca']:.0f} idle — no US market. "
                    f"Consider FX overnight carry via IBKR."
                )
            if idle.get("ibkr", 0) > self._capital.get("ibkr", 0) * 0.5:
                recommendations.append(
                    "IBKR mostly idle — FX carry positions use minimal margin."
                )

        elif session.name == "EU":
            if idle.get("alpaca", 0) > 0:
                recommendations.append(
                    f"Alpaca ${idle['alpaca']:.0f} waiting for US open."
                )

        elif session.name == "OVERLAP":
            recommendations.append(
                "OVERLAP session — all brokers active. "
                "Best time for rebalancing and high-conviction trades."
            )

        elif session.name == "US":
            if idle.get("ibkr", 0) > self._capital.get("ibkr", 0) * 0.5:
                recommendations.append(
                    "IBKR EU market closed — FX + futures still available."
                )

        # Crypto always active suggestion
        crypto_idle = idle.get("binance", 0)
        if crypto_idle > self._capital.get("binance", 0) * 0.3:
            recommendations.append(
                f"Binance ${crypto_idle:.0f} idle — consider Earn for passive yield."
            )

        return TimezoneAllocation(
            session=session.name,
            active_markets=session.markets,
            broker_utilization=dict(session.utilization),
            reallocation=reallocation,
            idle_capital=idle,
            recommendations=recommendations,
        )

    def get_daily_utilization(self) -> dict[str, float]:
        """Compute weighted average daily utilization per broker."""
        utilization = {}
        for broker in self._capital:
            weighted_hours = 0
            for session in SESSIONS:
                hours = session.end_hour_cet - session.start_hour_cet
                util = session.utilization.get(broker, 0)
                weighted_hours += hours * util
            utilization[broker] = round(weighted_hours / 24, 3)
        return utilization

    def get_efficiency_report(self) -> dict[str, Any]:
        """Full efficiency analysis."""
        daily_util = self.get_daily_utilization()
        total_capital = sum(self._capital.values())

        weighted_util = sum(
            util * self._capital[broker] / total_capital
            for broker, util in daily_util.items()
        )

        sessions_report = []
        for session in SESSIONS:
            hours = session.end_hour_cet - session.start_hour_cet
            total_active = sum(
                self._capital.get(b, 0) * session.utilization.get(b, 0)
                for b in self._capital
            )
            sessions_report.append({
                "session": session.name,
                "hours": hours,
                "markets": session.markets,
                "active_capital": round(total_active, 0),
                "utilization_pct": round(total_active / total_capital * 100, 1),
            })

        return {
            "total_capital": total_capital,
            "daily_utilization_by_broker": daily_util,
            "weighted_portfolio_utilization": round(weighted_util, 3),
            "sessions": sessions_report,
            "improvement_opportunity_pct": round((1 - weighted_util) * 100, 1),
        }
