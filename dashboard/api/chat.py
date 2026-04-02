"""
Chat endpoint — Haiku 4.5 assistant for trading dashboard.

Injects live portfolio context into the system prompt so Haiku
can answer questions about positions, signals, risk, and brokers.
"""
import os
import sys
import json
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

logger = logging.getLogger("dashboard-chat")

ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Rate limit: max 1 request per 2 seconds per user
_last_request = {}
RATE_LIMIT_SECONDS = 2


class ChatRequest(BaseModel):
    message: str
    history: list = []  # [{role, content}, ...]


class ChatResponse(BaseModel):
    response: str
    context_used: list = []


def _get_live_context() -> str:
    """Build live context string from portfolio data."""
    parts = []

    # System health
    try:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "dashboard" / "api"))

        # Broker connectivity
        import socket
        ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
        ibkr_port = int(os.environ.get("IBKR_PORT", "4002"))
        ibkr_ok = False
        try:
            with socket.create_connection((ibkr_host, ibkr_port), timeout=2):
                ibkr_ok = True
        except Exception:
            pass

        # Binance
        binance_info = {}
        try:
            from core.broker.binance_broker import BinanceBroker
            bnb = BinanceBroker()
            binance_info = bnb.get_account_info()
        except Exception:
            pass

        # Alpaca
        alpaca_info = {}
        try:
            from core.alpaca_client.client import AlpacaClient
            ac = AlpacaClient.from_env()
            alpaca_info = ac.get_account_info()
        except Exception:
            pass

        parts.append(f"""ETAT LIVE ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}):
- Binance: equity=${binance_info.get('equity', 'N/A')}, spot=${binance_info.get('spot_total_usd', 'N/A')}, earn=${binance_info.get('earn_total_usd', 'N/A')}
- IBKR: {'connecte' if ibkr_ok else 'deconnecte (weekend)'}, port {ibkr_port}
- Alpaca: equity=${alpaca_info.get('equity', 'N/A')} ({'PAPER' if alpaca_info.get('paper', True) else 'LIVE'})
- Worker: actif sur Hetzner VPS""")

    except Exception as e:
        parts.append(f"Erreur contexte brokers: {e}")

    # Recent worker signals
    try:
        import subprocess
        result = subprocess.run(
            ["journalctl", "-u", "trading-worker", "--no-pager", "-n", "30"],
            capture_output=True, text=True, timeout=5
        )
        signals = [l for l in result.stdout.split("\n") if "SIGNAL" in l or "signal" in l.lower() or "pas de signal" in l]
        if signals:
            parts.append("DERNIERS SIGNAUX:\n" + "\n".join(signals[-8:]))
    except Exception:
        pass

    # Portfolio state
    try:
        state_file = ROOT / "paper_portfolio_state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            positions = state.get("intraday_positions", {})
            if positions:
                parts.append(f"POSITIONS ALPACA: {json.dumps(positions, indent=2)[:500]}")
            else:
                parts.append("POSITIONS ALPACA: aucune position ouverte")
    except Exception:
        pass

    return "\n\n".join(parts)


SYSTEM_PROMPT = """Tu es l'assistant IA du dashboard de trading de Marc. Tu reponds en francais, de facon concise et precise.

Tu as acces aux donnees LIVE du portfolio. Voici le contexte actuel:

{context}

REGLES:
- Reponds en francais, court et direct
- Utilise les donnees live ci-dessus pour repondre
- Si tu ne sais pas, dis-le
- Ne fabrique pas de donnees
- Pour les montants, utilise $ ou EUR selon le broker
- Tu peux expliquer les strategies, le risk management, les signaux
- Si on te demande d'executer un trade, refuse (read-only)

INFOS SYSTEME:
- 46 strategies: 12 crypto (Binance), 15 FX/EU (IBKR), 7 US (Alpaca paper), 8 futures
- 14 live lundi: 8 crypto + 6 FX/EU
- Risk V10: correlation engine, ERE, kill switch, safety mode
- Worker cycle: crypto 15min, FX/EU 5min quand marche ouvert
- Dashboard: trading.aucoeurdeville-laval.fr"""


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request = None):
    """Chat with Haiku about the trading platform."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ChatResponse(response="ANTHROPIC_API_KEY non configure sur le serveur.")

    # Rate limit per client IP (was global — one user could block others)
    now = time.time()
    client_ip = "default"
    try:
        if request:
            client_ip = request.client.host or "default"
    except Exception:
        pass
    if now - _last_request.get(client_ip, 0) < RATE_LIMIT_SECONDS:
        return ChatResponse(response="Trop de requetes. Attends quelques secondes.")
    _last_request[client_ip] = now

    # Build context
    context = _get_live_context()
    system = SYSTEM_PROMPT.format(context=context)

    # Build messages
    messages = []
    for h in req.history[-10:]:  # Keep last 10 messages
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": req.message})

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        text = response.content[0].text
        return ChatResponse(
            response=text,
            context_used=["brokers", "signals", "positions"],
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return ChatResponse(response=f"Erreur API: {str(e)[:200]}")
