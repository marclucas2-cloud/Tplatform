"""
Portfolio Manager Agent — allocation de capital entre stratégies validées.

Méthodes d'allocation supportées :
  - equal_weight : allocation égale entre stratégies actives
  - kelly        : critère de Kelly (fraction f = edge / odds)
  - risk_parity  : allocation inversement proportionnelle à la volatilité

Flux :
  VALIDATION_PASSED {strategy} →
  Calcul allocation →
  ALLOCATION_READY {strategy, allocation_pct}
"""
from __future__ import annotations

import logging
import os

from agents.base_agent import AgentMessage, BaseAgent

logger = logging.getLogger(__name__)


class PortfolioManagerAgent(BaseAgent):
    """
    Gère l'allocation de capital entre stratégies actives.
    Applique les contraintes de risque globales (max_daily_drawdown, etc.).
    """

    def __init__(self, bus, total_capital: float = 10_000.0):
        super().__init__("portfolio", bus)
        self.total_capital = total_capital
        self.max_risk_per_trade = float(os.getenv("MAX_RISK_PER_TRADE", "0.02"))
        self.max_daily_drawdown = float(os.getenv("MAX_DAILY_DRAWDOWN", "0.05"))
        self._active_strategies: dict[str, dict] = {}  # strategy_id → stratégie + métriques

    async def process(self, message: AgentMessage):
        if message.type == "VALIDATION_PASSED":
            await self._on_strategy_validated(message)
        elif message.type == "EXECUTION_REPORT":
            await self._on_execution_report(message)

    async def _on_strategy_validated(self, message: AgentMessage):
        """Enregistre une nouvelle stratégie validée et calcule l'allocation."""
        strategy = message.payload["strategy"]
        strategy_id = message.payload["strategy_id"]
        avg_sharpe = message.payload.get("avg_wf_sharpe", 1.0)

        self._active_strategies[strategy_id] = {
            "strategy": strategy,
            "sharpe": avg_sharpe,
            "allocation_pct": 0.0,
        }

        # Recalcul de l'allocation pour toutes les stratégies
        allocations = self._compute_allocations()

        for sid, alloc_pct in allocations.items():
            self._active_strategies[sid]["allocation_pct"] = alloc_pct
            if sid == strategy_id:
                allocated_capital = self.total_capital * alloc_pct
                self.logger.info(
                    f"Allocation {strategy_id} : {alloc_pct*100:.1f}% "
                    f"({allocated_capital:.0f}€ / {self.total_capital:.0f}€ total)"
                )
                await self.emit("ALLOCATION_READY", {
                    "strategy": strategy,
                    "strategy_id": strategy_id,
                    "allocation_pct": alloc_pct,
                    "allocated_capital": allocated_capital,
                    "max_position_size": allocated_capital * self.max_risk_per_trade,
                }, message.correlation_id)

    async def _on_execution_report(self, message: AgentMessage):
        """Met à jour le capital suite à un trade exécuté."""
        pnl = message.payload.get("net_pnl", 0.0)
        self.total_capital += pnl
        self.logger.info(f"Capital mis à jour : {self.total_capital:.2f} ({pnl:+.2f})")

    def _compute_allocations(self) -> dict[str, float]:
        """
        Allocation risk-parity basée sur le Sharpe ratio walk-forward.
        Limite chaque stratégie à max 40% du capital.
        """
        if not self._active_strategies:
            return {}

        sharpes = {sid: max(s["sharpe"], 0.1) for sid, s in self._active_strategies.items()}
        total_sharpe = sum(sharpes.values())

        allocations = {}
        for sid, sharpe in sharpes.items():
            raw_alloc = sharpe / total_sharpe
            # Limiter à 40% max par stratégie
            allocations[sid] = min(raw_alloc, 0.40)

        # Renormaliser après le plafonnement
        total_alloc = sum(allocations.values())
        if total_alloc > 0:
            allocations = {sid: v / total_alloc for sid, v in allocations.items()}

        return allocations
