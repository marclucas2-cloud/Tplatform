"""
Chat endpoint — Haiku 4.5 assistant for trading dashboard.

Injects FULL live portfolio context so the assistant can answer
questions about positions, signals, risk, trades, strategies.
"""
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger("dashboard-chat")

ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter(prefix="/api/chat", tags=["chat"])

_last_request = {}
RATE_LIMIT_SECONDS = 5


class ChatRequest(BaseModel):
    message: str
    history: list = []


class ChatResponse(BaseModel):
    response: str
    context_used: list = []


def _get_live_context() -> str:
    """Build comprehensive live context from ALL data sources."""
    parts = []
    context_keys = []

    # 1. Broker connectivity + equity
    try:
        sys.path.insert(0, str(ROOT))
        import socket
        ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
        ibkr_port = int(os.environ.get("IBKR_PORT", "4002"))
        ibkr_ok = False
        try:
            with socket.create_connection((ibkr_host, ibkr_port), timeout=2):
                ibkr_ok = True
        except Exception:
            pass

        # IBKR equity from snapshot
        ibkr_equity = 0
        snap_dir = ROOT / "logs" / "portfolio"
        if snap_dir.exists():
            import glob
            files = sorted(glob.glob(str(snap_dir / "*.jsonl")), reverse=True)
            for fpath in files[:2]:
                try:
                    with open(fpath) as f:
                        lines = f.readlines()
                    for line in reversed(lines[-5:]):
                        snap = json.loads(line.strip())
                        for b in snap.get("portfolio", {}).get("brokers", []):
                            if b.get("broker") == "ibkr":
                                ibkr_equity = float(b.get("equity", 0))
                        if ibkr_equity > 0:
                            break
                except Exception:
                    continue

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

        bnb_eq = binance_info.get('equity', 'N/A')
        parts.append(f"""BROKERS ({datetime.now(UTC).strftime('%H:%M UTC')}):
- IBKR: {'connecte' if ibkr_ok else 'deconnecte'}, equity=EUR {ibkr_equity:,.0f}, port {ibkr_port}
- Binance: equity=${bnb_eq}, spot=${binance_info.get('spot_total_usd', 'N/A')}, earn=${binance_info.get('earn_total_usd', 'N/A')}
- Alpaca: equity=${alpaca_info.get('equity', 'N/A')} (PAPER)""")
        context_keys.append("brokers")
    except Exception as e:
        parts.append(f"Brokers: erreur {e}")

    # 2. Futures positions (state files)
    try:
        for suffix in ("live", "paper"):
            fp = ROOT / "data" / "state" / f"futures_positions_{suffix}.json"
            if fp.exists():
                pos = json.loads(fp.read_text(encoding="utf-8"))
                if pos:
                    parts.append(f"FUTURES {suffix.upper()}: {json.dumps(pos, indent=1)}")
                else:
                    parts.append(f"FUTURES {suffix.upper()}: aucune position")
        context_keys.append("futures_positions")
    except Exception:
        pass

    # 3. Kill switch states
    try:
        ks_path = ROOT / "data" / "kill_switch_state.json"
        if ks_path.exists():
            ks = json.loads(ks_path.read_text(encoding="utf-8"))
            parts.append(f"KILL SWITCH: active={ks.get('active', False)}, reason={ks.get('activation_reason', 'N/A')}")

        cks_path = ROOT / "data" / "crypto_kill_switch_state.json"
        if cks_path.exists():
            cks = json.loads(cks_path.read_text(encoding="utf-8"))
            parts.append(f"CRYPTO KILL SWITCH: active={cks.get('active', False)}")
        context_keys.append("kill_switch")
    except Exception:
        pass

    # 4. Recent events (trades, signals, errors)
    try:
        events_path = ROOT / "logs" / "events.jsonl"
        if events_path.exists():
            with open(events_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            recent = lines[-30:]  # last 30 events
            events_text = []
            for line in reversed(recent):
                try:
                    ev = json.loads(line.strip())
                    ts = ev.get("timestamp", "")[:16]
                    action = ev.get("action", "")
                    strat = ev.get("strategy", "")
                    details = ev.get("details", {})
                    # Filter interesting events
                    if action in ("futures_trade", "signal", "kill_switch", "error",
                                  "cycle_start", "cycle_end", "trade", "order"):
                        summary = f"{ts} {action}"
                        if strat:
                            summary += f" [{strat}]"
                        if "fill_price" in details:
                            summary += f" {details.get('side','')} {details.get('symbol','')} @ {details.get('fill_price','')}"
                        elif "equity" in details:
                            summary += f" eq={details['equity']}"
                        events_text.append(summary)
                except Exception:
                    continue
            if events_text:
                parts.append("EVENEMENTS RECENTS:\n" + "\n".join(events_text[:15]))
                context_keys.append("events")
    except Exception:
        pass

    # 5. Recent worker signals (from log)
    try:
        log_path = ROOT / "logs" / "worker" / "worker.log"
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                f.seek(max(0, f.seek(0, 2) - 50000))  # last 50KB
                lines = f.readlines()
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            signal_lines = []
            for line in reversed(lines):
                if today not in line:
                    continue
                if any(kw in line for kw in ["SIGNAL", "signal", "SELL", "BUY", "pas de signal",
                                              "FUTURES LIVE:", "FUTURES PAPER:", "DISABLED"]):
                    # Clean the line for display
                    clean = line.strip()
                    if len(clean) > 120:
                        clean = clean[:120] + "..."
                    signal_lines.append(clean)
                if len(signal_lines) >= 20:
                    break
            if signal_lines:
                parts.append("SIGNAUX AUJOURD'HUI:\n" + "\n".join(reversed(signal_lines[-15:])))
                context_keys.append("signals")
    except Exception:
        pass

    # 6. Active strategies list (updated 15 avril 2026 post-audit)
    try:
        parts.append("""STRATEGIES FUTURES ACTIVES (15 avril 2026):
LIVE IBKR (futures_live port 4002):
- EU Gap Open (ESTX50 gap > 1% revert)
- Sector Rotation EU (DAX/CAC40 weekly)
- Gold-Equity Divergence (MGC vs MES)
- Commodity Seasonality (MCL, MGC)
- MES/MNQ Pairs (z-score stat arb)
- MIB/ESTX50 Spread (relative value)

DISABLED (negative backtests):
- MES Trend, MES Trend+MR, 3-Day Stretch (negative or redundant)
- Overnight MES (Sharpe 0.07 after 60-combo sweep, WF OOS -0.68)
- Overnight MNQ, TSMOM (catastrophic backtest)

PAPER ONLY (enabled 15 avril to collect real data):
- MES Trend, MES Trend+MR, 3-Day Stretch, Overnight MNQ, TSMOM, M2K ORB,
  MCL Brent Lag, MGC VIX Hedge, VIX Mean Reversion

IMPORTANT: anciens claims "Sharpe 3.85" etc dans les docstrings etaient
FAUX (jamais re-verifies). Real Sharpe Overnight MES prod = 0.07.""")
        context_keys.append("strategies")
    except Exception:
        pass

    # 7. Risk limits
    try:
        import yaml
        limits_path = ROOT / "config" / "limits_live.yaml"
        if limits_path.exists():
            limits = yaml.safe_load(limits_path.read_text(encoding="utf-8"))
            cb = limits.get("circuit_breakers", {})
            parts.append(f"""LIMITES RISQUE:
- Daily loss: -{cb.get('daily_loss_pct', 0)*100:.0f}% | Weekly: -{cb.get('weekly_loss_pct', 0)*100:.0f}%
- Max contracts: {limits.get('futures_limits', {}).get('max_contracts_per_symbol', 2)}
- Allowed: {limits.get('futures_limits', {}).get('allowed_contracts', [])}""")
            context_keys.append("risk_limits")
    except Exception:
        pass

    # 8. Crypto strategies status
    try:
        log_path = ROOT / "logs" / "worker" / "worker.log"
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                f.seek(max(0, f.seek(0, 2) - 30000))
                lines = f.readlines()
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            crypto_signals = []
            for line in reversed(lines):
                if today not in line:
                    continue
                if "STRAT-" in line and ("signal" in line.lower() or "SIGNAL" in line):
                    clean = line.strip()
                    if len(clean) > 100:
                        clean = clean[:100] + "..."
                    crypto_signals.append(clean)
                if len(crypto_signals) >= 15:
                    break
            if crypto_signals:
                parts.append("CRYPTO (dernier cycle):\n" + "\n".join(reversed(crypto_signals[-10:])))
                context_keys.append("crypto")
    except Exception:
        pass

    return "\n\n".join(parts), context_keys


SYSTEM_PROMPT = """Tu es l'assistant IA du dashboard de trading de Marc. Tu as acces complet aux donnees LIVE.

{context}

REGLES:
- Reponds en francais, court et direct
- Utilise les donnees live ci-dessus pour repondre aux questions
- Tu peux analyser les trades, positions, signaux, risques, equity, strategies
- Si on te demande un calcul (PnL, drawdown, etc), fais-le avec les donnees disponibles
- Ne fabrique pas de donnees. Si tu ne sais pas, dis-le
- Pour les montants: EUR pour IBKR, $ pour Binance/Alpaca
- Tu peux expliquer les strategies, le risk management, les signaux
- Si on te demande d'executer un trade, refuse (read-only)

CONTEXTE SYSTEME:
- 3 strats futures actives: Overnight MES, Trend+MR, TSMOM MES
- 11 strats crypto: Binance (spot + earn + margin isole)
- Capital: IBKR ~EUR 10K + Binance ~$8.7K
- Worker 24/7 sur Hetzner VPS
- Futures cycle: 16h CET (14h UTC)
- Crypto cycle: toutes les 15 min
- Risk cycle: toutes les 5 min (software SL/TP)"""


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request = None):
    """Chat with the trading assistant — full data access."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ChatResponse(response="ANTHROPIC_API_KEY non configure sur le serveur.")

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

    context, context_keys = _get_live_context()
    system = SYSTEM_PROMPT.format(context=context)

    messages = []
    for h in req.history[-10:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": req.message})

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=system,
            messages=messages,
        )
        text = response.content[0].text
        return ChatResponse(response=text, context_used=context_keys)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return ChatResponse(response=f"Erreur API: {str(e)[:200]}")
