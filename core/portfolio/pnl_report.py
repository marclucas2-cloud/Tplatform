"""U2-03: PnL Report with Cash Drag — shows true economic cost of inaction.

Two modes:
  GROSS: PnL classique (trades gagnants - trades perdants - commissions)
  NET_ADJUSTED: PnL avec cash drag (realite economique)
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("portfolio.pnl")


@dataclass
class PnLReport:
    """PnL report with optional cash drag adjustment."""
    period: str  # "daily", "weekly", "monthly"
    gross_pnl: float = 0.0
    commissions: float = 0.0
    slippage_cost: float = 0.0
    cash_drag: float = 0.0
    net_pnl_gross: float = 0.0      # Without cash drag
    net_pnl_adjusted: float = 0.0    # With cash drag
    n_trades: int = 0
    utilization_avg_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "gross_pnl": round(self.gross_pnl, 2),
            "commissions": round(self.commissions, 2),
            "slippage_cost": round(self.slippage_cost, 2),
            "cash_drag": round(self.cash_drag, 2),
            "net_pnl_gross": round(self.net_pnl_gross, 2),
            "net_pnl_adjusted": round(self.net_pnl_adjusted, 2),
            "n_trades": self.n_trades,
            "utilization_avg_pct": round(self.utilization_avg_pct, 1),
        }

    def format_telegram(self) -> str:
        lines = [
            f"PnL {self.period}:",
            f"  Gross: ${self.gross_pnl:+.2f}",
            f"  Commissions: -${self.commissions:.2f}",
            f"  Net (gross): ${self.net_pnl_gross:+.2f}",
        ]
        if self.cash_drag > 0:
            lines.append(f"  Cash drag: -${self.cash_drag:.2f}")
            lines.append(f"  Net (adjusted): ${self.net_pnl_adjusted:+.2f}")
        lines.append(f"  Trades: {self.n_trades} | Util: {self.utilization_avg_pct:.0f}%")
        return "\n".join(lines)


def compute_pnl_report(
    trades: list[dict],
    cash_drag_total: float = 0.0,
    utilization_avg: float = 0.0,
    period: str = "daily",
) -> PnLReport:
    """Compute PnL report from a list of trades.

    Each trade dict: {pnl_gross, commission, slippage_cost}
    """
    gross = sum(t.get("pnl_gross", 0) for t in trades)
    comms = sum(t.get("commission", 0) for t in trades)
    slip = sum(t.get("slippage_cost", 0) for t in trades)

    net_gross = gross - comms - slip
    net_adjusted = net_gross - cash_drag_total

    return PnLReport(
        period=period,
        gross_pnl=gross,
        commissions=comms,
        slippage_cost=slip,
        cash_drag=cash_drag_total,
        net_pnl_gross=net_gross,
        net_pnl_adjusted=net_adjusted,
        n_trades=len(trades),
        utilization_avg_pct=utilization_avg,
    )
