"""
Classe de base pour tous les agents de la plateforme.

Chaque agent :
  - Reçoit des messages via sa queue d'entrée (asyncio.Queue)
  - Émet des événements vers le bus central de l'Orchestrator
  - Loggue toutes ses actions de façon structurée
  - Est stoppable proprement via stop()
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """
    Message échangé entre agents via le bus de l'Orchestrator.
    Toujours horodaté UTC pour reproductibilité.
    """
    type: str                    # Ex: "BACKTEST_REQUEST", "VALIDATION_RESULT"
    sender: str                  # Nom de l'agent émetteur
    payload: dict[str, Any]      # Données du message
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: str = ""     # Pour lier requête / réponse

    def __repr__(self):
        return f"AgentMessage(type={self.type}, sender={self.sender}, ts={self.timestamp})"


class BaseAgent(ABC):
    """
    Interface commune pour tous les agents.

    Cycle de vie :
      agent.start()  → lance la coroutine de traitement
      agent.stop()   → arrêt propre (attend fin du traitement en cours)

    Communication :
      agent.inbox    → asyncio.Queue — l'Orchestrator y dépose les messages
      agent.bus      → asyncio.Queue — l'agent y dépose ses événements sortants
    """

    def __init__(self, name: str, bus: asyncio.Queue):
        self.name = name
        self.bus = bus           # Bus central de l'Orchestrator
        self.inbox: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None
        self.logger = logging.getLogger(f"agent.{name}")

    async def start(self):
        """Démarre l'agent en arrière-plan."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"agent-{self.name}")
        self.logger.info(f"[{self.name}] démarré")

    async def stop(self):
        """Arrêt propre — attend que le message en cours soit traité."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.info(f"[{self.name}] arrêté")

    async def _run_loop(self):
        """Boucle principale — attend des messages et les traite."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self.inbox.get(), timeout=1.0)
                self.logger.debug(f"[{self.name}] reçoit {msg}")
                await self.process(msg)
                self.inbox.task_done()
            except asyncio.TimeoutError:
                continue  # Pas de message — vérifie _running et continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"[{self.name}] erreur lors du traitement : {e}", exc_info=True)

    async def emit(self, msg_type: str, payload: dict, correlation_id: str = ""):
        """Émet un message vers le bus central."""
        msg = AgentMessage(
            type=msg_type,
            sender=self.name,
            payload=payload,
            correlation_id=correlation_id,
        )
        await self.bus.put(msg)
        self.logger.debug(f"[{self.name}] émet {msg_type}")

    @abstractmethod
    async def process(self, message: AgentMessage):
        """Traiter un message entrant. Implémenter dans chaque agent."""
        ...
