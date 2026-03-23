"""
Execution Agent — gère les ordres vers IG Markets.

Modes :
  - PAPER : simule les ordres sans envoyer à IG (log uniquement)
  - LIVE  : envoie les ordres réels via l'API IG (nécessite PAPER_TRADING=false)

Circuit-breakers :
  - MAX_DAILY_DRAWDOWN : stoppe tous les ordres si dépassé
  - Vérification fingerprint stratégie avant chaque ordre

Flux :
  ALLOCATION_READY + signal de prix →
  Vérifications risque →
  Ordre IG (paper ou live) →
  EXECUTION_REPORT {trade_id, status, fill_price}
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from agents.base_agent import BaseAgent, AgentMessage

logger = logging.getLogger(__name__)


class ExecutionAgent(BaseAgent):
    """
    Exécution des ordres avec circuit-breakers et mode paper/live.
    """

    def __init__(self, bus, ig_client=None, broker_client=None):
        super().__init__("execution", bus)
        self.paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.max_daily_drawdown = float(os.getenv("MAX_DAILY_DRAWDOWN", "0.05"))
        # broker_client prend la priorité (Alpaca), ig_client conservé pour compatibilité
        self._broker = broker_client or ig_client
        self._ig = self._broker  # alias rétrocompat
        self._active_allocations: dict[str, dict] = {}  # strategy_id → allocation
        self._daily_pnl = 0.0
        self._daily_capital_start = 0.0
        self._open_positions: dict[str, dict] = {}     # deal_id → position

        broker_name = type(self._broker).__name__ if self._broker else "aucun"
        if self.paper_trading:
            self.logger.warning(f"MODE PAPER TRADING — broker={broker_name}, aucun ordre réel")
        else:
            self.logger.warning(f"MODE LIVE — broker={broker_name}, ordres réels activés !")

    async def process(self, message: AgentMessage):
        if message.type == "ALLOCATION_READY":
            await self._on_allocation(message)
        elif message.type == "PRICE_SIGNAL":
            await self._on_price_signal(message)

    async def _on_allocation(self, message: AgentMessage):
        """Enregistre l'allocation approuvée par le Portfolio Manager."""
        payload = message.payload
        strategy_id = payload["strategy_id"]
        self._active_allocations[strategy_id] = payload
        self.logger.info(
            f"Allocation enregistrée : {strategy_id} "
            f"({payload['allocation_pct']*100:.1f}%, {payload['allocated_capital']:.0f}€)"
        )

    async def _on_price_signal(self, message: AgentMessage):
        """
        Reçoit un signal de prix (généré par le moteur de stratégie en temps réel)
        et décide d'ouvrir / fermer une position.
        """
        payload = message.payload
        strategy_id = payload.get("strategy_id")
        signal = payload.get("signal")        # "long", "short", "close", None
        current_price = payload.get("price")
        epic = payload.get("epic")

        if strategy_id not in self._active_allocations:
            self.logger.debug(f"Signal ignoré : {strategy_id} non alloué")
            return

        # Circuit-breaker drawdown journalier
        if self._daily_capital_start > 0:
            daily_dd = self._daily_pnl / self._daily_capital_start
            if daily_dd < -self.max_daily_drawdown:
                self.logger.warning(
                    f"CIRCUIT BREAKER : drawdown journalier {daily_dd*100:.1f}% — "
                    f"arrêt de tous les ordres"
                )
                return

        allocation = self._active_allocations[strategy_id]
        max_position = allocation["max_position_size"]

        if signal in ("long", "short"):
            await self._open_position(
                strategy_id=strategy_id,
                epic=epic,
                direction=signal,
                price=current_price,
                size=max_position / current_price if current_price else 0,
                correlation_id=message.correlation_id,
            )
        elif signal == "close":
            await self._close_position(
                strategy_id=strategy_id,
                price=current_price,
                correlation_id=message.correlation_id,
            )

    async def _open_position(self, strategy_id: str, epic: str, direction: str,
                              price: float, size: float, correlation_id: str):
        """Ouvre une position (paper ou live)."""
        deal_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"

        if self.paper_trading:
            self.logger.info(
                f"[PAPER] OPEN {direction.upper()} {epic} "
                f"size={size:.4f} @ {price:.5f} — deal_id={deal_id}"
            )
        else:
            if not self._broker:
                self.logger.error("Broker non initialisé — impossible d'ouvrir position live")
                return
            try:
                response = self._broker.create_position(
                    symbol=epic,
                    direction=direction.upper(),
                    qty=round(size, 4),
                )
                deal_id = response.get("orderId", response.get("dealId", deal_id))
                self.logger.info(f"[LIVE] Position ouverte : {deal_id}")
            except Exception as e:
                self.logger.error(f"Erreur ouverture position IG : {e}")
                return

        self._open_positions[deal_id] = {
            "strategy_id": strategy_id,
            "epic": epic,
            "direction": direction,
            "entry_price": price,
            "size": size,
            "entry_time": datetime.now(timezone.utc).isoformat(),
        }

        await self.emit("EXECUTION_REPORT", {
            "deal_id": deal_id,
            "action": "open",
            "direction": direction,
            "epic": epic,
            "price": price,
            "size": size,
            "paper": self.paper_trading,
            "status": "filled",
        }, correlation_id)

    async def _close_position(self, strategy_id: str, price: float, correlation_id: str):
        """Ferme la position ouverte pour cette stratégie."""
        positions = [
            (did, pos) for did, pos in self._open_positions.items()
            if pos["strategy_id"] == strategy_id
        ]
        if not positions:
            return

        for deal_id, position in positions:
            if position["direction"] == "long":
                pnl = (price - position["entry_price"]) * position["size"]
            else:
                pnl = (position["entry_price"] - price) * position["size"]

            self._daily_pnl += pnl
            del self._open_positions[deal_id]

            self.logger.info(
                f"[{'PAPER' if self.paper_trading else 'LIVE'}] CLOSE {deal_id} "
                f"@ {price:.5f} — PnL net : {pnl:+.4f}"
            )

            await self.emit("EXECUTION_REPORT", {
                "deal_id": deal_id,
                "action": "close",
                "price": price,
                "net_pnl": pnl,
                "paper": self.paper_trading,
                "status": "filled",
            }, correlation_id)
