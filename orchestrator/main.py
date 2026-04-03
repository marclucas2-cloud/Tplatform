"""
Orchestrator central — cerveau de la plateforme.

Responsabilités :
  1. Démarrer et stopper tous les agents
  2. Router les messages entre agents via le bus central (asyncio.Queue)
  3. Maintenir le pipeline : Research → Backtest → Validation → Portfolio → Execution
  4. Gérer les erreurs et la reprise

Bus de messages :
  Chaque agent émet vers self.bus (Queue centrale).
  L'Orchestrator lit le bus et dispatch vers l'inbox du bon agent.

Table de routage :
  RESEARCH_REQUEST    → research.inbox
  STRATEGY_READY      → backtest.inbox
  BACKTEST_COMPLETE   → validation.inbox
  VALIDATION_PASSED   → portfolio.inbox
  ALLOCATION_READY    → execution.inbox
  EXECUTION_REPORT    → monitoring.inbox + portfolio.inbox
  PRICE_SIGNAL        → execution.inbox
  ALERT               → monitoring.inbox (log seulement)
  VALIDATION_FAILED   → monitoring.inbox (log + possible retry)
  RESEARCH_ERROR      → monitoring.inbox
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.backtest.agent import BacktestAgent
from agents.base_agent import AgentMessage
from agents.execution.agent import ExecutionAgent
from agents.monitoring.agent import MonitoringAgent
from agents.portfolio.agent import PortfolioManagerAgent
from agents.research.agent import ResearchAgent
from agents.validation.agent import ValidationAgent

logger = logging.getLogger(__name__)


# Table de routage : type de message → liste des agents destinataires
ROUTING_TABLE: dict[str, list[str]] = {
    "RESEARCH_REQUEST":  ["research"],
    "STRATEGY_READY":    ["backtest"],
    "BACKTEST_COMPLETE": ["validation"],
    "VALIDATION_PASSED": ["portfolio", "monitoring"],
    "VALIDATION_FAILED": ["monitoring"],
    "ALLOCATION_READY":  ["execution"],
    "EXECUTION_REPORT":  ["monitoring", "portfolio"],
    "PRICE_SIGNAL":      ["execution"],
    "ALERT":             ["monitoring"],
    "RESEARCH_ERROR":    ["monitoring"],
    "BACKTEST_REQUEST":  ["backtest"],
}


class Orchestrator:
    """
    Orchestrateur central — démarre les agents et route les messages.

    Usage :
        orch = Orchestrator()
        await orch.start()
        await orch.send("RESEARCH_REQUEST", {"asset": "EURUSD", "timeframe": "1H"})
        # ... attente
        await orch.stop()
    """

    def __init__(self, initial_capital: float = 10_000.0, ig_client=None):
        self.bus: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._running = False

        # Instanciation des agents
        self._agents = {
            "research":   ResearchAgent(bus=self.bus),
            "backtest":   BacktestAgent(bus=self.bus, initial_capital=initial_capital),
            "validation": ValidationAgent(bus=self.bus, initial_capital=initial_capital),
            "portfolio":  PortfolioManagerAgent(bus=self.bus, total_capital=initial_capital),
            "execution":  ExecutionAgent(bus=self.bus, ig_client=ig_client),
            "monitoring": MonitoringAgent(bus=self.bus),
        }

        self._router_task: asyncio.Task | None = None

    async def start(self):
        """Démarre tous les agents et le router."""
        logger.info("Orchestrator démarrage...")
        self._running = True

        for name, agent in self._agents.items():
            await agent.start()

        self._router_task = asyncio.create_task(self._route_loop(), name="orchestrator-router")
        logger.info(f"Orchestrator actif — {len(self._agents)} agents démarrés")

    async def stop(self):
        """Arrêt propre — attend que le bus soit vide."""
        logger.info("Orchestrator arrêt...")
        self._running = False

        # Attendre que les messages restants soient traités
        await asyncio.sleep(0.5)

        if self._router_task:
            self._router_task.cancel()
            try:
                await self._router_task
            except asyncio.CancelledError:
                pass

        for name, agent in self._agents.items():
            await agent.stop()

        logger.info("Orchestrator arrêté")

    async def send(self, msg_type: str, payload: dict, correlation_id: str = ""):
        """Envoie un message dans le bus (point d'entrée externe)."""
        msg = AgentMessage(
            type=msg_type,
            sender="orchestrator",
            payload=payload,
            correlation_id=correlation_id,
        )
        await self.bus.put(msg)
        logger.debug(f"Orchestrator → bus : {msg_type}")

    async def _route_loop(self):
        """
        Boucle de routage — lit le bus et dispatch vers les agents destinataires.
        C'est le cœur du système multi-agents.
        """
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.get(), timeout=0.5)
                destinations = ROUTING_TABLE.get(msg.type, [])

                if not destinations:
                    logger.warning(f"Message non routé : {msg.type} (sender={msg.sender})")
                else:
                    for dest in destinations:
                        agent = self._agents.get(dest)
                        if agent:
                            await agent.inbox.put(msg)
                            logger.debug(f"Router : {msg.type} → {dest}")

                self.bus.task_done()

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur routage : {e}", exc_info=True)

    def get_agent(self, name: str):
        """Accès direct à un agent (pour tests ou monitoring)."""
        return self._agents.get(name)

    def get_metrics(self) -> dict:
        """Proxy vers les métriques du Monitoring Agent."""
        monitoring = self._agents.get("monitoring")
        if monitoring:
            return monitoring.get_metrics()
        return {}

    async def run_backtest_only(self, strategy: dict, data_config: dict | None = None) -> dict:
        """
        Mode backtest standalone — sans démarrer tous les agents.
        Utile pour tests rapides et CI.
        """
        from core.backtest.engine import BacktestEngine
        from core.data.loader import OHLCVLoader

        engine = BacktestEngine(initial_capital=10_000.0)
        data_cfg = data_config or {}

        if data_cfg.get("source") == "csv":
            data = OHLCVLoader.from_csv(data_cfg["path"], strategy["asset"], strategy["timeframe"])
        else:
            data = OHLCVLoader.generate_synthetic(
                asset=strategy["asset"],
                timeframe=strategy["timeframe"],
                n_bars=data_cfg.get("n_bars", 3000),
            )

        result = engine.run(data, strategy)
        return result.to_dict()
