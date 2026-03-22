"""
Strategy Research Agent — chargement de stratégies depuis fichiers JSON.

Mode de fonctionnement (sans API) :
  Les stratégies sont générées par Claude Code (conversation interactive),
  écrites dans strategies/*.json, et chargées ici par le ResearchAgent.

  Workflow :
    1. Marc demande à Claude Code : "génère une stratégie RSI pour DAX 5M"
    2. Claude Code écrit strategies/rsi_dax_5m_v1.json
    3. ResearchAgent.load_from_file() → validation → STRATEGY_READY

Mode API (optionnel, si ANTHROPIC_API_KEY défini) :
  Activer avec RESEARCH_MODE=api dans .env pour générer via LLM.
  Par défaut : RESEARCH_MODE=file (aucun appel API).

Flux :
  RESEARCH_REQUEST {strategy_file: "rsi_mean_reversion"} →
  Chargement + validation du JSON →
  STRATEGY_READY {strategy: dict}
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from agents.base_agent import BaseAgent, AgentMessage
from core.strategy_schema.validator import StrategyValidator, StrategyValidationError

logger = logging.getLogger(__name__)

STRATEGIES_DIR = Path(__file__).parent.parent.parent / "strategies"


class ResearchAgent(BaseAgent):
    """
    Charge et valide des stratégies depuis le dossier strategies/.

    En mode "file" (défaut) : lit les JSON écrits par Claude Code.
    En mode "api"           : génère via Anthropic API (nécessite ANTHROPIC_API_KEY).
    """

    def __init__(self, bus):
        super().__init__("research", bus)
        self._validator = StrategyValidator()
        self._mode = os.getenv("RESEARCH_MODE", "file").lower()

        if self._mode == "api":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning("RESEARCH_MODE=api mais ANTHROPIC_API_KEY absent — bascule en mode file")
                self._mode = "file"
            else:
                import anthropic
                self._client = anthropic.Anthropic(api_key=api_key)
                logger.info("ResearchAgent : mode API Anthropic actif")
        else:
            logger.info("ResearchAgent : mode FILE (Claude Code) actif")

    async def process(self, message: AgentMessage):
        if message.type != "RESEARCH_REQUEST":
            return

        if self._mode == "api":
            await self._process_api(message)
        else:
            await self._process_file(message)

    # ─── Mode FILE (défaut — Claude Code) ────────────────────────────────────

    async def _process_file(self, message: AgentMessage):
        """
        Charge une stratégie depuis strategies/*.json.
        Le fichier est créé par Claude Code dans la conversation.
        """
        payload = message.payload

        # Accepte soit un nom de fichier, soit un dict stratégie déjà chargé
        if "strategy" in payload:
            try:
                validated = self._validator.validate(payload["strategy"])
                self.logger.info(f"Stratégie inline validée : {validated['strategy_id']}")
                await self.emit("STRATEGY_READY", {"strategy": validated}, message.correlation_id)
                return
            except StrategyValidationError as e:
                await self.emit("RESEARCH_ERROR", {"error": str(e)}, message.correlation_id)
                return

        strategy_name = payload.get("strategy_file", "")
        if not strategy_name:
            # Charger toutes les stratégies disponibles
            await self._load_all(message.correlation_id)
            return

        # Résolution du chemin
        path = self._resolve_path(strategy_name)
        if not path:
            error = f"Stratégie introuvable : '{strategy_name}' dans {STRATEGIES_DIR}"
            self.logger.error(error)
            await self.emit("RESEARCH_ERROR", {"error": error}, message.correlation_id)
            return

        try:
            validated = self._validator.load_and_validate(path)
            self.logger.info(f"Stratégie chargée : {validated['strategy_id']} ({path.name})")
            await self.emit("STRATEGY_READY", {"strategy": validated}, message.correlation_id)
        except (FileNotFoundError, StrategyValidationError) as e:
            self.logger.error(f"Erreur chargement stratégie : {e}")
            await self.emit("RESEARCH_ERROR", {"error": str(e)}, message.correlation_id)

    async def _load_all(self, correlation_id: str):
        """Charge et émet toutes les stratégies JSON du dossier strategies/."""
        json_files = list(STRATEGIES_DIR.glob("*.json"))
        if not json_files:
            await self.emit("RESEARCH_ERROR", {
                "error": f"Aucun fichier JSON dans {STRATEGIES_DIR}"
            }, correlation_id)
            return

        loaded = 0
        for path in json_files:
            try:
                validated = self._validator.load_and_validate(path)
                await self.emit("STRATEGY_READY", {"strategy": validated}, correlation_id)
                loaded += 1
            except Exception as e:
                self.logger.warning(f"Ignoré {path.name} : {e}")

        self.logger.info(f"{loaded}/{len(json_files)} stratégies chargées")

    def _resolve_path(self, name: str) -> Path | None:
        """Résout le nom en chemin de fichier (avec ou sans .json)."""
        candidates = [
            STRATEGIES_DIR / name,
            STRATEGIES_DIR / f"{name}.json",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    # ─── Mode API (optionnel) ─────────────────────────────────────────────────

    async def _process_api(self, message: AgentMessage):
        """Génère une stratégie via l'API Anthropic et la sauvegarde localement."""
        payload = message.payload
        asset     = payload.get("asset", "EURUSD")
        timeframe = payload.get("timeframe", "1H")
        style     = payload.get("style", "mean_reversion")

        self.logger.info(f"[API] Generation strategie {style} pour {asset} {timeframe}")

        system_prompt = (
            "Tu es un expert en trading algorithmique quantitatif. "
            "Reponds UNIQUEMENT avec du JSON valide conforme au schema de strategie."
        )
        user_prompt = (
            f"Genere une strategie de type '{style}' pour {asset} en {timeframe}. "
            f"JSON complet uniquement."
        )

        try:
            response = self._client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()

            strategy_dict = json.loads(raw)
            validated = self._validator.validate(strategy_dict)

            # Sauvegarder pour réutilisation
            out_path = STRATEGIES_DIR / f"{validated['strategy_id']}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                clean = {k: v for k, v in validated.items() if k != "_fingerprint"}
                json.dump(clean, f, indent=2, ensure_ascii=False)

            self.logger.info(f"[API] Strategie generee et sauvegardee : {out_path.name}")
            await self.emit("STRATEGY_READY", {"strategy": validated}, message.correlation_id)

        except Exception as e:
            self.logger.error(f"[API] Erreur generation : {e}")
            await self.emit("RESEARCH_ERROR", {"error": str(e)}, message.correlation_id)

    # ─── Utilitaires ─────────────────────────────────────────────────────────

    @staticmethod
    def list_available() -> list[str]:
        """Liste les stratégies disponibles dans strategies/."""
        return [p.stem for p in STRATEGIES_DIR.glob("*.json")]
