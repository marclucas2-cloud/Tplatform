"""U1-02: Capital Deployment Monitor — identifies why capital isn't working.

Runs every 15 min in the worker. Identifies blocked strategies,
recommends corrective actions, alerts when capital is idle.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utilization_rate import UtilizationRateCalculator, UtilizationLevel
from .cash_drag import CashDragCalculator

logger = logging.getLogger("portfolio.deployment")


@dataclass
class BlockedStrategy:
    name: str
    reason: str
    blocked_since: str = ""
    broker: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "reason": self.reason,
                "blocked_since": self.blocked_since, "broker": self.broker}


@dataclass
class DeploymentReport:
    timestamp: str = ""
    utilization: Dict[str, Any] = field(default_factory=dict)
    by_broker: Dict[str, Dict] = field(default_factory=dict)
    blocked_strategies: List[BlockedStrategy] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    cash_drag: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "utilization": self.utilization,
            "by_broker": self.by_broker,
            "blocked_strategies": [b.to_dict() for b in self.blocked_strategies],
            "recommendations": self.recommendations,
            "cash_drag": self.cash_drag,
        }


class DeploymentMonitor:
    """Monitors capital deployment and identifies bottlenecks.

    Usage:
        monitor = DeploymentMonitor()
        report = monitor.check(
            positions_by_broker={...},
            equity_by_broker={...},
            regime="UNKNOWN",
            active_strategies=["fx_carry", "btc_momentum", ...],
        )
        if report.recommendations:
            send_alert(report)
    """

    def __init__(self, target_annual_return: float = 0.10):
        self.utilization_calc = UtilizationRateCalculator()
        self.cash_drag_calc = CashDragCalculator(target_annual_return)

    def check(
        self,
        positions_by_broker: Dict[str, list],
        equity_by_broker: Dict[str, float],
        regime: str = "UNKNOWN",
        active_strategies: List[str] = None,
        strategy_positions: Dict[str, list] = None,
    ) -> DeploymentReport:
        """Run deployment check."""
        report = DeploymentReport(timestamp=datetime.now().isoformat())

        # Utilization
        snap = self.utilization_calc.calculate(
            positions_by_broker, equity_by_broker, regime
        )
        report.utilization = snap.to_dict()

        # Cash drag
        drag = self.cash_drag_calc.calculate_daily_drag(
            snap.nav_total, snap.gross_exposure
        )
        report.cash_drag = drag.to_dict()

        # Per-broker breakdown
        for broker, eq in equity_by_broker.items():
            positions = positions_by_broker.get(broker, [])
            deployed = sum(abs(p.get("value", p.get("notional", 0))) for p in positions)
            report.by_broker[broker] = {
                "equity": round(eq, 2),
                "deployed": round(deployed, 2),
                "utilization_pct": round(deployed / eq * 100, 1) if eq > 0 else 0,
                "n_positions": len(positions),
            }

        # Identify blocked strategies
        if active_strategies and strategy_positions:
            for strat in active_strategies:
                strat_pos = strategy_positions.get(strat, [])
                if not strat_pos:
                    report.blocked_strategies.append(BlockedStrategy(
                        name=strat,
                        reason="No positions — check signal generation and funnel",
                    ))

        # Recommendations
        report.recommendations = self._generate_recommendations(snap, report)

        return report

    def _generate_recommendations(
        self,
        snap,
        report: DeploymentReport,
    ) -> List[str]:
        recs = []

        if snap.level == UtilizationLevel.CRITICAL_LOW:
            recs.append("CRITICAL: utilization < 10% — system effectively stopped")
            recs.append("Run signal_funnel_diagnostic.py to identify blockers")

        if snap.level == UtilizationLevel.LOW:
            hours = self.utilization_calc.hours_underinvested
            recs.append(
                f"Underinvested for {hours:.1f}h "
                f"(utilization {snap.utilization_pct:.0f}% vs target {snap.target_min_pct:.0f}%)"
            )

        # Per-broker recommendations
        for broker, info in report.by_broker.items():
            if info["utilization_pct"] == 0 and info["equity"] > 1000:
                recs.append(f"{broker}: $0 deployed on ${info['equity']:,.0f} equity")

        if len(report.blocked_strategies) > 3:
            recs.append(
                f"{len(report.blocked_strategies)} strategies with $0 deployed — "
                f"reduce active strats or increase sizing"
            )

        # Cash drag warning
        if report.cash_drag.get("daily_drag_usd", 0) > 5:
            recs.append(
                f"Cash drag: ${report.cash_drag['daily_drag_usd']:.2f}/day "
                f"(${report.cash_drag['annualized_drag_usd']:,.0f}/year)"
            )

        return recs

    def format_telegram(self, report: DeploymentReport) -> str:
        """Format report for Telegram."""
        u = report.utilization
        lines = [
            f"Portfolio — {u.get('regime', '?')}",
            f"NAV: ${u.get('nav_total', 0):,.0f} | "
            f"Deployed: ${u.get('gross_exposure', 0):,.0f} ({u.get('utilization_pct', 0):.0f}%)",
        ]

        level = u.get("level", "")
        if level in ("CRITICAL_LOW", "LOW"):
            lines.append(
                f"Utilization {u.get('utilization_pct', 0):.0f}% "
                f"[cible {u.get('target_min_pct', 0):.0f}-{u.get('target_max_pct', 0):.0f}%]"
            )

        drag = report.cash_drag
        if drag.get("daily_drag_usd", 0) > 1:
            lines.append(
                f"Cash drag: -${drag['daily_drag_usd']:.2f}/jour | "
                f"Cumul: -${drag.get('cumulative_drag_usd', 0):.2f}"
            )

        if report.recommendations:
            lines.append("")
            for rec in report.recommendations[:3]:
                lines.append(f"  {rec}")

        return "\n".join(lines)
