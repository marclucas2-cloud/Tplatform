"""U2-01: Cash Drag Calculator — explicit cost of idle capital.

Cash drag = cash_idle × target_annual_return / 365
Makes the cost of inaction VISIBLE in daily reports.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List

logger = logging.getLogger("portfolio.cash_drag")


@dataclass
class CashDragSnapshot:
    date: str
    cash_idle_usd: float
    utilization_pct: float
    daily_drag_usd: float
    cumulative_drag_usd: float
    annualized_drag_usd: float

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "cash_idle_usd": round(self.cash_idle_usd, 2),
            "utilization_pct": round(self.utilization_pct, 1),
            "daily_drag_usd": round(self.daily_drag_usd, 2),
            "cumulative_drag_usd": round(self.cumulative_drag_usd, 2),
            "annualized_drag_usd": round(self.annualized_drag_usd, 2),
        }


class CashDragCalculator:
    """Calculates the opportunity cost of uninvested capital.

    Usage:
        calc = CashDragCalculator(target_annual_return=0.10)
        result = calc.calculate_daily_drag(nav_total=45000, gross_exposure=5000)
        print(f"Cash drag: ${result.daily_drag_usd}/day")
    """

    def __init__(self, target_annual_return: float = 0.10):
        self.target_annual_return = target_annual_return
        self._cumulative_drag = 0.0
        self._history: List[CashDragSnapshot] = []

    def calculate_daily_drag(
        self,
        nav_total: float,
        gross_exposure: float,
    ) -> CashDragSnapshot:
        cash_idle = max(0, nav_total - gross_exposure)
        utilization = gross_exposure / nav_total if nav_total > 0 else 0
        daily_drag = cash_idle * self.target_annual_return / 365
        self._cumulative_drag += daily_drag

        snapshot = CashDragSnapshot(
            date=datetime.now().isoformat(),
            cash_idle_usd=cash_idle,
            utilization_pct=utilization * 100,
            daily_drag_usd=daily_drag,
            cumulative_drag_usd=self._cumulative_drag,
            annualized_drag_usd=daily_drag * 365,
        )

        self._history.append(snapshot)
        return snapshot

    @property
    def cumulative_drag(self) -> float:
        return self._cumulative_drag

    def format_telegram(self, snap: CashDragSnapshot) -> str:
        """Format for Telegram daily digest."""
        if snap.utilization_pct < 10:
            return (
                f"CAPITAL IDLE — ${snap.cash_idle_usd:,.0f} ne travaille pas\n"
                f"Cash drag: -${snap.daily_drag_usd:.2f}/jour = -${snap.annualized_drag_usd:,.0f}/an\n"
                f"Action requise : verifier le signal funnel"
            )
        return (
            f"Deployed: {snap.utilization_pct:.0f}% | "
            f"Cash drag: -${snap.daily_drag_usd:.2f}/jour | "
            f"Cumul: -${snap.cumulative_drag_usd:.2f}"
        )
