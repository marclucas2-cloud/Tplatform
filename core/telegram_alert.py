"""
Module d'alerting Telegram pour le trading platform.

Configure via variables d'environnement :
  TELEGRAM_BOT_TOKEN : token du bot Telegram
  TELEGRAM_CHAT_ID   : ID du chat/groupe pour les alertes

Usage :
    from core.telegram_alert import send_alert, send_heartbeat
    send_alert("CIRCUIT-BREAKER DECLENCHE", level="critical")
    send_heartbeat(equity=100000, n_positions=3, pnl=150)
"""
import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Envoie un message via l'API Telegram Bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram non configure (TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return True
            else:
                logger.warning(f"Telegram API error: {result}")
                return False
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def send_alert(message: str, level: str = "info") -> bool:
    """
    Envoie une alerte Telegram.
    Levels : info, warning, critical
    """
    emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "ℹ️")
    text = f"{emoji} <b>Trading Platform</b>\n\n{message}"
    return _send_message(text)


def send_heartbeat(equity: float, n_positions: int, pnl: float,
                   n_strategies: int = 0) -> bool:
    """Envoie un heartbeat avec l'etat du portefeuille."""
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    text = (
        f"💓 <b>Heartbeat OK</b>\n\n"
        f"Equity: ${equity:,.2f}\n"
        f"Positions: {n_positions}\n"
        f"P&L: {pnl_emoji} ${pnl:+,.2f}\n"
        f"Strategies actives: {n_strategies}"
    )
    return _send_message(text)


def send_trade_alert(strategy: str, direction: str, ticker: str,
                     qty: int, price: float, stop_loss: float = None,
                     take_profit: float = None) -> bool:
    """Alerte sur un trade execute."""
    emoji = "🟢" if direction in ("LONG", "BUY") else "🔴"
    sl_info = f"\nSL: ${stop_loss:.2f}" if stop_loss else ""
    tp_info = f"\nTP: ${take_profit:.2f}" if take_profit else ""
    text = (
        f"{emoji} <b>Trade Execute</b>\n\n"
        f"Strategie: {strategy}\n"
        f"Direction: {direction}\n"
        f"Ticker: {ticker}\n"
        f"Qty: {qty} @ ${price:.2f}"
        f"{sl_info}{tp_info}"
    )
    return _send_message(text)


def send_circuit_breaker(equity: float, daily_start: float, dd_pct: float) -> bool:
    """Alerte CRITIQUE circuit-breaker."""
    text = (
        f"🚨🚨🚨 <b>CIRCUIT-BREAKER DECLENCHE</b> 🚨🚨🚨\n\n"
        f"Drawdown journalier: {dd_pct*100:.1f}%\n"
        f"Equity: ${equity:,.2f}\n"
        f"Capital debut de journee: ${daily_start:,.2f}\n\n"
        f"<b>TOUS LES ORDRES SUSPENDUS</b>"
    )
    return _send_message(text)


def send_kill_switch(strategy: str, rolling_pnl: float, threshold: float) -> bool:
    """Alerte kill switch par strategie."""
    text = (
        f"⛔ <b>Kill Switch — {strategy}</b>\n\n"
        f"PnL rolling 5j: ${rolling_pnl:,.2f}\n"
        f"Seuil: ${threshold:,.2f}\n\n"
        f"Strategie <b>DESACTIVEE</b> automatiquement."
    )
    return _send_message(text)


def send_position_not_closed(symbols: list) -> bool:
    """Alerte position non fermee apres cloture."""
    text = (
        f"⚠️ <b>Positions non fermees apres 16:00 ET</b>\n\n"
        f"Symboles: {', '.join(symbols)}\n\n"
        f"Action manuelle requise!"
    )
    return _send_message(text)
