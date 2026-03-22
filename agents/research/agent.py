"""
Strategy Research Agent — génère des idées de stratégies via LLM.

RÈGLE CRITIQUE : cet agent produit UNIQUEMENT du JSON de paramètres.
Il ne fait AUCUN calcul quantitatif. La séparation IA/calcul est stricte.

Flux :
  RESEARCH_REQUEST {asset, timeframe, context} →
  Appel Anthropic API →
  Validation JSON Schema →
  STRATEGY_READY {strategy: dict}
"""
from __future__ import annotations

import json
import logging
import os

import anthropic

from agents.base_agent import BaseAgent, AgentMessage
from core.strategy_schema.validator import StrategyValidator, StrategyValidationError

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
Tu es un expert en trading algorithmique quantitatif.
Ta SEULE mission : générer un JSON de stratégie de trading STRICTEMENT conforme au schéma fourni.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT avec du JSON valide — aucun texte avant ou après
2. Le strategy_id doit être snake_case avec suffixe _v1, ex: "rsi_mean_reversion_v1"
3. Les paramètres sont des nombres — JAMAIS de formules, JAMAIS de code
4. entry_rules et exit_rules sont du pseudo-code lisible, PAS du Python
5. cost_model doit refléter des coûts RÉELS (spread typique pour l'actif)
6. validation_requirements doit être conservateur (min_trades >= 50)

NE PAS inclure de lookahead dans les règles (pas de "futur", "prochain", etc.)
"""


class ResearchAgent(BaseAgent):
    """
    Génère des stratégies de trading via l'API Anthropic.
    Valide le JSON produit avant de le transmettre.
    """

    def __init__(self, bus):
        super().__init__("research", bus)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY manquant dans l'environnement")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._validator = StrategyValidator()

    async def process(self, message: AgentMessage):
        if message.type != "RESEARCH_REQUEST":
            return

        payload = message.payload
        asset = payload.get("asset", "EURUSD")
        timeframe = payload.get("timeframe", "1H")
        style = payload.get("style", "mean_reversion")  # ou "breakout", "trend_following"
        context = payload.get("context", "")

        self.logger.info(f"Génération stratégie {style} pour {asset} {timeframe}")

        schema_hint = json.dumps({
            "strategy_id": f"{style}_v1",
            "asset": asset,
            "timeframe": timeframe,
        }, indent=2)

        user_prompt = f"""
Génère une stratégie de trading de type "{style}" pour {asset} en {timeframe}.
{f"Contexte additionnel : {context}" if context else ""}

La stratégie doit être adaptée au timeframe {timeframe} et aux caractéristiques de {asset}.
Utilise des paramètres raisonnables et des seuils de validation conservateurs.

Réponds UNIQUEMENT avec le JSON complet.
"""
        try:
            response = self._client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = response.content[0].text.strip()

            # Nettoyage si le LLM a ajouté des backticks markdown
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            strategy_dict = json.loads(raw_text)
            validated = self._validator.validate(strategy_dict)

            self.logger.info(f"Stratégie générée et validée : {validated['strategy_id']}")
            await self.emit("STRATEGY_READY", {"strategy": validated}, message.correlation_id)

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON invalide produit par le LLM : {e}")
            await self.emit("RESEARCH_ERROR", {"error": str(e), "raw": raw_text[:200]}, message.correlation_id)

        except StrategyValidationError as e:
            self.logger.error(f"Stratégie LLM ne valide pas le schéma : {e}")
            await self.emit("RESEARCH_ERROR", {"error": str(e)}, message.correlation_id)
