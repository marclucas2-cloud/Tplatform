"""
Monitoring Agent — métriques, alertes et circuit-breakers.

Responsabilités :
  - Consolider les EXECUTION_REPORT en métriques temps réel
  - Déclencher des alertes si seuils dépassés
  - Générer des rapports périodiques
  - Exposer les métriques (dict en mémoire — adapter pour Prometheus/Grafana en prod)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from agents.base_agent import BaseAgent, AgentMessage

logger = logging.getLogger(__name__)


class MonitoringAgent(BaseAgent):
    """
    Collecte et expose les métriques de performance en temps réel.
    """

    def __init__(self, bus):
        super().__init__("monitoring", bus)
        self._metrics: dict = {
            "total_trades": 0,
            "total_pnl": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "strategies": defaultdict(lambda: {
                "trades": 0,
                "pnl": 0.0,
                "wins": 0,
                "losses": 0,
            }),
            "events": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_update": None,
        }

    async def process(self, message: AgentMessage):
        if message.type == "EXECUTION_REPORT":
            await self._on_execution(message)
        elif message.type == "VALIDATION_PASSED":
            self._log_event("strategy_validated", message.payload.get("strategy_id"))
        elif message.type == "VALIDATION_FAILED":
            self._log_event("strategy_rejected", message.payload.get("strategy_id"),
                            extra=message.payload.get("reason", ""))
        elif message.type == "BACKTEST_COMPLETE":
            result = message.payload.get("result", {})
            self._log_event("backtest_complete", result.get("strategy_id"),
                            extra=f"Sharpe={result.get('sharpe_ratio', 0):.3f}")

    async def _on_execution(self, message: AgentMessage):
        payload = message.payload
        if payload.get("action") != "close":
            return

        pnl = payload.get("net_pnl", 0.0)
        deal_id = payload.get("deal_id", "")

        self._metrics["total_trades"] += 1
        self._metrics["total_pnl"] += pnl
        self._metrics["last_update"] = datetime.now(timezone.utc).isoformat()

        if pnl > 0:
            self._metrics["winning_trades"] += 1
        else:
            self._metrics["losing_trades"] += 1

        self._log_event("trade_closed", deal_id, extra=f"PnL={pnl:+.4f}")
        self.logger.info(self._format_dashboard())

        # Alerte si pertes importantes
        if pnl < -50:
            self.logger.warning(f"ALERTE : perte significative {pnl:+.4f} sur {deal_id}")
            await self.emit("ALERT", {
                "type": "large_loss",
                "deal_id": deal_id,
                "pnl": pnl,
            })

    def _log_event(self, event_type: str, ref: str = "", extra: str = ""):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "ref": ref,
            "extra": extra,
        }
        self._metrics["events"].append(entry)
        # Limiter l'historique en mémoire
        if len(self._metrics["events"]) > 500:
            self._metrics["events"] = self._metrics["events"][-500:]

    def _format_dashboard(self) -> str:
        m = self._metrics
        total = m["total_trades"]
        wins = m["winning_trades"]
        win_rate = (wins / total * 100) if total > 0 else 0
        return (
            f"\n{'─'*50}\n"
            f"  MONITORING — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n"
            f"{'─'*50}\n"
            f"  Trades total  : {total}\n"
            f"  PnL total     : {m['total_pnl']:+.4f}\n"
            f"  Win rate      : {win_rate:.1f}%\n"
            f"{'─'*50}"
        )

    def get_metrics(self) -> dict:
        """Expose les métriques — pour API REST ou dashboard futur."""
        return dict(self._metrics)
